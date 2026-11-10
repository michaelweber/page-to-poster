"""Build a project's 3D model from layout.json and props.py (run inside Blender; `p2p.py build` does this for you).

blender -b --factory-startup --python-exit-code 1 --python mapkit/blender/build.py -- PROJECT [--preview]
Writes PROJECT/build/model.blend and PROJECT/build/anchors.json. --preview also writes build/plan_preview.png and
build/iso_preview.png (Workbench engine, a few seconds) so the result can be checked before any slow render.

What comes from where:
- layout.json: rooms, corridors, vents, blockages, doors and entrances (docs/layout.md). This file builds all of
  that: floors, walls with openings, corridor runs, junctions, ducts, rubble plugs, door leaves, stairs and ramps.
- props.py (optional): one function per room, `room_<id>(r, rng, P)` or `room_<id>(r, rng, P, cut)`, that fills
  the room using mapkit.blender.kit. `r` is the room's Rect, `rng` a seeded random.Random, `P` the collection for
  the room's objects, `cut` a mesh builder whose solids are subtracted from the room's floor (pits, pools).
  An optional `extras(rooms, rng, P)` runs last for anything outside a single room (rooms is {id: Rect}).
- anchors.json records where everything ended up (rooms, links, entrances, named features), in meters, for the
  render and poster steps.
"""
import argparse
import importlib.util
import inspect
import json
import math
import random
import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mapkit import layout as lay  # noqa: E402
from mapkit.blender import kit as k  # noqa: E402

SIDE_OUT = {"N": math.pi / 2, "S": -math.pi / 2, "E": 0.0, "W": math.pi}   # heading that points out of the room


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="build.py")
    ap.add_argument("project")
    ap.add_argument("--preview", action="store_true")
    return ap.parse_args(argv)


def load_props(project):
    path = project / "props.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("props", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================================== entrances
def wall_frame(room, side, at):
    """Frame with its origin on the room edge at `at` along wall `side`: x points out of the room, y along the
    wall, z up. The wall itself occupies x 0..WALL_T."""
    x, y = {"N": (at, room.maxy), "S": (at, room.miny), "E": (room.maxx, at), "W": (room.minx, at)}[side]
    return k.T(x, y, 0.0, rz=SIDE_OUT[side])


def world(M, x, y, z):
    p = M @ k.Vector((x, y, z))
    return [round(p.x, 3), round(p.y, 3), round(p.z, 3)]


def side_walls(mb, mat, M, hw, x0, x1, z0, z1, end=False):
    """Walls either side of a passage of half-width hw running out from a room, and optionally across its end."""
    W = k.WALL_T
    k.boxmm(mb, mat, (x0, -hw - W, z0), (x1, -hw, z1), M)
    k.boxmm(mb, mat, (x0, hw, z0), (x1, hw + W, z1), M)
    if end:
        k.boxmm(mb, mat, (x1, -hw - W, z0), (x1 + W, hw + W, z1), M)


def entrance_stairs(room, e, M, name):
    w, n = float(e.get("width", 2.6)), int(e.get("steps", 16))
    rise, run, hw = 0.24, 0.28, float(e.get("width", 2.6)) / 2
    down = e.get("dir", "up") == "down"
    top, foot = n * rise, 0.65
    L = foot + n * run + 0.6
    room.openings.append((e["side"], e["at"], w, -k.SLAB_T - 0.01, float(e.get("height", 3.2))))
    well = k.MB(f"{name}_well")
    steps = k.MB(f"{name}_steps")
    if down:
        base = -top - k.SLAB_T
        k.boxmm(well, room.floor, (0, -hw, -k.SLAB_T), (foot, hw, 0.0), M)
        k.boxmm(well, room.floor, (foot + n * run, -hw, base), (L, hw, -top), M)
        for i in range(n):
            k.boxmm(steps, room.floor, (foot + i * run, -hw, base), (foot + (i + 1) * run, hw, -(i + 1) * rise), M)
        side_walls(well, room.wall, M, hw, k.WALL_T, L, base, k.ROCK_TOP, end=True)
    else:
        k.boxmm(well, room.floor, (0, -hw, -k.SLAB_T), (L, hw, 0.0), M)
        k.stairs(steps, room.floor, foot, 0.0, 0.0, w, n, rise, run, M=M)
        side_walls(well, room.wall, M, hw, k.WALL_T, L, -k.SLAB_T, top + 3.0, end=True)
        for s in (-1, 1):
            k.sweep(steps, "rust_metal", [(foot + 0.05, s * (hw - 0.1), 1.0), (foot + n * run, s * (hw - 0.1), 1.0 + top)],
                    0.03, 6, M)
    well.obj(k.C["entrances"])["cap_as_wall"] = True
    steps.obj(k.C["entrances"], nocap=True)
    return world(M, foot + n * run / 2, 0, (-top if down else top) / 2)


