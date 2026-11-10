"""page-to-poster: turn a written adventure location into a 3D model and a print-ready map poster.

    python p2p.py doctor [--comfy URL]             check Python, Blender, the render device and (optionally) ComfyUI
    python p2p.py new PROJECT [--source FILE] [--pages 53-56]
                                                    start a project folder; copy or unpack the adventure into it
    python p2p.py check PROJECT                     validate layout.json and draw build/layout_check.png
    python p2p.py textures PROJECT [--comfy URL] [--only a,b] [--force] [--seed N]
    python p2p.py build PROJECT [--no-preview]      layout.json + props.py -> build/model.blend, anchors.json, previews
    python p2p.py render PROJECT VIEW [--rooms 1,2] [--width N] [--ppm N] [--samples N] [--device D] [--gpu NAME]
                                                    VIEW: closeup, plan, rooms, eye, poster (see mapkit/blender/render.py)
    python p2p.py poster PROJECT [--dpi 200] [--variant gm|player|bare|all] [--bleed IN] [--reuse-render]
                                                    render the cutaway and compose out/poster_<variant>.png and .jpg
    python p2p.py paint PROJECT --comfy URL [--rooms 1,2] [--count 2] [--strength 1.2] [--seed N]
    python p2p.py all PROJECT [--dpi 200]           check, textures (if missing), build, poster

Blender is found through the BLENDER environment variable, the PATH, or its usual install folders. SKILL.md is the
step-by-step procedure; docs/ describes each file format.
"""
import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
TEMPLATES = ROOT / "mapkit" / "templates"
BLENDER_SCRIPTS = ROOT / "mapkit" / "blender"
SHOW = ("[mapkit]", "+ ", "build:", "render:", "Error", "error:", "Traceback")


def find_blender():
    """Path of the Blender executable, or None."""
    env = os.environ.get("BLENDER")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    system = platform.system()
    if system == "Windows":
        hits = sorted(glob.glob("C:/Program Files/Blender Foundation/Blender */blender.exe"), reverse=True)
    elif system == "Darwin":
        hits = glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender")
    else:
        hits = glob.glob("/usr/bin/blender") + glob.glob("/snap/bin/blender") + \
            sorted(glob.glob(str(Path.home() / "blender*" / "blender")), reverse=True)
    return hits[0] if hits else None


def blender(script, *args, log=None):
    """Run one of mapkit/blender/*.py headless. Prints the lines that matter; the full output goes to `log`."""
    exe = find_blender()
    if not exe:
        raise SystemExit("Blender not found. Install it (docs/setup.md) or set the BLENDER environment variable to "
                         "the blender executable.")
    cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(BLENDER_SCRIPTS / script),
           "--", *[str(a) for a in args]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    lines, tail = [], False
    for line in proc.stdout:
        lines.append(line)
        tail = tail or line.startswith("Traceback")
        if tail or line.startswith(SHOW) or "Error:" in line:
            print(line.rstrip(), flush=True)
    code = proc.wait()
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text("".join(lines), encoding="utf-8")
    if code:
        if not tail:
            print("".join(lines[-25:]))
        raise SystemExit(f"Blender exited with code {code}" + (f"; full log: {log}" if log else ""))


def project_dir(path, need=("layout.json",)):
    p = Path(path).resolve()
    for name in need:
        if not (p / name).exists():
            raise SystemExit(f"{p / name} is missing. Start a project with `python p2p.py new {path}`.")
    return p


def poster_cfg(project):
    path = project / "poster.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---------------------------------------------------------------------------------------------------- commands
