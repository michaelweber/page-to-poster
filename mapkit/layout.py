"""layout.json: sizes, validation and a 2D check drawing (plain Python; the drawing needs Pillow).

`python p2p.py check PROJECT` prints every problem and writes PROJECT/build/layout_check.png, a to-scale plan to
compare against the adventure's own map before anything is built. docs/layout.md describes the file format.
"""
import math
import re
from dataclasses import dataclass
from pathlib import Path

WALL_T = 0.6
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
LINK_KINDS = ("open", "blocked", "vent")
ENTRANCE_KINDS = ("stairs", "ramp", "tunnel", "door", "shaft")
OUTWARD = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}


@dataclass
class Problem:
    level: str   # "error" stops the build; "warning" is worth a look
    text: str


def sizes(layout):
    """(corridor width, corridor height, vent width, vent centre height) with defaults filled in."""
    cw, ch = layout.get("corridor_width", 3.2), layout.get("corridor_height", 3.6)
    width = float(cw["open"] if isinstance(cw, dict) else cw)
    vent = float(cw.get("vent", 0.9) if isinstance(cw, dict) else layout.get("vent_width", 0.9))
    height = float(ch["open"] if isinstance(ch, dict) else ch)
    return width, height, vent, float(layout.get("vent_z", height - 1.0))


def _on_edge(room, p, tol=0.05):
    (x0, y0), (x1, y1) = room["min"], room["max"]
    x, y = p
    in_x, in_y = x0 - tol <= x <= x1 + tol, y0 - tol <= y <= y1 + tol
    return (in_x and (abs(y - y0) < tol or abs(y - y1) < tol)) or (in_y and (abs(x - x0) < tol or abs(x - x1) < tol))


def _side_of(room, p):
    (x0, y0), (x1, y1) = room["min"], room["max"]
    d = {"S": abs(p[1] - y0), "N": abs(p[1] - y1), "W": abs(p[0] - x0), "E": abs(p[0] - x1)}
    s = min(d, key=d.get)
    return s, (p[0] if s in "SN" else p[1])


def _span(room, side):
    (x0, y0), (x1, y1) = room["min"], room["max"]
    return (x0, x1) if side in "SN" else (y0, y1)


def _seg_hits_rect(a, b, rect):
    """True when segment a-b enters the open rectangle (x0, y0, x1, y1) (Liang-Barsky clip)."""
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    return t1 - t0 > 1e-6


def _cross(a, b, c, d):
    """Intersection point of segments a-b and c-d if they properly cross, else None."""
    r, s = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if 1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6:
        return a[0] + r[0] * t, a[1] + r[1] * t
    return None