def entrance_ramp(room, e, M, name):
    w, Lr, rise = float(e.get("width", 4.0)), float(e.get("length", 10.0)), float(e.get("rise", 3.0))
    hw, down = w / 2, e.get("dir", "up") == "down"
    x0, x1 = k.WALL_T, k.WALL_T + Lr
    room.openings.append((e["side"], e["at"], w, -k.SLAB_T - 0.01, float(e.get("height", 4.0))))
    ramp = k.MB(f"{name}_ramp")
    walls = k.MB(f"{name}_walls")
    k.boxmm(walls, room.floor, (0, -hw, -k.SLAB_T), (x0, hw, 0.0), M)
    if down:
        prof = [(x0, 0.0), (x1, -rise), (x1, -rise - k.SLAB_T), (x0, -rise - k.SLAB_T)]
        side_walls(walls, room.wall, M, hw, x0, x1, -rise - k.SLAB_T, k.ROCK_TOP, end=bool(e.get("sealed")))
    else:
        prof = [(x0, 0.0), (x1, 0.0), (x1, rise)]
        side_walls(walls, room.wall, M, hw, x0, x1, -k.SLAB_T, rise + 3.0)
        if e.get("sealed"):   # a slab across the top of the ramp, with a warning band
            k.boxmm(walls, "painted_metal", (x1 - 0.3, -hw - k.WALL_T, rise - 0.4), (x1 + 0.3, hw + k.WALL_T, rise + 3.0), M)
            k.boxmm(walls, "hazard", (x1 - 0.34, -hw + 0.1, rise), (x1 - 0.3, hw - 0.1, rise + 1.0), M)
    k.loft(ramp, room.floor, [[(x, -hw, z) for x, z in prof], [(x, hw, z) for x, z in prof]], M)
    k.boxmm(ramp, "hazard", (x0 - 0.45, -hw, 0.0), (x0, hw, 0.02), M)
    ramp.obj(k.C["entrances"], nocap=True)
    walls.obj(k.C["entrances"])["cap_as_wall"] = True
    return world(M, x0 + Lr / 2, 0, (-rise if down else rise) / 2)


def entrance_tunnel(room, e, M, name, rng):
    w, L = float(e.get("width", min(k.CW, 2.4))), float(e.get("length", 8.0))
    hw, rough = w / 2, bool(e.get("rough"))
    floor, wall = ("dirt", "dirt") if rough else (room.floor, room.wall)
    room.openings.append((e["side"], e["at"], w, -k.SLAB_T - 0.01, float(e.get("height", 2.8))))
    mb = k.MB(name)
    k.boxmm(mb, floor, (0, -hw, -k.SLAB_T), (L, hw, 0.0), M)
    side_walls(mb, wall, M, hw, k.WALL_T, L, -k.SLAB_T, k.ROCK_TOP)
    if rough:   # hand-dug: lumpy walls and timber shoring
        x = k.WALL_T + 0.5
        while x < L - 0.3:
            for s in (-1, 1):
                k.blob(mb, "rubble", (x, s * (hw + 0.05), rng.uniform(0.3, 2.0)), (0.5, 0.3, 0.4), rng, 0.3, M=M)
            x += 0.8
        shoring = k.MB(f"{name}_shoring")
        x = k.WALL_T + 0.8
        while x < L - 0.3:
            for s in (-1, 1):
                k.boxmm(shoring, "wood", (x - 0.08, s * (hw - 0.15) - 0.08, 0.0), (x + 0.08, s * (hw - 0.15) + 0.08, 2.5), M)
            k.boxmm(shoring, "wood", (x - 0.1, -hw + 0.02, 2.5), (x + 0.1, hw - 0.02, 2.7), M)
            x += 2.0
        shoring.obj(k.C["entrances"])
    if e.get("blocked"):   # a collapse part-way along, like a blocked link
        q, far = M @ k.Vector((L * 0.55, 0, 0)), M @ k.Vector((L, 0, 0))
        tan = (far - q).normalized()
        k.blockage((q.x, q.y), (tan.x, tan.y), f"{name}_blockage", rng, width=w, mat=e.get("fill", "rubble"),
                   slab=wall, bars=wall.startswith("concrete"))
        k.ANCH["blockages"].append({"a": room.id, "b": None, "at": [round(q.x, 3), round(q.y, 3), 0.0]})
    mb.obj(k.C["entrances"])["cap_as_wall"] = True
    return world(M, L, 0, 0)


