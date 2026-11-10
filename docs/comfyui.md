# ComfyUI (optional)

The poster needs none of this. ComfyUI adds two things:

| Tier | What you get | What it needs |
|---|---|---|
| 1 | Textures and decals painted in ink by an image model instead of drawn by code (`p2p.py textures --comfy URL`) | ComfyUI, Krea 2 Turbo, the Clio style node |
| 2 | Paintings of each room as you would see it on walking in, locked to the 3D model (`p2p.py paint`) | tier 1, plus the Krea 2 control nodes and the depth Control LoRA |

The difference tier 1 makes on a finished poster is small: the print process reduces every texture to ink and
paper. Tier 2 makes pictures you cannot get otherwise.

## Install

**1. ComfyUI**, a version whose `CLIPLoader` offers the type `krea2` (we used 0.42.0; `doctor` checks this). Install
it from <https://github.com/comfyanonymous/ComfyUI> or <https://docs.comfy.org/installation>. Start it and note its
address, normally `http://127.0.0.1:8188`. If the agent runs on another machine, start ComfyUI with `--listen`.

**2. Models.** Krea 2 Turbo, repackaged by Comfy-Org at <https://huggingface.co/Comfy-Org/Krea-2>. Read the
license in that repository before use. Put three files into ComfyUI's `models` folder, keeping the subfolders:

| File | Size | Goes in |
|---|---|---|
| `diffusion_models/krea2_turbo_fp8_scaled.safetensors` | 13.1 GB | `ComfyUI/models/diffusion_models/` |
| `text_encoders/qwen3vl_4b_fp8_scaled.safetensors` | 5.2 GB | `ComfyUI/models/text_encoders/` |
| `vae/qwen_image_vae.safetensors` | 254 MB | `ComfyUI/models/vae/` |

```
pip install -U huggingface_hub
cd ComfyUI/models
hf download Comfy-Org/Krea-2 diffusion_models/krea2_turbo_fp8_scaled.safetensors text_encoders/qwen3vl_4b_fp8_scaled.safetensors vae/qwen_image_vae.safetensors --local-dir .
```

(`hf` comes with recent versions of `huggingface_hub`; on older ones the same command is `huggingface-cli download`.)

**3. The Clio style node** (MIT license), cloned under the folder name `clio-style-node`:

```
cd ComfyUI/custom_nodes
git clone https://github.com/lumenastrum/clio-style-preview clio-style-node
```

It supplies the "Crosshatched Graphic Novel" style all our recipes start from.

**4. For tier 2 only: the control nodes and the depth LoRA.**

```
cd ComfyUI/custom_nodes
git clone https://github.com/facok/comfyui-krea2-controlnet
cd ../models/loras
hf download Patil/Krea-2-depth-controlnet depth-control-lora.safetensors --local-dir .
```

The LoRA is 862 MB. If a custom node folder has a `requirements.txt`, install it into ComfyUI's Python.

**5. Restart ComfyUI and check:**

```
python p2p.py doctor --comfy http://127.0.0.1:8188
```

```
ComfyUI 0.42.0 at http://127.0.0.1:8188
  textures and decals: ok
  room paintings:      ok
```

Anything missing is listed by file or node name. If your files are named differently, set `P2P_UNET`, `P2P_CLIP`,
`P2P_VAE` or `P2P_DEPTH_LORA` to the names ComfyUI shows. `P2P_COMFY` sets the address so you can leave `--comfy`
off.

We ran ComfyUI on a 96 GB NVIDIA card, where about 21 GB of GPU memory was in use after a run. Smaller cards are
untested here; ComfyUI moves models in and out of memory by itself, at a cost in speed.

## Tier 1: painted textures

```
python p2p.py textures PROJECT --comfy http://127.0.0.1:8188            # make what is missing
python p2p.py textures PROJECT --comfy URL --only stone_wall,scorch --force --seed 11
python p2p.py build PROJECT                                              # the model picks up the new images
```

Each texture is painted as a straight-down ink swatch, has its lighting evened out, and is then made tileable:
the image is rolled by half so its edges meet in the middle, and that seam is painted over with the same prompt.
Look at `PROJECT/textures/_preview/<key>_tiled.png` (the texture repeated 2 x 2); if a seam or an odd shape shows,
remake that one with another `--seed`.

To add or reword subjects for one project, write `PROJECT/textures.json`:

```json
{
 "textures": {"stone_wall": "rough-cut blocks of rock salt, glittering faces, crumbling joints"},
 "decals": {"chalk_mark": "a miner's tally of chalk strokes, five-bar gates"}
}
```

A new decal key such as `chalk_mark` can then be placed with `decal("chalk_mark", x, y, size=1.2)` in `props.py`.
Describe the surface or the mark and nothing about the medium; the recipe adds the ink style.

## Tier 2: room paintings

1. Write `PROJECT/paint.json`: one description per room you want painted.

   ```json
   {
    "accent": "vermilion orange (#E4572E)",
    "strength": 1.2,
    "rooms": {
     "4": "an underground cistern of stone blocks, a square pool of still black brine filling the floor, a narrow plank walkway on trestles crossing the water, an old timber beam pump on the far bank, lit by a single lantern"
    }
   }
   ```

   Describe what is there and how it is lit. Name the big things the model contains (the pool, the walkway, the
   pump), because the painter needs to know what each shape is. Say nothing about style.

2. Render the eye-level views. Each room is seen from its first doorway, 1.7 m up, by flashlight:

   ```
   python p2p.py render PROJECT eye --rooms 4
   ```

   Look at `renders/eye_4.png`. If something stands in the way, move the camera in `poster.json`:
   `"eye": {"4": {"inward": 3.0, "z": 2.4}}` steps 3 m into the room and raises the eye; `"aisle": true` puts the
   camera on the room's center line looking straight down it.

3. Paint:

   ```
   python p2p.py paint PROJECT --comfy URL --rooms 4 --count 2
   ```

   Paintings land in `PROJECT/out/paintings/room_<id>_<seed>.png`, 1216 x 832 pixels.

### Strength

The depth map of the eye-level view steers the painting through a Control LoRA. Its strength decides how firmly:

| Strength | Effect |
|---|---|
| 1.0 | Loose. The room is recognizable, but props drift, multiply or vanish. |
| **1.2** | The default. Walls, doorways and large props stay where the model has them. |
| 1.4 | Tighter still; the ink work gets rougher. |
| above 1.5 | The picture breaks up into noise. |

Set it for one run with `--strength 1.3`, or for the project with `"strength"` in `paint.json`. The built-in
default is `DEPTH_STRENGTH` in `mapkit/comfy.py`.

`"accent"` is the one color besides black, named so an image model understands it: a color word plus a hex code.
