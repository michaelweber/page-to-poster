"""Materials and decals for map models (import inside Blender).

Every surface uses world-space box (triplanar) projection, so procedurally built geometry needs no UVs and texel
density stays constant in meters. Textures are grayscale ink drawings (<project>/textures/<tex>.png, tileable)
tinted by the material color; a missing texture falls back to the flat color.

Tricks borrowed from game texturing:
- hide the repeat: a large soft noise varies brightness, and a second copy of the texture at another scale is blended in
- grime where dirt collects: ambient occlusion darkens corners and contact lines
- edge wear: Cycles pointiness lightens convex edges
- decals: alpha-cut ink stickers (<project>/decals/<name>.png) laid on surfaces
- cut walls read as solid black (architectural poché): any back face seen through the section cut renders black

The poster prints four inks. A surface whose rendered color is strongly red (hazard, liquid_acid, emissive_red, or
any material registered with a red color) prints in the accent ink; everything else becomes ink, paper or white by
its brightness.
"""
from pathlib import Path

import bpy

TEX_DIR = DECAL_DIR = Path(".")

# name: color (linear-ish RGB tint over the ink texture), tex (texture key or None), scale (meters per tile),
# rough, metal, emit (strength) and bump.
MATS = {
    "concrete_floor": dict(color=(0.50, 0.49, 0.47), tex="concrete_floor", scale=4.0, rough=0.85, bump=0.25),
    "concrete_wall":  dict(color=(0.42, 0.41, 0.40), tex="concrete_wall", scale=4.0, rough=0.9, bump=0.3),
    "stone_floor":    dict(color=(0.52, 0.50, 0.47), tex="stone_floor", scale=3.0, rough=0.9, bump=0.4),
    "stone_wall":     dict(color=(0.44, 0.42, 0.40), tex="stone_wall", scale=3.0, rough=0.95, bump=0.45),
    "rock":           dict(color=(0.40, 0.38, 0.36), tex="rock", scale=4.0, rough=1.0, bump=0.6),
    "rust_metal":     dict(color=(0.42, 0.33, 0.30), tex="rust_metal", scale=2.0, rough=0.7, metal=0.4, bump=0.35),
    "painted_metal":  dict(color=(0.55, 0.56, 0.58), tex="rust_metal", scale=2.0, rough=0.55, metal=0.2, bump=0.2),
    "hazard":         dict(color=(0.75, 0.08, 0.07), tex="hazard_stripes", scale=1.0, rough=0.6, bump=0.1),
    "corrugated":     dict(color=(0.48, 0.47, 0.46), tex="corrugated_metal", scale=2.5, rough=0.6, metal=0.3, bump=0.4),
    "grating":        dict(color=(0.40, 0.40, 0.41), tex="grating", scale=1.0, rough=0.5, metal=0.6, bump=0.5),
    "tile":           dict(color=(0.80, 0.79, 0.76), tex="tile", scale=1.5, rough=0.35, bump=0.3),
    "rubble":         dict(color=(0.50, 0.49, 0.47), tex="rubble", scale=3.0, rough=0.95, bump=0.6),
    "dirt":           dict(color=(0.45, 0.43, 0.40), tex="dirt", scale=3.0, rough=1.0, bump=0.3),
    "foam":           dict(color=(0.70, 0.68, 0.62), tex="foam", scale=1.5, rough=1.0, bump=0.5),
    "wood":           dict(color=(0.50, 0.44, 0.38), tex="wood", scale=1.5, rough=0.85, bump=0.3),
    "planks":         dict(color=(0.48, 0.42, 0.36), tex="planks", scale=2.0, rough=0.85, bump=0.35),
    "liquid_runoff":  dict(color=(0.10, 0.11, 0.13), tex="liquid_crust", scale=3.0, rough=0.2, bump=0.2),
    "liquid_acid":    dict(color=(0.62, 0.07, 0.05), tex="liquid_crust", scale=3.0, rough=0.1, emit=0.12, bump=0.1),
    "water":          dict(color=(0.16, 0.18, 0.20), tex=None, scale=3.0, rough=0.08),
    "ice":            dict(color=(0.80, 0.86, 0.90), tex="rock", scale=3.0, rough=0.25, bump=0.4),
    "glass":          dict(color=(0.85, 0.87, 0.88), tex=None, scale=1.0, rough=0.05),
    "black_plastic":  dict(color=(0.04, 0.04, 0.045), tex="rust_metal", scale=2.0, rough=0.5, bump=0.1),
    "fabric":         dict(color=(0.35, 0.34, 0.33), tex="foam", scale=1.0, rough=1.0, bump=0.2),
    "mushroom":       dict(color=(0.78, 0.74, 0.66), tex="foam", scale=0.5, rough=0.9, bump=0.3),
    "bone":           dict(color=(0.82, 0.80, 0.72), tex=None, scale=1.0, rough=0.7),
    "emissive_warm":  dict(color=(1.0, 0.85, 0.6), tex=None, scale=1.0, rough=0.5, emit=12.0),
    "emissive_red":   dict(color=(1.0, 0.06, 0.04), tex=None, scale=1.0, rough=0.5, emit=10.0),
}
_BUILTIN = dict(MATS)

_cache: dict = {}


def configure(tex_dir, decal_dir):
    """Point the module at one project's textures and decals and forget materials from an earlier build."""
    global TEX_DIR, DECAL_DIR
    TEX_DIR, DECAL_DIR = Path(tex_dir), Path(decal_dir)
    _cache.clear()
    MATS.clear()
    MATS.update(_BUILTIN)


def texture_path(key):
    p = TEX_DIR / f"{key}.png"
    return p if key and p.exists() else None