def entrance_door(room, e, M, name):
    w, h = float(e.get("width", 1.6)), float(e.get("height", 2.4))
    hw = w / 2
    room.openings.append((e["side"], e["at"], w, -k.SLAB_T - 0.01, h))
    mb = k.MB(name)
    k.boxmm(mb, room.floor, (0, -hw, -k.SLAB_T), (k.WALL_T, hw, 0.0), M)
    k.boxmm(mb, room.floor, (k.WALL_T, -hw - 0.5, -k.SLAB_T), (k.WALL_T + 1.8, hw + 0.5, 0.0), M)
    k.boxmm(mb, "hazard", (k.WALL_T, -hw, 0.0), (k.WALL_T + 0.4, hw, 0.02), M)
    for s in (-1, 1):   # jambs proud of the outer wall face
        k.boxmm(mb, "painted_metal", (k.WALL_T, s * hw - 0.06, 0.0), (k.WALL_T + 0.06, s * hw + 0.06, h + 0.1), M)
    ajar = 0.0 if e.get("closed") else math.radians(float(e.get("ajar", 55)))
    k.door_leaf(mb, M @ k.T(k.WALL_T / 2, -hw + 0.03, 0, rz=math.pi / 2 - ajar), w - 0.06, h - 0.05, t=0.12)
    mb.obj(k.C["entrances"])
    return world(M, k.WALL_T + 0.9, 0, 0)


def entrance_shaft(room, e, name, cut):
    x, y = e["at"]
    r = float(e.get("radius", 1.2))
    mb = k.MB(name)
    if e.get("dir", "up") == "down":
        depth = float(e.get("depth", 6.0))
        k.round_pit(mb, cut, x, y, r, depth, wall=room.wall, floor=room.floor)
        k.ladder(mb, x + r - 0.12, y, -depth, 1.1)
        at = [x, y, 0.0]
    else:
        k.tube(mb, "hazard", (x, y), r - 0.2, r, 0.0, 0.015, 40)
        k.ladder(mb, x + r - 0.3, y, 0.0, room.h)
        if e.get("daylight"):
            k.lamp(f"{name}_daylight", "SPOT", (x, y, room.h - 0.5), 4000.0 * room.h, (1.0, 0.97, 0.9), spot_deg=24.0,
                   radius=r, map_dim=0.06)
        at = [x, y, min(room.h, 3.0)]
    mb.obj(k.C["entrances"], nocap=True)
    return at


def build_entrance(rooms, cutters, e, i, rng):
    room, kind = rooms[e["room"]], e["kind"]
    name = f"entrance_{i}_{kind}"
    if kind == "shaft":
        at = entrance_shaft(room, e, name, cutters[room.id])
    else:
        M = wall_frame(room, e["side"], float(e["at"]))
        at = {"stairs": entrance_stairs, "ramp": entrance_ramp, "door": entrance_door}[kind](room, e, M, name) \
            if kind != "tunnel" else entrance_tunnel(room, e, M, name, rng)
    k.ANCH["entrances"].append({"room": room.id, "kind": kind, "at": at, "label": e.get("label"),
                                "note": e.get("note")})


