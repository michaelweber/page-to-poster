"""Textures and decals for a project (plain Python). `p2p.py textures PROJECT` runs this.

Writes PROJECT/textures/<key>.png (grayscale, tileable; the materials in mapkit/blender/materials.py look them up by
key) and PROJECT/decals/<key>.png (RGBA ink stickers placed with kit.decal). Two sources:
- default: drawn by code (mapkit.proctex). Needs nothing but Python.
- --comfy URL: painted by ComfyUI in an ink style and then made tileable. The usual game-texture recipe:
  1. paint a straight-down ink swatch (the `texture` recipe in mapkit.comfy)
  2. even the light: divide out the low-frequency brightness so no tile edge is brighter than another
  3. hide the seams: roll the image by half so the old borders meet in a cross through the middle, repaint that
     cross with the same prompt (masked img2img) and blend it in with a feathered mask. Wrapped edges now match.
  Regular patterns break under seam repainting, so hazard_stripes always comes from code.

A project may add or reword subjects in PROJECT/textures.json: {"textures": {key: subject}, "decals": {key:
subject}}. Keys that only exist there (a graffiti tag, a warning sign) need ComfyUI.
"""
import json
import random
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from . import comfy, proctex

TEXTURES = {
    "concrete_floor": "smooth poured industrial concrete floor slab, hairline cracks, old oil stains, faint trowel "
                      "marks, scattered grit",
    "concrete_wall": "board-formed concrete wall surface, horizontal plank imprints, form-tie holes in a loose grid, "
                     "water stains and efflorescence",
    "stone_floor": "worn irregular flagstone paving, tight dark joints, chipped corners, grit in the cracks",
    "stone_wall": "coursed rough-cut stone block masonry, staggered joints, chipped faces, crumbling mortar",
    "rock": "rough natural cave rock face, layered strata, fractures and pitted hollows",
    "rust_metal": "corroded riveted steel plate, flaking rust blisters, pitting, rows of rivets",
    "corrugated_metal": "corrugated steel sheet with straight parallel vertical ribs running edge to edge, dents and "
                        "rust streaks",
    "grating": "industrial steel bar grating floor, a regular grid of flat steel bars, grime caught in the openings, "
               "dark void below",
    "tile": "small square ceramic tiles in a strict regular grid with dark grout lines, chipped, cracked and stained",
    "planks": "old wooden floorboards running straight edge to edge, staggered butt joints, heavy grain, nail heads",
    "wood": "rotten water-damaged fiberboard panels, swollen and moldy, peeling laminate",
    "rubble": "a spread of broken concrete chunks, gravel and dust with bent rebar fragments, filling the frame",
    "dirt": "packed dust and grime on a floor, scattered small debris, uneven drifts",
    "foam": "shredded upholstery foam and torn fabric scraps matted together, filling the frame",
    "liquid_crust": "stagnant chemical liquid surface with a cracked floating crust of scum and oily swirls",
}
DECALS = {
    "crack": "a single thin jagged branching crack line drawn in black ink",
    "oil_stain": "a dark oil stain puddle with ragged soaked edges",
    "puddle": "a smooth-edged pool of dark liquid with a few satellite drops",
    "blood_smear": "a long drag smear of blood with drips and handprints, as if a body was dragged",
    "rust_streak": "a long vertical rust streak running down from a bolt hole with drips",
    "splatter": "a splatter of droppings, many small splats and drips",
    "scorch": "a burn mark of soot on a floor, dense black core with feathered smoky edges",
    "footprints": "a trail of boot prints in black ink walking diagonally",
}
SEAM_W, FEATHER, DENOISE = 176, 56, 0.85


def even_light(gray, sigma_frac=1 / 10):
    """Divide by a heavy blur (wrap mode, so the correction itself tiles), then stretch to a fixed tonal range."""
    blur = gaussian_filter(gray, gray.shape[1] * sigma_frac, mode="wrap")
    flat = gray / np.maximum(blur, gray.mean() * 1e-3) * gray.mean()
    lo, hi = np.percentile(flat, (1.5, 99.5))
    return np.clip((flat - lo) / max(hi - lo, 1e-3), 0, 1) * 0.85 + 0.1


