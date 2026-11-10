# Setup

The core pipeline needs two things: **Python** and **Blender**. Nothing else: no GPU, no ComfyUI, no Blender
add-on, no MCP server.

| | Needed for | Version |
|---|---|---|
| Python with numpy, scipy, Pillow | everything | 3.10 or newer (developed on 3.14) |
| PyMuPDF | unpacking a PDF adventure into text and page images | any recent |
| Blender | building and rendering the model | developed and tested on 5.2 LTS; 4.2 LTS or newer expected to work |
| A GPU | faster renders | optional; any card Cycles supports |
| ComfyUI | painted textures and room paintings | optional: [comfyui.md](comfyui.md) |

## 1. Python packages

```
pip install -r requirements.txt
```

## 2. Blender

Install the current LTS release from <https://www.blender.org/download/>, or with a package manager:

| System | Command |
|---|---|
| Windows | `winget install BlenderFoundation.Blender` |
| macOS | `brew install --cask blender` |
| Linux | `sudo snap install blender --classic`, or unpack the official tarball into your home folder (`~/blender-5.2.1-linux-x64/`) |

On Linux prefer the official build over a distribution package: distribution builds are often old and may lack
GPU rendering.

`p2p.py` finds Blender in this order: the `BLENDER` environment variable (the full path of the executable), the
`PATH`, then the usual install folders (`C:\Program Files\Blender Foundation\Blender *\`,
`/Applications/Blender*.app/`, `/usr/bin`, `/snap/bin`, `~/blender*/`). If yours is somewhere else:

```
# Windows (PowerShell)
$env:BLENDER = "D:\tools\blender\blender.exe"
# macOS, Linux
export BLENDER=/opt/blender/blender
```

There is nothing to configure inside Blender. Every run starts it headless with factory settings
(`blender -b --factory-startup --python ...`), so your own Blender preferences and add-ons are neither used nor
changed.

## 3. Check

```
python p2p.py doctor
```

```
Python 3.14.7 (C:\Python314\python.exe)
  ok   numpy 2.5.3
  ok   scipy 1.18.1
  ok   PIL 12.3.0
  ok   pymupdf 1.28.2
Blender 5.2.1 LTS (C:/Program Files/Blender Foundation\Blender 5.2\blender.exe)
  render devices: OPTIX: NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, NVIDIA GeForce RTX 5090 | CUDA: NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, NVIDIA GeForce RTX 5090
Fonts: 8 bundled
ComfyUI: not checked (optional; pass --comfy http://127.0.0.1:8188 to check it)

Core pipeline: ready
```

`render devices: none (CPU rendering)` is fine; renders just take longer.

## Rendering device

Each render picks the first GPU backend Blender offers, in the order OptiX, CUDA (NVIDIA), HIP (AMD), Metal
(Apple), oneAPI (Intel), and uses the CPU if there is none. To choose:

```
python p2p.py poster PROJECT --device cpu          # never touch the GPU
python p2p.py poster PROJECT --device cuda         # this backend or fail
python p2p.py poster PROJECT --gpu 5090            # only the cards whose name contains "5090"
```

Times measured for the example in `examples/brine-chapel` (8 rooms, 20,000 triangles), each including all three
poster versions, on a workstation with a 32-core Threadripper 7970X, an RTX 5090 and an RTX PRO 6000:

| | Build with previews | Draft poster, 100 dpi | Final poster, 200 dpi |
|---|---|---|---|
| Both GPUs (OptiX) | 5 s | 26 s | 1 min 7 s |
| CPU only (`--device cpu`) | 5 s | 53 s | 3 min 18 s |

A CPU with fewer cores takes proportionally longer; the draft size keeps it bearable.

## Do I need a Blender MCP server?

No. An MCP server lets an agent drive a running Blender window step by step. This pipeline never does that: the
agent writes `layout.json` and `props.py`, and `p2p.py build` runs a script inside a headless Blender that builds
the whole model from them in one go. That is faster, repeatable (the same files always give the same model), and
works on a machine with no display.

To look at a model yourself, open `PROJECT/build/model.blend` in Blender.

If you want an agent to work in an open Blender window as well, for instance to adjust a finished `model.blend`
by hand and by prompt, the usual server is the third-party [MCP for Blender](https://github.com/ahujasid/blender-mcp)
(formerly `blender-mcp`). Its README gives one setup command that installs the Blender add-on and registers the
server with your MCP clients:

```
uvx mcp-for-blender setup          # needs uv: https://docs.astral.sh/uv/
```

Then restart your agent, open Blender, and check the "MCP for Blender" tab in the 3D viewport's sidebar (`N`).
This pipeline does not use it and was not tested with it; changes made that way live only in the `.blend` file
and are overwritten by the next `p2p.py build`.