# ============================================================================================== scene build
def build(layout, props):
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    C = k.C
    k.coll("structure")
    k.coll("walls", C["structure"])
    for n in ("vents", "blockages"):
        k.coll(n)
    for d in layout["rooms"]:
        k.coll(f"props_{d['id']}")
    for n in ("props_corridors", "decals", "lights", "entrances", "ceilings"):
        k.coll(n)
    C["ceilings"].hide_render = True
    floor0, wall0 = layout.get("floor", "concrete_floor"), layout.get("wall", "concrete_wall")

    rooms = {}
    for d in layout["rooms"]:
        h = float(d.get("height", k.CH))
        rooms[d["id"]] = k.Rect(d["id"], d["name"], d["min"][0], d["min"][1], d["max"][0], d["max"][1], h,
                                max(h, k.ROOM_MIN_TOP), floor=d.get("floor", floor0), wall=d.get("wall", wall0))
    J = {name: tuple(p) for name, p in layout.get("junctions", {}).items()}
    links = layout.get("links", [])
    for L in links:   # a path that names a junction but meets it elsewhere gets its own junction there
        if L["kind"] == "vent":
            continue
        for nm, p in ((L["a"], L["path"][0]), (L["b"], L["path"][-1])):
            if nm in J and math.dist(p, J[nm]) > 0.3:
                key = f"{nm}b"
                if key not in J:
                    J[key] = tuple(p)
                    k.ISSUES.append(f"link {L['a']}-{L['b']}: path ends at {tuple(p)} but names junction {nm} at "
                                    f"{J[nm]}; treated {tuple(p)} as an extra junction '{key}'")
    juncs = {name: k.Rect(name, name, x - k.JUNC_HALF, y - k.JUNC_HALF, x + k.JUNC_HALF, y + k.JUNC_HALF, k.CH,
                          k.ROCK_TOP + 0.05, junction=True, floor=floor0, wall=wall0) for name, (x, y) in J.items()}

    def locate(p):
        for name, q in J.items():
            if math.dist(p, q) < 0.3:
                return juncs[name]
        for rm in rooms.values():
            if rm.on_boundary(p):
                return rm
        raise SystemExit(f"build: path end {tuple(p)} touches no room edge and no junction")

    # ---- corridors: split links at junction vertices, lay floor and side walls, cut the wall openings
    pieces = []
    for L in links:
        if L["kind"] == "vent":
            continue
        path = [tuple(p) for p in L["path"]]
        cuts = [0] + [i for i in range(1, len(path) - 1) if any(math.dist(path[i], q) < 0.3 for q in J.values())] \
            + [len(path) - 1]
        for a, b in zip(cuts, cuts[1:]):
            pieces.append({"link": L, "path": path[a:b + 1], "first": a == 0, "last": b == len(path) - 1})
    floors, cwalls, doors = k.MB("floors_corridors"), k.MB("walls_corridors"), k.MB("doors")
    door_w, door_h = float(layout.get("door_width", min(k.CW, 1.6))), float(layout.get("door_height", 2.4))
    for pc in pieces:
        L, path = pc["link"], pc["path"]
        w = float(L.get("width", k.CW))
        A, B = locate(path[0]), locate(path[-1])
        fp = path
        if A.junction:
            fp = k.trim_start(fp, k.JUNC_HALF)
        if B.junction:
            fp = k.trim_end(fp, k.JUNC_HALF)
        k.strip(floors, floor0, fp, -w / 2, w / 2, -k.SLAB_T, 0.0)
        if k.path_len(fp) > 2 * k.WALL_T + 0.05:
            wp = k.trim_end(k.trim_start(fp, k.WALL_T), k.WALL_T)
            k.strip(cwalls, wall0, wp, w / 2, w / 2 + k.WALL_T, -k.SLAB_T, k.ROCK_TOP)
            k.strip(cwalls, wall0, wp, -w / 2 - k.WALL_T, -w / 2, -k.SLAB_T, k.ROCK_TOP)
        for end, tgt, nxt, tag in ((path[0], A, path[1], "a" if pc["first"] else None),
                                   (path[-1], B, path[-2], "b" if pc["last"] else None)):
            if tgt.junction:
                tgt.add_opening(end, w, -k.SLAB_T - 0.01, k.CH, toward=nxt)
            elif tag in L.get("doors", ()):
                ow = min(door_w, w)
                tgt.add_opening(end, ow, -k.SLAB_T - 0.01, door_h)
                ux, uy = (nxt[0] - end[0]) / k.seglen(end, nxt), (nxt[1] - end[1]) / k.seglen(end, nxt)
                hx, hy = end[0] + ux * k.WALL_T / 2 + uy * (ow / 2 - 0.03), end[1] + uy * k.WALL_T / 2 - ux * (ow / 2 - 0.03)
                secret = bool(L.get("secret"))
                ajar = 0.0 if secret or L.get("closed") else math.radians(40)
                k.door_leaf(doors, k.T(hx, hy, 0, rz=math.atan2(ux, -uy) - ajar), ow - 0.06, door_h - 0.05,
                            t=0.3 if secret else 0.1, mat=tgt.wall if secret else "painted_metal", handle=not secret)
            else:
                tgt.add_opening(end, w, -k.SLAB_T - 0.01, k.CH)
        pc["floor_path"] = fp
    floors.obj(C["structure"])
    cwalls.obj(C["walls"])
    doors.obj(C["props_corridors"])

    # ---- vents: a duct between two wall grilles, at height VZ unless the link gives its own "z"
    vm = k.MB("vent_ducts")
    for L in links:
        if L["kind"] != "vent":
            continue
        path = [tuple(p) for p in L["path"]]
        vz = float(L.get("z", k.VZ))
        ext = k.trim_end(k.trim_start(path, -0.12), -0.12)
        k.strip(vm, "rust_metal", ext, -k.VW / 2, k.VW / 2, vz - k.VW / 2, vz + k.VW / 2)
        for end, nxt, e2 in ((path[0], path[1], ext[0]), (path[-1], path[-2], ext[-1])):
            locate(end).add_opening(end, k.VW, vz - k.VW / 2, vz + k.VW / 2)
            dx, dy = end[0] - nxt[0], end[1] - nxt[1]
            Ld = math.hypot(dx, dy)
            M = k.T(e2[0] + dx / Ld * 0.03, e2[1] + dy / Ld * 0.03, vz, rz=math.atan2(dy, dx))
            k.box(vm, "grating", (0, 0, 0), (0.06, k.VW + 0.25, k.VW + 0.25), M)
            for u in (-0.3, -0.1, 0.1, 0.3):
                k.box(vm, "rust_metal", (0.04, u * k.VW / 0.9, 0), (0.03, 0.04, k.VW + 0.2), M)
        k.ANCH["vents"].append({"a": L["a"], "b": L["b"], "z": vz, "path": [list(p) for p in path],
                                "mid": list(k.path_at(path, k.path_len(path) / 2)[0])})
    vob = vm.obj(C["vents"])
    if vob is not None:
        vob["nocap"] = True

    # ---- entrances, then each room's props (both may cut the floor or open a wall)
    cutters = {rid: k.MB(f"cut_{rid}") for rid in rooms}
    rng_e = random.Random(k.SEED + 5)
    for i, e in enumerate(layout.get("entrances", []), 1):
        build_entrance(rooms, cutters, e, i, rng_e)
    for n_room, (rid, rm) in enumerate(rooms.items()):
        k.ANCH["rooms"][str(rid)] = {"name": rm.name, "center": [round(rm.cx, 3), round(rm.cy, 3), 0.0],
                                     "rect": [rm.minx, rm.miny, rm.maxx, rm.maxy],
                                     "bounds": [rm.minx - 2, rm.miny - 2, rm.maxx + 2, rm.maxy + 2], "height": rm.h}
        fn = getattr(props, f"room_{rid}", None) if props else None
        if fn is None:
            continue
        seed = k.SEED * 100 + (rid if isinstance(rid, int) else 1000 + n_room)   # stable when rooms are added
        args = [rm, random.Random(seed), C[f"props_{rid}"], cutters[rid]]
        fn(*args[:len(inspect.signature(fn).parameters)])

    # ---- floors (with pit holes) and walls
    for rid, rm in rooms.items():
        f = k.MB(f"floor_room_{rid}")
        k.boxmm(f, rm.floor, (rm.minx, rm.miny, -k.SLAB_T), (rm.maxx, rm.maxy, 0.0))
        fob = f.obj(C["structure"])
        if not cutters[rid].empty():
            k.cut_holes(fob, cutters[rid])
        w = k.MB(f"walls_room_{rid}")
        k.build_walls(rm, w)
        w.obj(C["walls"])
    for name, jr in juncs.items():
        f = k.MB(f"floor_{name}")
        k.boxmm(f, jr.floor, (jr.minx, jr.miny, -k.SLAB_T), (jr.maxx, jr.maxy, 0.0))
        f.obj(C["structure"])
        w = k.MB(f"walls_{name}")
        k.build_walls(jr, w)
        w.obj(C["walls"])
        k.ANCH["junctions"][name] = [J[name][0], J[name][1], 0.0]

    # ---- link anchors, blockages, corridor dressing
    for L in links:
        path = [tuple(p) for p in L["path"]]
        mid = k.path_at(path, k.path_len(path) / 2)[0]
        k.ANCH["links"].append({"a": L["a"], "b": L["b"], "kind": L["kind"], "mid": [round(c, 3) for c in mid],
                                "path": [list(p) for p in path], "doors": list(L.get("doors", ())),
                                "secret": bool(L.get("secret"))})
        if L["kind"] == "blocked":
            want = L.get("block_at", mid)
            d, q, tan, _ = k.project(path, want)
            if d > 0.05:
                k.ISSUES.append(f"link {L['a']}-{L['b']}: block_at {want} lies {d:.2f} m off the corridor path; "
                                f"snapped to {tuple(round(c, 2) for c in q)}")
            k.blockage(q, tan, f"blockage_{L['a']}_{L['b']}", random.Random(k.SEED + 7 * len(k.ANCH['blockages'])),
                       width=float(L.get("width", k.CW)), mat=L.get("fill", "rubble"), slab=wall0,
                       bars=wall0.startswith("concrete"))
            k.ANCH["blockages"].append({"a": L["a"], "b": L["b"], "at": [round(q[0], 3), round(q[1], 3), 0.0]})
    stains = layout.get("corridor_decals", ["oil_stain", "crack", "crack", "rust_streak"])
    rng_c = random.Random(k.SEED)
    for pc in pieces if stains else ():
        fp = pc["floor_path"]
        Lp = k.path_len(fp)
        for _ in range(max(1, int(Lp / 7)) if Lp > 1.5 else 0):
            (x, y), (tx, ty) = k.path_at(fp, rng_c.uniform(0.5, Lp - 0.5))
            off = rng_c.uniform(-1.0, 1.0) * float(pc["link"].get("width", k.CW)) / 3.2
            k.decal(rng_c.choice(stains), x - ty * off, y + tx * off, 0.0, rng_c.uniform(1.0, 2.2), rng_c.uniform(0, 6.28))
    if props and hasattr(props, "extras"):
        props.extras(rooms, random.Random(k.SEED + 99), C["props_corridors"])
    for mb in k.BUILDERS:
        if not mb.used and not mb.empty():
            k.ISSUES.append(f"props.py: mesh builder {mb.name!r} has geometry but never became an object; finish it "
                            "with mb.obj(P)")
    k.ANCH["issues"] = list(k.ISSUES)
    print(f"[mapkit] geometry built in {time.time() - t0:.1f}s")