def _box_image(nt, image, vec, blend=0.25):
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.projection = "BOX"
    tex.projection_blend = blend
    tex.interpolation = "Cubic"
    nt.links.new(vec, tex.inputs["Vector"])
    return tex


def _math(nt, op, a, b=None, clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def get(name: str) -> bpy.types.Material:
    """Material `name` from MATS (KeyError on an unknown name), built once per file."""
    if name in _cache:
        return _cache[name]
    spec = MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = spec.get("rough", 0.8)
    bsdf.inputs["Metallic"].default_value = spec.get("metal", 0.0)

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    scale = spec.get("scale", 2.0)
    vec = nt.nodes.new("ShaderNodeVectorMath")
    vec.operation = "SCALE"
    nt.links.new(geo.outputs["Position"], vec.inputs[0])
    vec.inputs["Scale"].default_value = 1.0 / scale

    ink = None
    path = texture_path(spec.get("tex"))
    if path:
        img = bpy.data.images.load(str(path), check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        t1 = _box_image(nt, img, vec.outputs[0])
        # a second copy at another scale, blended in, hides the tile grid
        vec2 = nt.nodes.new("ShaderNodeVectorMath")
        vec2.operation = "SCALE"
        nt.links.new(geo.outputs["Position"], vec2.inputs[0])
        vec2.inputs["Scale"].default_value = 1.0 / (scale * 2.37)
        t2 = _box_image(nt, img, vec2.outputs[0])
        ink = _math(nt, "ADD", _math(nt, "MULTIPLY", t1.outputs["Color"], 0.7),
                    _math(nt, "MULTIPLY", t2.outputs["Color"], 0.3))
    # macro brightness variation (big soft noise)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.04
    noise.inputs["Detail"].default_value = 2.0
    nt.links.new(geo.outputs["Position"], noise.inputs["Vector"])
    macro = _math(nt, "ADD", _math(nt, "MULTIPLY", noise.outputs["Fac"], 0.35), 0.82)
    shade = _math(nt, "MULTIPLY", ink, macro) if ink else macro

    # grime in crevices (AO) and wear on convex edges (pointiness, Cycles only)
    ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = 1.2
    ao.samples = 8
    grime = _math(nt, "ADD", _math(nt, "MULTIPLY", ao.outputs["AO"], 0.55), 0.45)
    edge = _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", geo.outputs["Pointiness"], 0.52, clamp=True), 2.5)
    shade = _math(nt, "ADD", _math(nt, "MULTIPLY", shade, grime), edge)

    tint = nt.nodes.new("ShaderNodeVectorMath")
    tint.operation = "MULTIPLY"
    tint.inputs[0].default_value = spec["color"]
    nt.links.new(shade, tint.inputs[1])
    nt.links.new(tint.outputs["Vector"], bsdf.inputs["Base Color"])

    if ink and spec.get("bump", 0) > 0:
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = spec["bump"]
        bump.inputs["Distance"].default_value = 0.02
        nt.links.new(ink, bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    if spec.get("emit"):
        bsdf.inputs["Emission Color"].default_value = (*spec["color"], 1.0)
        bsdf.inputs["Emission Strength"].default_value = spec["emit"]

    # poche: back faces (inside walls exposed by the section cut) render solid black
    black = nt.nodes.new("ShaderNodeEmission")
    black.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(geo.outputs["Backfacing"], mix.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(black.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    _cache[name] = m
    return m


# Ink color per decal when the caller passes none: black unless listed here. A red color prints in the accent ink.
DECAL_COLORS = {
    "splatter": (0.82, 0.81, 0.76), "blood_smear": (0.35, 0.015, 0.015), "rust_streak": (0.30, 0.12, 0.08),
}


def decal(name: str, location, size, rotation_z=0.0, color=None, lift=0.004, collection=None):
    """Lay decals/<name>.png (RGBA ink sticker) flat on the floor at `location` (x, y, z of the surface), `size`
    meters across its longest side. Returns the plane, or None when the decal image is missing."""
    color = color or DECAL_COLORS.get(name, (0.05, 0.05, 0.05))
    path = DECAL_DIR / f"{name}.png"
    if not path.exists():
        return None
    img = bpy.data.images.load(str(path), check_existing=True)
    w, h = img.size
    sx, sy = (size, size * h / w) if w >= h else (size * w / h, size)
    mesh = bpy.data.meshes.new(f"decal_{name}")
    mesh.from_pydata([(-sx / 2, -sy / 2, 0), (sx / 2, -sy / 2, 0), (sx / 2, sy / 2, 0), (-sx / 2, sy / 2, 0)],
                     [], [(0, 1, 2, 3)])
    mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        mesh.uv_layers[0].data[loop.index].uv = uv
    ob = bpy.data.objects.new(f"decal_{name}", mesh)
    ob.location = (location[0], location[1], location[2] + lift)
    ob.rotation_euler = (0, 0, rotation_z)
    # camera-only: otherwise the floor's AO and shadows see the (transparent) plane and print a square around it
    ob.visible_diffuse = ob.visible_glossy = ob.visible_shadow = False
    ob.visible_transmission = ob.visible_volume_scatter = False
    key = f"decal::{name}::{tuple(round(c, 3) for c in color)}"
    m = _cache.get(key)
    if m is None:
        m = bpy.data.materials.new(key)
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
        transp = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(transp.outputs["BSDF"], mix.inputs[1])
        nt.links.new(bsdf.outputs["BSDF"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        _cache[key] = m
    mesh.materials.append(m)
    (collection or bpy.context.scene.collection).objects.link(ob)
    return ob