def seam_mask(w, h):
    """1 on a cross through the center (where the rolled image's old borders meet), feathered to 0."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.minimum(np.abs(xx - w / 2), np.abs(yy - h / 2))
    return np.clip((SEAM_W / 2 + FEATHER - d) / FEATHER, 0, 1)


def _paint(host, recipe, key, subject, seed, tmp, init=None, mask=None):
    wf = comfy.build_workflow(recipe, subject, seed, prefix=f"page-to-poster/{key}", init=init,
                              denoise=DENOISE if init else 1.0)
    if mask:
        comfy.add_mask(wf, mask)
    (path,) = comfy.generate(host, wf, tmp)
    return Image.open(path)


def comfy_texture(host, key, subject, seed, tmp):
    raw = _paint(host, "texture", key, subject, seed, tmp).convert("L")
    # the model vignettes its swatches; drop the outer 10 % before evening the light
    rw, rh = raw.size
    raw = raw.crop((rw // 10, rh // 10, rw - rw // 10, rh - rh // 10)).resize((rw, rh), Image.LANCZOS)
    flat = even_light(np.asarray(raw, dtype=np.float32))
    h, w = flat.shape
    rolled = np.roll(flat, (h // 2, w // 2), axis=(0, 1))
    m = seam_mask(w, h)
    init = comfy.upload(host, Image.fromarray((rolled * 255).astype(np.uint8)), f"p2p_{key}_rolled.png")
    mask = comfy.upload(host, Image.fromarray((np.clip(m * 1.15, 0, 1) * 255).astype(np.uint8)), f"p2p_{key}_mask.png")
    fixed = np.asarray(_paint(host, "texture", key, subject, seed + 1, tmp, init=init, mask=mask)
                       .convert("L").resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    # The repainted cross drifts in tone. Fit it to the untouched area (global gain/offset), then swap its low
    # frequencies for the surroundings' so the band keeps its new detail but not its own brightness.
    keep = m < 0.01
    gain, offset = np.polyfit(fixed[keep], rolled[keep], 1)
    fixed = fixed * gain + offset
    sigma = SEAM_W / 3
    fixed = fixed - gaussian_filter(fixed, sigma, mode="wrap") + gaussian_filter(rolled, sigma, mode="wrap")
    return even_light(rolled * (1 - m) + fixed * m)


def comfy_decal(host, key, subject, seed, tmp):
    g = np.asarray(_paint(host, "decal", key, subject, seed, tmp).convert("L"), dtype=np.float32) / 255
    # some marks come back on toned paper: scale so the paper (median of the outer border) reads as white
    h, w = g.shape
    b = int(w * 0.06)
    border = np.concatenate([g[:b].ravel(), g[-b:].ravel(), g[:, :b].ravel(), g[:, -b:].ravel()])
    g = np.clip(g / max(np.median(border), 0.2), 0, 1)
    alpha = np.clip((0.88 - g) / 0.55, 0, 1)
    edge = int(w * 0.03)  # drop anything touching the frame (borders, cropped marks)
    alpha[:edge], alpha[-edge:], alpha[:, :edge], alpha[:, -edge:] = 0, 0, 0, 0
    ys, xs = np.nonzero(alpha > 0.05)
    if len(xs) == 0:
        raise RuntimeError(f"decal {key}: the generated image has no ink in it; try another --seed")
    pad = 12
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, w)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, h)
    rgba = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
    rgba[..., 3] = (alpha[y0:y1, x0:x1] * 255).astype(np.uint8)
    return rgba


def save_texture(tex_dir, key, arr):
    tex_dir.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    img.save(tex_dir / f"{key}.png")
    prev = tex_dir / "_preview"   # 2x2 repeat, for checking seams by eye
    prev.mkdir(exist_ok=True)
    small = img.resize((512, 512), Image.LANCZOS)
    sheet = Image.new("L", (1024, 1024))
    for x in (0, 512):
        for y in (0, 512):
            sheet.paste(small, (x, y))
    sheet.save(prev / f"{key}_tiled.png")


def make(project, host=None, only=None, force=False, seed=None):
    """Create every missing texture and decal for a project (all of them with force=True). Returns the keys made."""
    project = Path(project)
    extra_path = project / "textures.json"
    extra = json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else {}
    textures = {**TEXTURES, **(extra.get("textures") or {})}
    decals = {**DECALS, **(extra.get("decals") or {})}
    tex_dir, decal_dir = project / "textures", project / "decals"
    man_path = project / "textures" / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    made = []
    jobs = [("texture", key, subject, tex_dir) for key, subject in textures.items()]
    jobs.append(("texture", "hazard_stripes", None, tex_dir))
    jobs += [("decal", key, subject, decal_dir) for key, subject in decals.items()]
    with tempfile.TemporaryDirectory() as tmp:
        for kind, key, subject, folder in jobs:
            if (only and key not in only) or ((folder / f"{key}.png").exists() and not force):
                continue
            s = random.randrange(2**31) if seed is None else seed
            known = key in (proctex.TEXTURE_KEYS if kind == "texture" else proctex.DECAL_KEYS)
            if host and subject:
                source = "comfyui"
                if kind == "texture":
                    save_texture(tex_dir, key, comfy_texture(host, key, subject, s, tmp))
                else:
                    decal_dir.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(comfy_decal(host, key, subject, s, tmp), "RGBA").save(decal_dir / f"{key}.png")
            elif known:
                source = "procedural"
                if kind == "texture":
                    save_texture(tex_dir, key, proctex.texture(key, seed=s))
                else:
                    decal_dir.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(proctex.decal(key, seed=s), "RGBA").save(decal_dir / f"{key}.png")
            else:
                print(f"- {kind} {key}: only ComfyUI can make this one (rerun with --comfy URL); skipped")
                continue
            manifest[key] = {"kind": kind, "source": source, "seed": s, "subject": subject}
            tex_dir.mkdir(parents=True, exist_ok=True)
            man_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            made.append(key)
            print(f"+ {kind} {key} ({source}, seed {s})", flush=True)
    return made
