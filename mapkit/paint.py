"""Optional: paint eye-level room views in a two-ink comic style, locked to the model by a depth map (ComfyUI).

`p2p.py paint PROJECT --comfy URL [--rooms 1,2] [--count 2] [--strength 1.2]` runs this after
`p2p.py render PROJECT eye`. For each room it reads PROJECT/renders/eye_<id>_depth.png and writes
PROJECT/out/paintings/room_<id>_<seed>.png. The Krea 2 depth Control LoRA holds the room's layout (walls, tanks,
doorways) where the camera saw them, so the painting matches the map.

PROJECT/paint.json says what to paint:
  {"accent": "blood red (#D42A2A)",     optional: the one color besides black, as a name the model understands
   "strength": 1.2,                      optional: how hard the depth map constrains the picture
   "rooms": {"<room id>": "what the room looks like, seen by flashlight from its doorway"}}
Write a subject as a plain description of the place with its key props and light sources, and nothing about the
medium: the recipe supplies the ink style. Rooms without a subject are skipped.

Strength: 1.0 follows the model loosely (props move and multiply), 1.2 is the default, 1.4 is tighter but the
ink gets rougher, and above about 1.5 the picture breaks into noise.
"""
import json
import random
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from . import comfy


def paint(project, host, rooms=None, count=2, strength=None, seed=None):
    project = Path(project)
    cfg_path = project / "paint.json"
    if not cfg_path.exists():
        raise SystemExit(f"paint: {cfg_path} is missing (see docs/comfyui.md for its format)")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    strength = float(cfg.get("strength", comfy.DEPTH_STRENGTH) if strength is None else strength)
    lora = comfy.depth_lora(host)
    if lora is None:
        raise SystemExit("paint: this ComfyUI has no Krea 2 depth Control LoRA; run `p2p.py doctor --comfy URL`")
    out = project / "out" / "paintings"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        for rid, subject in (cfg.get("rooms") or {}).items():
            if rooms and rid not in rooms:
                continue
            depth = project / "renders" / f"eye_{rid}_depth.png"
            if not depth.exists():
                raise SystemExit(f"paint: {depth} is missing; run `p2p.py render {project} eye --rooms {rid}` first")
            name = comfy.upload(host, Image.open(depth), f"p2p_{project.name}_eye_{rid}_depth.png")
            for n in range(count):
                s = random.randrange(2**48) if seed is None else seed + n
                wf = comfy.build_workflow("comic", subject, s, accent=cfg.get("accent", comfy.ACCENT),
                                          prefix=f"page-to-poster/{project.name}")
                comfy.add_depth_control(wf, name, lora, strength)
                (path,) = comfy.generate(host, wf, tmp)
                dest = out / f"room_{rid}_{s}.png"
                shutil.copy(path, dest)
                made.append(dest)
                print(f"+ {dest} (strength {strength:g})", flush=True)
    return made