def cmd_doctor(a):
    ok = True
    print(f"Python {platform.python_version()} ({sys.executable})")
    for mod, why in (("numpy", "required"), ("scipy", "required"), ("PIL", "required: pip install pillow"),
                     ("pymupdf", "optional: unpacks PDF adventures in `p2p.py new`")):
        try:
            m = __import__(mod)
            print(f"  ok   {mod} {getattr(m, '__version__', '')}")
        except ImportError:
            print(f"  {'MISSING' if why.startswith('required') else 'absent '} {mod} ({why})")
            ok = ok and not why.startswith("required")
    if sys.version_info < (3, 10):
        print("  MISSING Python 3.10 or newer")
        ok = False
    exe = find_blender()
    if not exe:
        print("Blender: MISSING (docs/setup.md; or set BLENDER to the executable)")
        ok = False
    else:
        probe = ("import bpy\np = bpy.context.preferences.addons['cycles'].preferences\nfound = []\n"
                 "for b in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):\n"
                 "    try:\n        p.compute_device_type = b\n    except TypeError:\n        continue\n"
                 "    p.refresh_devices()\n    names = [d.name for d in p.devices if d.type == b]\n"
                 "    if names:\n        found.append(b + ': ' + ', '.join(names))\n"
                 "print('P2P_VERSION', bpy.app.version_string)\n"
                 "print('P2P_DEVICES', ' | '.join(found) if found else 'none (CPU rendering)')\n")
        r = subprocess.run([exe, "-b", "--factory-startup", "--python-expr", probe], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        got = {line.split(" ", 1)[0]: line.split(" ", 1)[1] for line in r.stdout.splitlines()
               if line.startswith("P2P_")}
        if "P2P_VERSION" not in got:
            print(f"Blender: found {exe} but it did not run a script:\n{r.stdout[-600:]}{r.stderr[-600:]}")
            ok = False
        else:
            print(f"Blender {got['P2P_VERSION']} ({exe})")
            print(f"  render devices: {got.get('P2P_DEVICES')}")
            if tuple(int(x) for x in got["P2P_VERSION"].split(".")[:2]) < (4, 2):
                print("  WARNING: tested with Blender 5.2; versions before 4.2 are not expected to work")
    fonts = sorted(p.name for p in (ROOT / "fonts").glob("*.ttf"))
    print(f"Fonts: {len(fonts)} bundled" if len(fonts) >= 8 else "Fonts: MISSING files in fonts/")
    ok = ok and len(fonts) >= 8
    host = a.comfy or os.environ.get("P2P_COMFY")
    if host:
        from mapkit import comfy
        c = comfy.check(host)
        if not c["reachable"]:
            print(f"ComfyUI: {c['missing'][0]}")
        else:
            print(f"ComfyUI {c['version']} at {host}")
            print(f"  textures and decals: {'ok' if c['textures'] else 'not available'}")
            print(f"  room paintings:      {'ok' if c['paint'] else 'not available'}")
            for m in c["missing"]:
                print(f"  missing: {m}")
    else:
        print("ComfyUI: not checked (optional; pass --comfy http://127.0.0.1:8188 to check it)")
    print("\nCore pipeline: " + ("ready" if ok else "NOT ready, fix the MISSING items above"))
    return 0 if ok else 1


def parse_pages(spec, count):
    pages = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        pages += range(int(lo), int(hi or lo) + 1)
    bad = [p for p in pages if not 1 <= p <= count]
    if bad:
        raise SystemExit(f"--pages: the document has pages 1-{count}; got {bad}")
    return pages


def cmd_new(a):
    project = Path(a.project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    for name in ("guidelines.md", "brief.md", "layout.json", "props.py", "poster.json"):
        if not (project / name).exists():
            shutil.copy(TEMPLATES / name, project / name)
            print("+", project / name)
    if a.source:
        src = Path(a.source)
        if not src.exists():
            raise SystemExit(f"--source: {src} does not exist")
        out = project / "source"
        out.mkdir(exist_ok=True)
        if src.suffix.lower() == ".pdf":
            try:
                import pymupdf
            except ImportError:
                raise SystemExit("--source with a PDF needs pymupdf (pip install pymupdf), or read the PDF yourself "
                                 "and skip --source") from None
            doc = pymupdf.open(src)
            pages = parse_pages(a.pages, doc.page_count) if a.pages else list(range(1, doc.page_count + 1))
            if len(pages) > 40:
                raise SystemExit(f"{len(pages)} pages is a whole book: pass --pages with the pages of the location "
                                 "you want mapped, such as --pages 53-56")
            for n in pages:
                page = doc[n - 1]
                (out / f"page_{n:03d}.txt").write_text(page.get_text(), encoding="utf-8")
                page.get_pixmap(dpi=130).save(out / f"page_{n:03d}.png")
            print(f"+ {out} ({len(pages)} pages as .txt and .png; look at the .png files for maps and diagrams)")
        else:
            shutil.copy(src, out / src.name)
            print("+", out / src.name)
    print(f"\nNext: read the source, fill in {project / 'guidelines.md'} and {project / 'brief.md'}, then write "
          "layout.json (SKILL.md, step 2).")


def cmd_check(a):
    from mapkit import layout as lay
    project = project_dir(a.project)
    layout = json.loads((project / "layout.json").read_text(encoding="utf-8"))
    problems = lay.validate(layout)
    for p in problems:
        print(f"{p.level.upper()}: {p.text}")
    try:
        lay.draw(layout, project / "build" / "layout_check.png", problems)
        print("+", project / "build" / "layout_check.png")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        print(f"could not draw the layout until the errors above are fixed ({type(e).__name__}: {e})")
    errors = sum(p.level == "error" for p in problems)
    rooms = len(layout.get("rooms") or [])
    print(f"{rooms} rooms, {len(layout.get('links') or [])} links: {errors} error(s), {len(problems) - errors} warning(s)")
    return 1 if errors else 0


def cmd_textures(a):
    from mapkit import textures
    project = project_dir(a.project)
    made = textures.make(project, host=a.comfy, only=set(a.only.split(",")) if a.only else None, force=a.force,
                         seed=a.seed)
    print(f"{len(made)} made; textures in {project / 'textures'}, decals in {project / 'decals'}")


def cmd_build(a):
    project = project_dir(a.project)
    if not (project / "textures").exists():
        print("note: no textures yet, so surfaces will be flat grey; run `p2p.py textures` first for the full look")
    blender("build.py", project, *([] if a.no_preview else ["--preview"]), log=project / "build" / "build.log")
    anchors = json.loads((project / "build" / "anchors.json").read_text(encoding="utf-8"))
    print(f"features for callouts: {', '.join(sorted(anchors['features'])) or '(none: props.py called feat() nowhere)'}")


def cmd_render(a):
    project = project_dir(a.project, need=("layout.json", "build/model.blend"))
    args = [project, a.view]
    for key in ("rooms", "width", "ppm", "samples", "device", "gpu"):
        if getattr(a, key) is not None:
            args += [f"--{key}", getattr(a, key)]
    blender("render.py", *args, log=project / "build" / f"render_{a.view}.log")


def cmd_poster(a):
    from mapkit import poster
    project = project_dir(a.project, need=("layout.json", "build/model.blend"))
    cfg = poster_cfg(project)
    x0, _, x1, _ = cfg.get("art_box", [0.015, 0.015, 0.985, 0.985])
    width = round(cfg.get("size_in", [36, 24])[0] * a.dpi * (x1 - x0))
    have = project / "renders" / "poster.json"
    if a.reuse_render and have.exists():
        print(f"reusing the render in {project / 'renders'}")
    else:
        args = [project, "poster", "--width", width]
        for key in ("samples", "device", "gpu"):
            if getattr(a, key) is not None:
                args += [f"--{key}", getattr(a, key)]
        blender("render.py", *args, log=project / "build" / "render_poster.log")
    poster.make(project, a.dpi, a.variant, a.bleed)


def cmd_paint(a):
    from mapkit import paint
    project = project_dir(a.project, need=("layout.json", "paint.json"))
    host = a.comfy or os.environ.get("P2P_COMFY")
    if not host:
        raise SystemExit("paint needs --comfy URL (or the P2P_COMFY environment variable)")
    made = paint.paint(project, host, rooms=set(a.rooms.split(",")) if a.rooms else None, count=a.count,
                       strength=a.strength, seed=a.seed)
    print(f"{len(made)} painting(s) in {project / 'out' / 'paintings'}")


def cmd_all(a):
    if cmd_check(a):
        raise SystemExit("fix the layout errors first")
    project = project_dir(a.project)
    if not (project / "textures").exists():
        cmd_textures(argparse.Namespace(project=a.project, comfy=None, only=None, force=False, seed=None))
    cmd_build(argparse.Namespace(project=a.project, no_preview=False))
    cmd_poster(argparse.Namespace(project=a.project, dpi=a.dpi, variant="all", bleed=0.0, reuse_render=False,
                                  samples=None, device=None, gpu=None))


def main():
    ap = argparse.ArgumentParser(prog="p2p.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, project=True):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if project:
            p.add_argument("project")
        return p

    def render_opts(p):
        p.add_argument("--samples")
        p.add_argument("--device", help="auto (default), cpu, optix, cuda, hip, metal or oneapi")
        p.add_argument("--gpu", help="part of a device name, when several GPUs are installed")

    add("doctor", cmd_doctor, project=False).add_argument("--comfy")
    p = add("new", cmd_new)
    p.add_argument("--source", help="the adventure: a PDF, text or Markdown file")
    p.add_argument("--pages", help="PDF pages to unpack, such as 53-56 or 12,14-15")
    add("check", cmd_check)
    p = add("textures", cmd_textures)
    p.add_argument("--comfy", help="ComfyUI URL; without it textures are drawn by code")
    p.add_argument("--only", help="comma-separated keys")
    p.add_argument("--force", action="store_true", help="remake images that already exist")
    p.add_argument("--seed", type=int)
    add("build", cmd_build).add_argument("--no-preview", action="store_true")
    p = add("render", cmd_render)
    p.add_argument("view", choices=("poster", "closeup", "plan", "rooms", "eye"))
    p.add_argument("--rooms")
    p.add_argument("--width")
    p.add_argument("--ppm")
    render_opts(p)
    p = add("poster", cmd_poster)
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--variant", choices=("gm", "player", "bare", "all"), default="all")
    p.add_argument("--bleed", type=float, default=0.0)
    p.add_argument("--reuse-render", action="store_true", help="only redo the labels; keep the last render")
    render_opts(p)
    p = add("paint", cmd_paint)
    p.add_argument("--comfy")
    p.add_argument("--rooms")
    p.add_argument("--count", type=int, default=2)
    p.add_argument("--strength", type=float, help="depth LoRA strength (default 1.2, or paint.json's)")
    p.add_argument("--seed", type=int)
    add("all", cmd_all).add_argument("--dpi", type=int, default=200)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