def validate(layout):
    """Every problem found in a layout, errors first."""
    out = []

    def err(text):
        out.append(Problem("error", text))

    def warn(text):
        out.append(Problem("warning", text))

    rooms = layout.get("rooms") or []
    if not rooms:
        return [Problem("error", "layout has no rooms")]
    cw, ch, vw, vz = sizes(layout)
    jh = cw / 2 + WALL_T
    by_id = {}
    for r in rooms:
        rid = r.get("id")
        if not ID_RE.match(str(rid)):
            err(f"room id {rid!r}: use letters, digits and underscores only (props.py names its function room_<id>)")
        if rid in by_id:
            err(f"room id {rid!r} is used twice")
        if any(key not in r for key in ("name", "min", "max")):
            err(f"room {rid!r} needs name, min and max")
            continue
        by_id[rid] = r
        (x0, y0), (x1, y1) = r["min"], r["max"]
        if x1 - x0 < 2.0 or y1 - y0 < 2.0:
            err(f"room {rid} is {x1 - x0:g} x {y1 - y0:g} m; min must be the south-west corner, max the north-east, "
                "and a room needs at least 2 m each way")
        if float(r.get("height", ch)) < 2.2:
            warn(f"room {rid} is only {r['height']} m high")
    ids = list(by_id)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (ax0, ay0), (ax1, ay1) = by_id[a]["min"], by_id[a]["max"]
            (bx0, by0), (bx1, by1) = by_id[b]["min"], by_id[b]["max"]
            dx, dy = max(0.0, ax0 - bx1, bx0 - ax1), max(0.0, ay0 - by1, by0 - ay1)
            if dx < WALL_T - 1e-6 and dy < WALL_T - 1e-6:
                err(f"rooms {a} and {b} are {max(dx, dy):.2f} m apart: walls are {WALL_T} m thick, so leave "
                    f"{WALL_T} m (a shared wall) or more between room rectangles")
    junctions = {name: tuple(p) for name, p in (layout.get("junctions") or {}).items()}
    for name, (x, y) in junctions.items():
        if name in by_id or str(name) in map(str, by_id):
            err(f"junction {name!r} has the same name as a room")
        for rid, r in by_id.items():
            (x0, y0), (x1, y1) = r["min"], r["max"]
            if x0 - jh - WALL_T < x < x1 + jh + WALL_T and y0 - jh - WALL_T < y < y1 + jh + WALL_T:
                err(f"junction {name} at ({x:g}, {y:g}) is inside or within {jh + WALL_T:g} m of room {rid}")

    links = layout.get("links") or []
    used = {name: 0 for name in junctions}
    connected = {rid: set() for rid in by_id}
    for n, L in enumerate(links):
        tag = f"link {n} ({L.get('a')}-{L.get('b')})"
        kind, path = L.get("kind"), [tuple(p) for p in L.get("path", [])]
        if kind not in LINK_KINDS:
            err(f"{tag}: kind must be one of {', '.join(LINK_KINDS)}")
            continue
        if len(path) < 2:
            err(f"{tag}: path needs at least two points")
            continue
        vent = kind == "vent"
        w = vw if vent else float(L.get("width", cw))
        ends_ok = True
        for which, name, p, nxt in (("a", L.get("a"), path[0], path[1]), ("b", L.get("b"), path[-1], path[-2])):
            seg = math.dist(p, nxt)
            if name in junctions:
                used[name] += 1
                if vent:
                    err(f"{tag}: a vent must run from room to room, not to junction {name}")
                if math.dist(p, junctions[name]) > 0.3:
                    warn(f"{tag}: end {which} is at {p}, but junction {name} is at {junctions[name]}")
                if seg < jh + WALL_T + 0.1 and len(path) > 1:
                    err(f"{tag}: the segment at junction {name} is {seg:.2f} m; it must be at least "
                        f"{jh + WALL_T + 0.1:.1f} m")
            elif name in by_id:
                room = by_id[name]
                if not _on_edge(room, p):
                    err(f"{tag}: end {which} at {p} is not on the edge of room {name} "
                        f"(x {room['min'][0]:g}..{room['max'][0]:g}, y {room['min'][1]:g}..{room['max'][1]:g})")
                    ends_ok = False
                    continue
                side, along = _side_of(room, p)
                lo, hi = _span(room, side)
                if not lo + w / 2 + 0.05 <= along <= hi - w / 2 - 0.05:
                    err(f"{tag}: the {w:g} m opening at {p} runs past the corner of room {name}'s {side} wall "
                        f"(keep it within {lo + w / 2 + 0.05:g}..{hi - w / 2 - 0.05:g})")
                ox, oy = OUTWARD[side]
                if ((nxt[0] - p[0]) * ox + (nxt[1] - p[1]) * oy) / max(seg, 1e-9) < 0.97:
                    err(f"{tag}: the path must leave room {name} at a right angle to its {side} wall")
                if len(path) > 2 and seg < WALL_T + 0.1:
                    err(f"{tag}: the first segment out of room {name} is {seg:.2f} m; make it at least "
                        f"{WALL_T + 0.1:.1f} m before the first bend")
                elif seg < WALL_T - 1e-6:
                    err(f"{tag}: path is {seg:.2f} m long; rooms are at least {WALL_T} m apart")
                connected[name].add(L.get("b") if which == "a" else L.get("a"))
            else:
                err(f"{tag}: {which}={name!r} is neither a room id nor a junction name")
                ends_ok = False
        for i in range(1, len(path) - 1):   # a path may pass through junctions on the way
            for name, q in junctions.items():
                if math.dist(path[i], q) < 0.3:
                    used[name] += 1
                    for a, b in ((path[i - 1], path[i]), (path[i], path[i + 1])):
                        if math.dist(a, b) < jh + WALL_T + 0.1:
                            err(f"{tag}: the segment at junction {name} is {math.dist(a, b):.2f} m; it must be at "
                                f"least {jh + WALL_T + 0.1:.1f} m")
        if not vent and ends_ok:
            for rid, room in by_id.items():
                (x0, y0), (x1, y1) = room["min"], room["max"]
                grow = WALL_T + w / 2 - 0.05
                rect = (x0 - grow, y0 - grow, x1 + grow, y1 + grow)
                segs = list(zip(path, path[1:]))
                if rid == L.get("a"):
                    segs = segs[1:]
                if rid == L.get("b"):
                    segs = segs[:-1]
                if any(_seg_hits_rect(a, b, rect) for a, b in segs):
                    err(f"{tag}: the corridor runs through or scrapes room {rid}; route it at least "
                        f"{WALL_T + w / 2:g} m clear of other rooms")
        if kind == "blocked" and "block_at" in L:
            d = min(_dist_seg(L["block_at"], a, b) for a, b in zip(path, path[1:]))
            if d > 0.5:
                warn(f"{tag}: block_at {L['block_at']} is {d:.1f} m off the path; it will be snapped onto it")
        for d in L.get("doors", ()):
            if d not in ("a", "b") or L.get(d) in junctions:
                err(f"{tag}: doors lists the ends (\"a\", \"b\") that have a door, and only room ends can have one")
        if L.get("doors") and vent:
            err(f"{tag}: a vent cannot have doors")
        if vent and "z" in L and not vw / 2 <= float(L["z"]) <= 30:
            err(f"{tag}: z is the height of the duct's center above the floor; it must be at least {vw / 2:g}")
    for name, n in used.items():
        if n < 2:
            warn(f"junction {name} is used by {n} path(s); a junction should join at least two")
    plain = [(n, [tuple(p) for p in L["path"]]) for n, L in enumerate(links)
             if L.get("kind") in ("open", "blocked") and len(L.get("path", [])) >= 2]
    for i, (na, pa) in enumerate(plain):
        for nb, pb in plain[i + 1:]:
            for a, b in zip(pa, pa[1:]):
                for c, d in zip(pb, pb[1:]):
                    x = _cross(a, b, c, d)
                    if x and not any(math.dist(x, q) < 0.5 for q in junctions.values()):
                        warn(f"links {na} and {nb} cross at ({x[0]:.1f}, {x[1]:.1f}) without a junction; add one "
                             "there or reroute")

    entered = set()
    for n, e in enumerate(layout.get("entrances") or []):
        tag = f"entrance {n} ({e.get('kind')}, room {e.get('room')})"
        room = by_id.get(e.get("room"))
        if room is None:
            err(f"{tag}: unknown room")
            continue
        entered.add(e["room"])
        if e.get("kind") not in ENTRANCE_KINDS:
            err(f"{tag}: kind must be one of {', '.join(ENTRANCE_KINDS)}")
        elif e["kind"] == "shaft":
            at, rad = e.get("at"), float(e.get("radius", 1.2))
            if (not isinstance(at, (list, tuple)) or len(at) != 2
                    or not room["min"][0] + rad < at[0] < room["max"][0] - rad
                    or not room["min"][1] + rad < at[1] < room["max"][1] - rad):
                err(f"{tag}: a shaft needs \"at\": [x, y] inside the room, at least its radius from the walls")
        else:
            side = e.get("side")
            if side not in OUTWARD or not isinstance(e.get("at"), (int, float)):
                err(f"{tag}: needs \"side\" (N, S, E or W) and \"at\" (the x of a N/S wall or the y of an E/W wall)")
                continue
            lo, hi = _span(room, side)
            half = _entrance_width(e, cw) / 2
            if not lo + half + 0.05 <= e["at"] <= hi - half - 0.05:
                err(f"{tag}: at={e['at']} with width {2 * half:g} m runs past the corner of the {side} wall "
                    f"(keep it within {lo + half + 0.05:g}..{hi - half - 0.05:g})")
    if not (layout.get("entrances") or []):
        warn("no entrances: add at least one so the map shows how people get in")
    seen, todo = set(), [ids[0]]
    hops = {rid: set() for rid in by_id}
    for L in links:
        group = [x for x in (L.get("a"), L.get("b")) if x in by_id]
        for name in (L.get("a"), L.get("b")):   # rooms joined through a junction are joined to each other
            if name in junctions:
                group += [x for M in links for x in (M.get("a"), M.get("b")) if name in (M.get("a"), M.get("b"))
                          and x in by_id]
        for a in group:
            hops[a].update(group)
    while todo:
        rid = todo.pop()
        if rid not in seen:
            seen.add(rid)
            todo += list(hops[rid] - seen)
    for rid in ids:
        if rid not in seen and rid not in entered:
            warn(f"room {rid} cannot be reached from room {ids[0]} and has no entrance")
    return sorted(out, key=lambda p: p.level != "error")


