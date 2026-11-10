"""Cycles renders of a built model (run inside Blender; `p2p.py render` and `p2p.py poster` do this for you).

blender -b --factory-startup --python-exit-code 1 --python mapkit/blender/render.py -- PROJECT VIEW [options]
VIEW is one of:
  poster   isometric cutaway passes for the poster: renders/poster_color.png, poster_depth.png (16-bit, near is
           white), poster_normal.png and poster.json (the camera, so labels can be placed). --width PIXELS
  closeup  the same cutaway framed on one room at a time, for checking props: renders/closeup_<id>.png.
           --rooms 1,2 (default: all), --width PIXELS
  plan     top-down plan of the whole site, cut walls flat black: renders/plan.png. --ppm PIXELS_PER_METER
  rooms    top-down battle map per room: renders/room_<id>.png. --rooms, --ppm
  eye      what you see on entering each room, by flashlight: renders/eye_<id>.png, eye_<id>_depth.png (near is
           white) and eye_<id>.json. The depth map drives the optional paint step. --rooms
Options for every view: --samples N, --device auto|cpu|optix|cuda|hip|metal|oneapi, --gpu NAME (part of a
device name, when several GPUs are installed).

PROJECT/poster.json may hold "view": {"azimuth": -45} (compass direction the cutaway is seen from, see
docs/poster.md), "cutaway": {"wall": 3.2, "tall": 20, "props": {"<room id>": height}} and
"eye": {"<room id>": {"inward": 3.0, "z": 2.4, "aisle": true}} to move an eye-level camera off a blocked doorway.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mapkit.blender import kit as k  # noqa: E402

GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")
ISO_TILT = math.radians(54.7356)   # true isometric: the camera looks down the cube diagonal
PLAN_CUT_Z, PLAN_CAM_Z, PLAN_WALL_CAP = 4.5, 80.0, 4.0
EYE_W, EYE_H, EYE_Z = 1216, 832, 1.7


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit(__doc__)
    opts = {"project": Path(argv[0]).resolve(), "view": argv[1], "rooms": None, "ppm": None, "width": None,
            "samples": None, "device": "auto", "gpu": None}
    it = iter(argv[2:])
    for key in it:
        opts[key.lstrip("-")] = next(it)
    return opts


def setup_render(samples, device="auto", gpu=None):
    """Cycles on the first GPU backend this machine offers (or the one asked for), else on the CPU."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    device = device.lower()
    wanted = GPU_BACKENDS if device == "auto" else () if device == "cpu" else (device.upper(),)
    used = None
    for backend in wanted:
        try:
            prefs.compute_device_type = backend
        except TypeError:   # this Blender build has no such backend
            continue
        prefs.refresh_devices()
        names = [d.name for d in prefs.devices if d.type == backend and (not gpu or gpu.lower() in d.name.lower())]
        if names:
            for d in prefs.devices:
                d.use = d.type == backend and d.name in names
            used = f"{backend} {names}"
            break
    if used:
        scene.cycles.device = "GPU"
    elif gpu or device not in ("auto", "cpu"):   # an explicit request must not quietly become a CPU render
        raise SystemExit(f"render: no {'GPU' if device == 'auto' else device.upper()} device"
                         + (f" matching {gpu!r}" if gpu else "") + " found; run `p2p.py doctor` to list devices")
    else:
        prefs.compute_device_type = "NONE"
        scene.cycles.device = "CPU"
        used = "CPU"
    scene.cycles.samples = int(samples)
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 6
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.resolution_percentage = 100
    print(f"[mapkit] rendering on {used}, {samples} samples", flush=True)


def top_z(ob):
    return max((ob.matrix_world @ Vector(c)).z for c in ob.bound_box)


def visible_meshes():
    hidden = {c.name for c in bpy.data.collections if c.hide_render}
    return [ob for ob in bpy.data.objects if ob.type == "MESH" and not ob.hide_render
            and not {c.name for c in ob.users_collection} & hidden]


def dim_practicals():
    """Lights built for a dark interior are scaled by their map_dim so they do not wash out a lit map view."""
    for ob in bpy.data.objects:
        if ob.type == "LIGHT":
            ob.data.energy *= float(ob.get("map_dim", 1.0))


