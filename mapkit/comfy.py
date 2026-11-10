"""Client and recipes for the optional ComfyUI steps (plain Python, standard library only).

The core pipeline never needs this. With a running ComfyUI that has KREA 2 Turbo and the Clio style node,
`p2p.py textures --comfy URL` paints the textures and decals instead of drawing them by code; with the Krea 2
control nodes and the depth Control LoRA as well, `p2p.py paint` paints eye-level room views. docs/comfyui.md lists
what to install and `p2p.py doctor --comfy URL` checks it.

Model file names can be overridden with the environment variables P2P_UNET, P2P_CLIP, P2P_VAE and P2P_DEPTH_LORA.
"""
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MODELS = {
    "unet": os.environ.get("P2P_UNET", "krea2_turbo_fp8_scaled.safetensors"),
    "clip": os.environ.get("P2P_CLIP", "qwen3vl_4b_fp8_scaled.safetensors"),
    "vae": os.environ.get("P2P_VAE", "qwen_image_vae.safetensors"),
}
# The depth Control LoRA is published as depth-control-lora.safetensors; either name is found.
DEPTH_LORAS = tuple(filter(None, (os.environ.get("P2P_DEPTH_LORA"), "krea2_depth_control_lora.safetensors",
                                  "depth-control-lora.safetensors")))
ACCENT = "blood red (#D42A2A)"
DEPTH_STRENGTH = 1.2   # 1.0 follows the model loosely; above about 1.5 the picture breaks into noise

# style: a Clio style name; treatment: the print look, placed after the style prose so it overrides its colors.
RECIPES = {
    # Eye-level room paintings: black ink over a flooded accent color, white highlights, halftone.
    "comic": dict(
        style="Crosshatched Graphic Novel", size=(1216, 832),
        treatment="a strict limited-palette two-ink print on white paper. The only inks are a flat saturated "
        "{accent} and a near-black ink; white paper shows through for highlights. Large areas are flooded with "
        "the flat accent color, black ink linework and shadows are printed over it, and mid-tones are rendered "
        "with coarse halftone dot screentone. No other hues at all: no skin tones, no blue, no green, no "
        "gradients, no painterly blending.",
    ),
    # Map material swatches: flat straight-down ink drawing with grey wash, made tileable by textures.py.
    "texture": dict(
        style="Crosshatched Graphic Novel", size=(1024, 1024),
        treatment="a flat material swatch for a game texture, drawn in black ink with crosshatching and a flat "
        "neutral grey ink wash on white paper. Seen perfectly straight on from directly above, orthographic, the "
        "surface fills the entire frame edge to edge, evenly lit with no cast shadows, no vignette, no perspective, "
        "no horizon, no objects, no border, no text. No color at all, only black, greys and white.",
    ),
    # Map decals: one isolated ink mark on white, cut to alpha by its darkness.
    "decal": dict(
        style="Crosshatched Graphic Novel", size=(1024, 1024),
        treatment="a single isolated black ink mark centered on a pure flat white background, like a stencil or a "
        "sticker, seen straight on from above. Nothing else in the frame, generous white margin all around, no "
        "shadow, no border, no paper texture. Only pure black ink on pure white.",
    ),
}


def _get(host, path, timeout=30):
    return json.load(urllib.request.urlopen(f"{host}{path}", timeout=timeout))


def build_workflow(recipe, subject, seed, accent=ACCENT, prefix="page-to-poster", init=None, denoise=1.0):
    """API-format ComfyUI graph for one image. `subject` says what is in the picture and nothing about the medium
    (no "photo of", "illustration of"): the recipe supplies that. `init` is an uploaded image name for img2img."""
    r = RECIPES[recipe]
    template = ("Style: {name}: {style}. Color and print treatment (overrides every color mentioned above): "
                + r["treatment"].replace("{accent}", accent) + " Subject: {prompt}")
    w, h = r["size"]
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS["unet"], "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["clip"], "type": "krea2"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["vae"]}},
        "11": {"class_type": "ClioStyle", "inputs": {"prompt": subject, "style": r["style"], "template": template}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ["11", 0]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": seed,
            "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": f"{prefix}/{recipe}"}},
    }
    if init:   # img2img: the init image is cropped and scaled to the output size
        g["20"] = {"class_type": "LoadImage", "inputs": {"image": init}}
        g["21"] = {"class_type": "ImageScale", "inputs": {
            "image": ["20", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "center"}}
        g["7"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["4", 0]}}
    return g


def add_mask(graph, mask):
    """Restrict an img2img graph to the white area of the uploaded mask image (inpainting)."""
    graph["30"] = {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}}
    graph["31"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["7", 0], "mask": ["30", 0]}}
    graph["8"]["inputs"]["latent_image"] = ["31", 0]
    return graph