def _dist_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.dist(p, (a[0] + dx * t, a[1] + dy * t))


def _entrance_width(e, cw):
    return float(e.get("width", {"stairs": 2.6, "ramp": 4.0, "door": 1.6}.get(e.get("kind"), min(cw, 2.4))))


def _entrance_run(room, e):
    """(point on the room edge, far end) of a stairs, ramp, tunnel or door entrance, as build.py lays it out."""
    (x0, y0), (x1, y1) = room["min"], room["max"]
    base = {"N": (e["at"], y1), "S": (e["at"], y0), "E": (x1, e["at"]), "W": (x0, e["at"])}[e["side"]]
    reach = {"stairs": 1.25 + 0.28 * int(e.get("steps", 16)), "ramp": WALL_T + float(e.get("length", 10.0)),
             "tunnel": float(e.get("length", 8.0))}.get(e.get("kind"), 2.4)
    ox, oy = OUTWARD[e["side"]]
    return base, (base[0] + ox * reach, base[1] + oy * reach)


def _path_mid(path):
    """The point halfway along a polyline."""
    left = sum(math.dist(a, b) for a, b in zip(path, path[1:])) / 2
    for a, b in zip(path, path[1:]):
        seg = math.dist(a, b)
        if seg >= left and seg > 0:
            t = left / seg
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        left -= seg
    return tuple(path[-1])