def cutaway(cfg):
    """Slice the model open: walls at cfg["wall"], everything else at cfg["tall"], and the props of the rooms
    listed in cfg["props"] at their own height (for ceiling-hung clutter that would float over the cut walls)."""
    wall_cap, tall_cap = float(cfg.get("wall", 3.2)), float(cfg.get("tall", 20.0))
    walls = [ob for ob in bpy.data.collections["walls"].all_objects if ob.type == "MESH"]
    walls += [ob for ob in bpy.data.objects if ob.type == "MESH" and ob.get("cap_as_wall")]
    for ob in walls:
        k.cap_object(ob, wall_cap)
    for ob in [o for o in visible_meshes() if not o.get("nocap") and top_z(o) > tall_cap]:
        k.cap_object(ob, tall_cap + float(ob.get("cap_base", 0.0)))
    for rid, h in (cfg.get("props") or {}).items():
        col = bpy.data.collections.get(f"props_{rid}")
        if col is None:
            raise SystemExit(f"render: poster.json cutaway.props names room {rid!r}, which has no props")
        for ob in [o for o in col.all_objects if o.type == "MESH" and not o.get("nocap")]:
            k.cap_object(ob, float(h))


def override(kind, near=0.0, far=1.0, distance=False):
    """Emission material showing depth (near is white) or the surface normal instead of the shaded surface."""
    m = bpy.data.materials.new(f"{kind}_override")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if kind == "depth":
        cam = nt.nodes.new("ShaderNodeCameraData")
        ramp = nt.nodes.new("ShaderNodeMapRange")
        ramp.inputs["From Min"].default_value = near
        ramp.inputs["From Max"].default_value = far
        ramp.inputs["To Min"].default_value = 1.0
        ramp.inputs["To Max"].default_value = 0.0
        nt.links.new(cam.outputs["View Distance" if distance else "View Z Depth"], ramp.inputs["Value"])
        nt.links.new(ramp.outputs["Result"], em.inputs["Strength"])
    else:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        remap = nt.nodes.new("ShaderNodeVectorMath")
        remap.operation = "MULTIPLY_ADD"
        remap.inputs[1].default_value = (0.5, 0.5, 0.5)
        remap.inputs[2].default_value = (0.5, 0.5, 0.5)
        nt.links.new(geo.outputs["Normal"], remap.inputs[0])
        nt.links.new(remap.outputs["Vector"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def new_world(name, grey):
    world = bpy.data.worlds.new(name)
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (*grey, 1)
    bpy.context.scene.world = world
    return world


def write(out, name, mapping=None):
    out.mkdir(exist_ok=True)
    bpy.context.scene.render.filepath = str(out / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    if mapping is not None:
        (out / f"{name}.json").write_text(json.dumps(mapping, indent=1), encoding="utf-8")
    print(f"+ {out / name}.png", flush=True)


def site_bounds(anchors, pad):
    bs = [r["bounds"] for r in anchors["rooms"].values()]
    return min(b[0] for b in bs) - pad, min(b[1] for b in bs) - pad, max(b[2] for b in bs) + pad, max(b[3] for b in bs) + pad


# ============================================================================================== isometric cutaway
def iso_lighting():
    new_world("iso_world", (0.25, 0.25, 0.25))
    sun = bpy.data.lights.new("iso_key", "SUN")
    sun.energy = 3.2
    sun.angle = math.radians(3)
    ob = bpy.data.objects.new("iso_key", sun)
    ob.rotation_euler = Euler((math.radians(40), 0, math.radians(-110)))  # from the west-north-west, high
    bpy.context.scene.collection.objects.link(ob)
    dim_practicals()


def iso_camera(points, azimuth, pad, width):
    """Orthographic camera at the isometric tilt, turned by `azimuth`, framing `points`. Returns its mapping."""
    scene = bpy.context.scene
    rot = Euler((ISO_TILT, 0, math.radians(azimuth)))
    R = rot.to_matrix()
    right, up, back = R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0)), R @ Vector((0, 0, 1))
    us, vs, ds = [p.dot(right) for p in points], [p.dot(up) for p in points], [p.dot(back) for p in points]
    w_m, h_m = max(us) - min(us) + 2 * pad, max(vs) - min(vs) + 2 * pad
    cam_data = bpy.data.cameras.new("cam_iso")
    cam_data.type = "ORTHO"
    cam_data.clip_end = 5000
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.ortho_scale = w_m
    cam = bpy.data.objects.new("cam_iso", cam_data)
    cam.rotation_euler = rot
    cam.location = right * (max(us) + min(us)) / 2 + up * (max(vs) + min(vs)) / 2 + back * (max(ds) + 50)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = int(width), round(int(width) * h_m / w_m)
    return {"kind": "ortho_iso", "location": list(cam.location), "rotation": list(rot), "ortho_scale": w_m,
            "px": [scene.render.resolution_x, scene.render.resolution_y],
            "depth_span_m": max(ds) - min(ds)}


def view_poster(o, cfg, out):
    setup_render(o["samples"] or 128, o["device"], o["gpu"])
    iso_lighting()
    cutaway(cfg.get("cutaway") or {})
    scene = bpy.context.scene
    pts = [ob.matrix_world @ Vector(c) for ob in visible_meshes() for c in ob.bound_box]
    cam = iso_camera(pts, float((cfg.get("view") or {}).get("azimuth", -45)), 6.0, o["width"] or 7200)
    near, far = 50.0, cam["depth_span_m"] + 50.0
    layer = scene.view_layers[0]
    for name, mat in (("poster_color", None), ("poster_depth", override("depth", near, far)),
                      ("poster_normal", override("normal"))):
        layer.material_override = mat
        settings = scene.render.image_settings
        if mat:  # flat passes: no lighting, and a black world so the background reads as "no geometry"
            scene.cycles.samples, scene.cycles.use_denoising = 4, False
            scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)
        if name == "poster_depth":  # linear 16-bit grey, so depth steps convert back to meters
            scene.view_settings.view_transform = "Raw"
            settings.color_mode, settings.color_depth = "BW", "16"
        else:
            scene.view_settings.view_transform = "Standard"
            settings.color_mode, settings.color_depth = "RGB", "8"
        write(out, name)
    (out / "poster.json").write_text(json.dumps(cam, indent=1), encoding="utf-8")