def add_depth_control(graph, depth, lora, strength=DEPTH_STRENGTH):
    """Lock the picture's layout to the uploaded depth map (near is white) with the Krea 2 depth Control LoRA."""
    graph["40"] = {"class_type": "LoadImage", "inputs": {"image": depth}}
    graph["41"] = {"class_type": "Krea2ControlLoRALoader",
                   "inputs": {"model": graph["8"]["inputs"]["model"], "lora_name": lora, "strength": strength}}
    graph["42"] = {"class_type": "Krea2ControlImageEncode",
                   "inputs": {"control_image": ["40", 0], "vae": ["4", 0], "resize": "match_latent_size",
                              "upscale_method": "lanczos", "crop": "center", "channel_mode": "rgb",
                              "normalize": "per_image_minmax", "invert": False, "batch_mode": "independent_images",
                              "latent": ["7", 0]}}
    graph["43"] = {"class_type": "Krea2ControlApply", "inputs": {"model": ["41", 0], "control_latent": ["42", 0]}}
    graph["8"]["inputs"]["model"] = ["43", 0]
    return graph


def upload(host, img, name):
    """Upload a PIL image to ComfyUI's input folder (replacing any image of that name); returns its name there."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            "Content-Type: image/png\r\n\r\n").encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{host}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.load(urllib.request.urlopen(req))["name"]


def generate(host, graph, out_dir):
    """Queue a graph, wait for it, and download its images into out_dir. Returns the saved paths."""
    req = urllib.request.Request(f"{host}/prompt", data=json.dumps({"prompt": graph}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    except urllib.error.HTTPError as e:   # ComfyUI explains a rejected graph in the response body
        raise RuntimeError(f"ComfyUI rejected the workflow: {e.read().decode(errors='replace')[:1500]}") from None
    while True:
        hist = _get(host, f"/history/{pid}")
        if pid in hist:
            status = hist[pid]["status"]
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status["messages"])[:3000])
            if status.get("completed"):
                break
        time.sleep(1)
    saved = []
    for node_out in hist[pid]["outputs"].values():
        for img in node_out.get("images", []):
            q = urllib.parse.urlencode({key: img[key] for key in ("filename", "subfolder", "type")})
            dest = Path(out_dir) / img["filename"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f"{host}/view?{q}", dest)
            saved.append(dest)
    return saved


def _options(info, node, name):
    spec = info[node]["input"]["required"][name]
    return spec[0] if isinstance(spec[0], list) else (spec[1] or {}).get("options", [])


def depth_lora(host):
    """Name of the installed depth Control LoRA, or None."""
    info = _get(host, "/object_info/Krea2ControlLoRALoader")
    if "Krea2ControlLoRALoader" not in info:
        return None
    have = _options(info, "Krea2ControlLoRALoader", "lora_name")
    for want in DEPTH_LORAS:
        for name in have:
            if name.replace("\\", "/").split("/")[-1] == want:
                return name
    return None


def check(host):
    """What this ComfyUI can do for us: {"reachable", "version", "textures", "paint", "missing": [...]}.
    `textures` needs KREA 2 Turbo + Clio; `paint` needs those plus the control nodes and the depth LoRA."""
    out = {"reachable": False, "version": None, "textures": False, "paint": False, "missing": []}
    try:
        out["version"] = _get(host, "/system_stats", timeout=8)["system"].get("comfyui_version")
        info = _get(host, "/object_info", timeout=120)
    except (OSError, ValueError, KeyError) as e:
        out["missing"].append(f"no ComfyUI answering at {host} ({e})")
        return out
    out["reachable"] = True
    base = []
    for node, name, want, folder in (("UNETLoader", "unet_name", MODELS["unet"], "models/diffusion_models"),
                                     ("CLIPLoader", "clip_name", MODELS["clip"], "models/text_encoders"),
                                     ("VAELoader", "vae_name", MODELS["vae"], "models/vae")):
        if want not in _options(info, node, name):
            base.append(f"model {folder}/{want}")
    if "krea2" not in _options(info, "CLIPLoader", "type"):
        base.append("a ComfyUI new enough to load Krea 2 text encoders (CLIPLoader type 'krea2')")
    if "ClioStyle" not in info:
        base.append("custom node clio-style-node (ClioStyle)")
    else:
        styles = _options(info, "ClioStyle", "style")
        base += [f"Clio style {s!r} (update clio-style-node)" for s in sorted({r["style"] for r in RECIPES.values()})
                 if s not in styles]
    out["textures"] = not base
    paint = [f"custom node comfyui-krea2-controlnet ({n})"
             for n in ("Krea2ControlLoRALoader", "Krea2ControlImageEncode", "Krea2ControlApply") if n not in info]
    if not paint and depth_lora(host) is None:
        paint.append(f"model models/loras/{DEPTH_LORAS[-1]}")
    out["paint"] = not base and not paint
    out["missing"] = base + paint
    return out