def bounds(layout, pad=4.0):
    """(x0, y0, x1, y1) around every room, path, junction and entrance."""
    xs, ys = [], []
    for r in layout["rooms"]:
        xs += [r["min"][0], r["max"][0]]
        ys += [r["min"][1], r["max"][1]]
    for L in layout.get("links") or []:
        xs += [p[0] for p in L["path"]]
        ys += [p[1] for p in L["path"]]
    for p in (layout.get("junctions") or {}).values():
        xs.append(p[0])
        ys.append(p[1])
    rooms = {r["id"]: r for r in layout["rooms"]}
    for e in layout.get("entrances") or []:
        if e.get("room") in rooms and e.get("side") in OUTWARD and isinstance(e.get("at"), (int, float)):
            tip = _entrance_run(rooms[e["room"]], e)[1]
            xs.append(tip[0])
            ys.append(tip[1])
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def draw(layout, path, problems=(), width=1800):
    """Write a to-scale plan of the layout (north up) to `path`: rooms with ids, names and sizes, corridors at
    their real width, vents dashed, blockages, doors, entrances, a 10 m grid and a scale bar."""
    from PIL import Image, ImageDraw, ImageFont

    cw, ch, vw, _ = sizes(layout)
    x0, y0, x1, y1 = bounds(layout, pad=8.0)
    s = width / (x1 - x0)
    height = round((y1 - y0) * s)
    footer = 26 * (len(problems) + 1) + 20 if problems else 0
    img = Image.new("RGB", (width, height + footer), (244, 242, 232))
    d = ImageDraw.Draw(img)
    fonts = Path(__file__).resolve().parents[1] / "fonts"

    def font(size, bold=True):
        try:
            return ImageFont.truetype(str(fonts / f"BarlowSemiCondensed-{'SemiBold' if bold else 'Regular'}.ttf"), size)
        except OSError:
            return ImageFont.load_default()

    def P(p):
        return (p[0] - x0) * s, (y1 - p[1]) * s

    g = math.ceil(x0 / 10) * 10
    while g < x1:
        d.line([P((g, y0)), P((g, y1))], fill=(222, 219, 206), width=1)
        d.text((P((g, y1))[0] + 3, 2), f"{g:g}", font=font(14, False), fill=(150, 146, 130))
        g += 10
    g = math.ceil(y0 / 10) * 10
    while g < y1:
        d.line([P((x0, g)), P((x1, g))], fill=(222, 219, 206), width=1)
        d.text((3, P((x0, g))[1] + 2), f"{g:g}", font=font(14, False), fill=(150, 146, 130))
        g += 10
    red, ink = (200, 40, 44), (28, 28, 26)
    for L in layout.get("links") or []:
        pts = [P(p) for p in L["path"]]
        if L["kind"] == "vent":
            for a, b in zip(pts, pts[1:]):
                n = max(1, int(math.dist(a, b) / 14))
                for i in range(0, n, 2):
                    d.line([(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n),
                            (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)],
                           fill=(70, 110, 160), width=max(2, round(vw * s)))
            continue
        w = float(L.get("width", cw))
        d.line(pts, fill=(196, 192, 176), width=max(3, round(w * s)), joint="curve")
        d.line(pts, fill=red, width=2)
        if L["kind"] == "blocked":
            bx, by = P(L.get("block_at") or _path_mid(L["path"]))
            r = max(8, w * s * 0.6)
            d.line([(bx - r, by - r), (bx + r, by + r)], fill=ink, width=5)
            d.line([(bx - r, by + r), (bx + r, by - r)], fill=ink, width=5)
    for name, p in (layout.get("junctions") or {}).items():
        px, py = P(p)
        h = (cw / 2) * s
        d.rectangle((px - h, py - h, px + h, py + h), fill=(196, 192, 176), outline=red, width=2)
        d.text((px, py), str(name), font=font(15), fill=ink, anchor="mm")
    for r in layout["rooms"]:
        (ax, ay), (bx, by) = P((r["min"][0], r["max"][1])), P((r["max"][0], r["min"][1]))
        t = WALL_T * s
        d.rectangle((ax - t, ay - t, bx + t, by + t), fill=ink)
        d.rectangle((ax, ay, bx, by), fill=(255, 255, 250))
        cx, cy = (ax + bx) / 2, (ay + by) / 2
        d.text((cx, cy - 12), f"{r['id']}  {r['name']}", font=font(20), fill=ink, anchor="mm")
        d.text((cx, cy + 12), f"{r['max'][0] - r['min'][0]:g} x {r['max'][1] - r['min'][1]:g} m, "
               f"h {r.get('height', ch):g}", font=font(15, False), fill=(90, 88, 80), anchor="mm")
    for L in layout.get("links") or []:   # door and secret marks go on top of the room walls
        ends = [L["path"][0] if end == "a" else L["path"][-1] for end in L.get("doors", ())]
        for px, py in map(P, ends or ([_path_mid(L["path"])] if L.get("secret") else [])):
            d.rectangle((px - 9, py - 9, px + 9, py + 9), fill=(244, 242, 232), outline=red, width=3)
            if L.get("secret"):
                d.text((px, py), "S", font=font(14), fill=red, anchor="mm")
    rooms = {r["id"]: r for r in layout["rooms"]}
    for e in layout.get("entrances") or []:
        room = rooms.get(e.get("room"))
        if room is None:
            continue
        if e.get("kind") == "shaft" and isinstance(e.get("at"), (list, tuple)):
            px, py = P(e["at"])
            rr = float(e.get("radius", 1.2)) * s
            d.ellipse((px - rr, py - rr, px + rr, py + rr), outline=red, width=4)
            ex, ey = px, P(room["min"])[1] + WALL_T * s + 14   # under the room, clear of its name
        elif e.get("side") in OUTWARD and isinstance(e.get("at"), (int, float)):
            base, tip = _entrance_run(room, e)
            thick = max(4, round(_entrance_width(e, cw) * s))
            d.line([P(base), P(tip)], fill=red, width=thick)
            if e.get("blocked"):
                bx, by = P((base[0] + (tip[0] - base[0]) * 0.55, base[1] + (tip[1] - base[1]) * 0.55))
                d.line([(bx - thick, by - thick), (bx + thick, by + thick)], fill=ink, width=5)
                d.line([(bx - thick, by + thick), (bx + thick, by - thick)], fill=ink, width=5)
            ox, oy = OUTWARD[e["side"]]
            if ox:   # beside an east or west wall: label above the run
                ex, ey = (P(base)[0] + P(tip)[0]) / 2, P(base)[1] - thick / 2 - 12
            else:    # beyond the far end of a north or south run
                ex, ey = P(tip)[0], P(tip)[1] - oy * 14
        else:
            continue
        text = f"{e['kind']}: {e.get('label') or ''}".strip(": ")
        half = d.textlength(text, font=font(15)) / 2 + 6
        d.text((min(max(ex, half), width - half), ey), text, font=font(15), fill=red, anchor="mm")
    bar = 10 * s
    d.rectangle((30, height - 40, 30 + bar, height - 32), fill=ink)
    d.text((30 + bar + 10, height - 36), "10 m", font=font(18), fill=ink, anchor="lm")
    d.text((width - 62, 46), "N", font=font(26), fill=ink, anchor="rm")
    d.polygon([(width - 42, 20), (width - 54, 62), (width - 30, 62)], fill=ink)
    if problems:
        d.rectangle((0, height, width, height + footer), fill=(255, 255, 250))
        d.text((16, height + 8), f"{len(problems)} problem(s)", font=font(18), fill=ink)
        for i, p in enumerate(problems, 1):
            d.text((16, height + 8 + 26 * i), f"{p.level.upper()}: {p.text}"[:210], font=font(16, False),
                   fill=red if p.level == "error" else (120, 90, 20))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