def main():
    args = parse_args()
    project = Path(args.project).resolve()
    layout = json.loads((project / "layout.json").read_text(encoding="utf-8"))
    errors = [p for p in lay.validate(layout) if p.level == "error"]
    if errors:
        raise SystemExit("build: layout.json has errors (run `p2p.py check`):\n  " + "\n  ".join(p.text for p in errors))
    t0 = time.time()
    k.init(layout, project)
    build(layout, load_props(project))
    k.lift_ground_contacts()
    out = project / "build"
    out.mkdir(exist_ok=True)
    meshes, tris, per = k.stats()
    (out / "anchors.json").write_text(json.dumps(k.ANCH, indent=1), encoding="utf-8")
    bpy.context.preferences.filepaths.save_version = 0   # no .blend1 backups next to the outputs
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "model.blend"), compress=True)
    print(f"[mapkit] saved {out / 'model.blend'} ({meshes} mesh objects, {tris} triangles) in {time.time() - t0:.1f}s")
    for i in k.ISSUES:
        print("[mapkit] issue:", i)
    if args.preview:
        cfg_path = project / "poster.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        k.render_previews(out, float((cfg.get("view") or {}).get("azimuth", -45)))
        print(f"[mapkit] previews written to {out}")


if __name__ == "__main__":
    main()