def view_closeup(o, cfg, anchors, out):
    setup_render(o["samples"] or 48, o["device"], o["gpu"])
    iso_lighting()
    cutaway(cfg.get("cutaway") or {})
    azimuth = float((cfg.get("view") or {}).get("azimuth", -45))
    want = set(o["rooms"].split(",")) if o["rooms"] else None
    for rid, r in anchors["rooms"].items():
        if want and rid not in want:
            continue
        x0, y0, x1, y1 = r["rect"]
        top = min(float(r["height"]), 8.0)
        pts = [Vector((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (-1.0, top)]
        iso_camera(pts, azimuth, 2.5, o["width"] or 1400)
        write(out, f"closeup_{rid}")


# ============================================================================================== top-down
def plan_lighting(anchors):
    """Readable map light: a big soft overhead fill plus a low raking key from the north-west for relief."""
    x0, y0, x1, y1 = site_bounds(anchors, 0)
    size = 1.6 * max(x1 - x0, y1 - y0)
    new_world("map_world", (0.02, 0.02, 0.022))
    fill = bpy.data.lights.new("map_fill", "AREA")
    fill.size = size
    fill.energy = 1.4e5 * (size / 260) ** 2
    ob = bpy.data.objects.new("map_fill", fill)
    ob.location = ((x0 + x1) / 2, (y0 + y1) / 2, 120)
    bpy.context.scene.collection.objects.link(ob)
    key = bpy.data.lights.new("map_key", "SUN")
    key.energy = 0.35
    key.angle = math.radians(8)
    ob = bpy.data.objects.new("map_key", key)
    ob.rotation_euler = Euler((math.radians(50), 0, math.radians(135)))
    bpy.context.scene.collection.objects.link(ob)
    dim_practicals()


def poche_walls():
    """Plan convention: everything in the `walls` collection renders flat near-black."""
    m = bpy.data.materials.new("poche")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.012, 0.012, 0.013, 1)
    nt.links.new(em.outputs["Emission"], nt.nodes.new("ShaderNodeOutputMaterial").inputs["Surface"])
    for ob in bpy.data.collections["walls"].all_objects:
        if ob.type == "MESH":
            ob.data.materials.clear()
            ob.data.materials.append(m)


def ortho_top(name, cx, cy, w_m, h_m, ppm):
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new(name)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(w_m, h_m)
    cam_data.clip_start = PLAN_CAM_Z - PLAN_CUT_Z
    cam_data.clip_end = PLAN_CAM_Z + 60
    cam_data.sensor_fit = "HORIZONTAL" if w_m >= h_m else "VERTICAL"
    cam = bpy.data.objects.new(name, cam_data)
    cam.location = (cx, cy, PLAN_CAM_Z)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = round(w_m * ppm), round(h_m * ppm)
    return {"kind": "ortho_top", "center": [cx, cy], "size_m": [w_m, h_m], "ppm": ppm,
            "px": [scene.render.resolution_x, scene.render.resolution_y], "cut_z": PLAN_CUT_Z}


def view_top(o, anchors, out, per_room):
    setup_render(o["samples"] or 128, o["device"], o["gpu"])
    k.apply_cap(PLAN_WALL_CAP)
    plan_lighting(anchors)
    poche_walls()
    if not per_room:
        x0, y0, x1, y1 = site_bounds(anchors, 6.0)
        write(out, "plan", ortho_top("cam_plan", (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0,
                                     float(o["ppm"] or 30)))
        return
    want = set(o["rooms"].split(",")) if o["rooms"] else None
    for rid, r in anchors["rooms"].items():
        if want and rid not in want:
            continue
        b = r["bounds"]
        m = ortho_top(f"cam_room_{rid}", (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0], b[3] - b[1],
                      float(o["ppm"] or 70))
        m["room"] = rid
        write(out, f"room_{rid}", m)


# ============================================================================================== eye level
def doorway(anchors, rid):
    """(door point on the room edge, unit vector pointing into the room) for the room's first open doorway, or
    the middle of its south wall when it has none."""
    room = anchors["rooms"][rid]
    c = Vector(room["center"][:2])
    for link in anchors["links"]:
        if link["kind"] == "open" and rid in (str(link["a"]), str(link["b"])):
            p = Vector(link["path"][0] if str(link["a"]) == rid else link["path"][-1])
            return p, (c - p).normalized()
    p = Vector((c.x, room["rect"][1]))
    return p, Vector((0.0, 1.0))


def view_eye(o, cfg, anchors, out):
    setup_render(o["samples"] or 96, o["device"], o["gpu"])
    scene = bpy.context.scene
    new_world("eye_world", (0.004, 0.004, 0.005))
    scene.render.resolution_x, scene.render.resolution_y = EYE_W, EYE_H
    want = set(o["rooms"].split(",")) if o["rooms"] else None
    for rid, room in anchors["rooms"].items():
        if want and rid not in want:
            continue
        ov = (cfg.get("eye") or {}).get(rid, {})
        door, inward = doorway(anchors, rid)
        c = Vector(room["center"][:2])
        eye = door + inward * float(ov.get("inward", 1.5))
        if ov.get("aisle"):   # slide onto the room's centre line and look along it instead of at the centre
            eye = c + inward * (eye - c).dot(inward)
        cam_data = bpy.data.cameras.new(f"eye_{rid}")
        cam_data.lens = 22
        cam_data.clip_end = 300
        cam = bpy.data.objects.new(f"eye_{rid}", cam_data)
        cam.location = eye.to_3d() + Vector((0, 0, float(ov.get("z", EYE_Z))))
        target = (c + inward * 8.0).to_3d() + Vector((0, 0, 2.0)) if ov.get("aisle") else Vector((*c, 1.2))
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(cam)
        scene.camera = cam
        torch = bpy.data.lights.new(f"torch_{rid}", "SPOT")
        torch.energy, torch.spot_size, torch.spot_blend, torch.color = 9000, math.radians(70), 0.6, (1.0, 0.93, 0.82)
        tob = bpy.data.objects.new(f"torch_{rid}", torch)
        tob.location = cam.location + Vector((0.25, 0, -0.2))
        tob.rotation_euler = cam.rotation_euler
        scene.collection.objects.link(tob)
        layer = scene.view_layers[0]
        layer.material_override = None
        write(out, f"eye_{rid}", {"kind": "perspective", "room": rid, "location": list(cam.location),
                                  "rotation": list(cam.rotation_euler), "lens_mm": cam_data.lens,
                                  "sensor_mm": cam_data.sensor_width, "px": [EYE_W, EYE_H]})
        x0, y0, x1, y1 = room["bounds"]
        layer.material_override = override("depth", 0.0, math.hypot(x1 - x0, y1 - y0), distance=True)
        samples = scene.cycles.samples
        scene.cycles.samples, scene.cycles.use_denoising = 1, False
        write(out, f"eye_{rid}_depth")
        scene.cycles.samples, scene.cycles.use_denoising = samples, True
        layer.material_override = None
        bpy.data.objects.remove(tob)


def main():
    o = parse_args()
    project = o["project"]
    bpy.ops.wm.open_mainfile(filepath=str(project / "build" / "model.blend"))
    anchors = json.loads((project / "build" / "anchors.json").read_text(encoding="utf-8"))
    cfg_path = project / "poster.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    out = project / "renders"
    for name in ("ceilings",):
        bpy.data.collections[name].hide_render = True
    view = o["view"]
    if view == "poster":
        view_poster(o, cfg, out)
    elif view == "closeup":
        view_closeup(o, cfg, anchors, out)
    elif view in ("plan", "rooms"):
        view_top(o, anchors, out, per_room=view == "rooms")
    elif view == "eye":
        view_eye(o, cfg, anchors, out)
    else:
        raise SystemExit(f"render: unknown view {view!r}\n{__doc__}")


if __name__ == "__main__":
    main()
