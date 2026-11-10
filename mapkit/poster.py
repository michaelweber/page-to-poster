"""Screenprint-style map poster (plain Python: numpy, scipy, Pillow). `p2p.py poster` runs the render and this.

python -m mapkit.poster PROJECT [--dpi 200] [--variant gm|player|bare|all] [--bleed 0.125]
Reads PROJECT/renders/poster_color|depth|normal.png + poster.json (from render.py), layout.json, build/anchors.json
and PROJECT/poster.json (titles, labels, callouts, palette; docs/poster.md). Writes PROJECT/out/poster_<variant>.png
and .jpg at the size in poster.json ("size_in", default 36 x 24 inches) times --dpi.

The look is a four-ink screenprint: the render's brightness is thresholded into ink or paper through grain and a
line screen, the brightest surfaces print white, strongly red surfaces print in the accent ink, and depth and
normal edges are drawn as ink lines. Labels are caps tags with hard offset shadows.
- gm: everything, including callouts marked "gm_only" and the "gm" text box
- player: no gm_only callouts, the "player" text box and footer
- bare: the artwork alone
"""
import argparse
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation, gaussian_filter, sobel

from . import layout as lay

FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
DEFAULT_PALETTE = {"paper": "#D4DA68", "ink": "#181816", "white": "#F1EFE2", "accent": "#D7262E"}
VARIANTS = ("gm", "player", "bare")
Image.MAX_IMAGE_PIXELS = None   # posters are large on purpose


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


@lru_cache(maxsize=None)
def _font(size, weight, width):
    family = "BarlowCondensed" if width <= 80 else "BarlowSemiCondensed"
    name = "Regular" if weight <= 450 else "Medium" if weight <= 550 else "SemiBold" if weight <= 650 else "Bold"
    return ImageFont.truetype(str(FONT_DIR / f"{family}-{name}.ttf"), size)


def font(size, weight=700, width=75):
    """Bundled Barlow face nearest to a weight (400-700) and width (75 condensed, 90 semi-condensed)."""
    return _font(max(6, int(size)), int(weight), int(width))


def to_px(cam):
    """World (x, y[, z]) -> image pixel for a render's camera sidecar (poster.json, plan.json, ...)."""
    if cam["kind"] == "ortho_top":
        (cx, cy), ppm = cam["center"], cam["ppm"]
        w, h = cam["px"]
        return lambda x, y, z=0.0: (w / 2 + (x - cx) * ppm, h / 2 - (y - cy) * ppm)
    rx, ry, rz = cam["rotation"]
    # rotation matrix for an XYZ Euler: its columns are the camera's right/up/back axes in world space
    cx_, sx_, cy_, sy_, cz_, sz_ = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    right = (cy_ * cz_, cy_ * sz_, -sy_)
    up = (sx_ * sy_ * cz_ - cx_ * sz_, sx_ * sy_ * sz_ + cx_ * cz_, sx_ * cy_)
    lx, ly, lz = cam["location"]
    w, h = cam["px"]
    s = w / cam["ortho_scale"]

    def f(x, y, z=0.0):
        d = (x - lx, y - ly, z - lz)
        return (w / 2 + sum(a * b for a, b in zip(d, right)) * s, h / 2 - sum(a * b for a, b in zip(d, up)) * s)
    return f


def dashed(draw, pts, color, width, dash, gap):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        t = 0.0
        while t < length:
            e = min(t + dash, length)
            draw.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)], fill=color, width=width)
            t = e + gap


# Brightness that prints as solid ink and as bare paper. The render's lighting is fixed (render.py), so these are
# fixed too: a material prints the same on every map (lit concrete floor: stippled paper; tile: white; rust and
# shadow: ink) whatever else is in the scene.
TONE_BLACK, TONE_WHITE = 0.24, 0.83


def screenprint(color, depth, normal, scale, span_m, pal, exposure=1.0):
    """Render passes -> RGB screenprint: ink, paper, white and accent only, with grain and a line screen.
    exposure above 1 prints lighter (less ink), below 1 darker."""
    h, w = depth.shape
    geo = depth > 1e-4
    lum = color @ np.array((0.299, 0.587, 0.114), np.float32)
    tone = np.clip((lum * exposure - TONE_BLACK) / (TONE_WHITE - TONE_BLACK), 0, 1)
    rng = np.random.default_rng(13)
    grain = gaussian_filter(rng.random((h, w)).astype(np.float32), 0.7 * scale)
    grain = (grain - grain.mean()) / grain.std()
    blot = gaussian_filter(rng.random((h, w)).astype(np.float32), 18 * scale)
    blot = (blot - blot.mean()) / blot.std()
    thr = 0.52 + 0.07 * grain + 0.05 * blot
    ink = geo & (tone < thr)
    yy, xx = np.mgrid[0:h, 0:w]
    period = max(3, round(5 * scale))
    screen = ((xx + yy) % period) < max(1, period // 3)
    ink |= geo & (tone < thr + 0.16) & screen
    white = geo & (tone > 0.9 + 0.04 * grain)
    r, g, b = color[..., 0], color[..., 1], color[..., 2]
    red = geo & (r > 0.22) & (r > 1.7 * g) & (r > 1.7 * b)
    # depth steps of more than ~0.4 m between neighbouring pixels are silhouettes (sobel answers 4x a step)
    dz = np.hypot(sobel(depth, 0), sobel(depth, 1)) * span_m / 4
    dn = sum(np.hypot(sobel(normal[..., c], 0), sobel(normal[..., c], 1)) for c in range(3))
    edge = geo & ((dz > 0.4) | (dn > 0.9))
    edge = binary_dilation(edge, iterations=max(1, round(scale)) - 1) if scale >= 2 else edge
    out = np.empty((h, w, 3), np.uint8)
    out[:] = pal["paper"]
    out[white] = pal["white"]
    out[ink] = pal["ink"]
    out[red & ~edge] = pal["accent"]
    # edges print as ink on paper and knock out to paper inside the ink, so shapes survive in dark masses
    rim = edge & ink & ~red
    out[edge & ~rim] = pal["ink"]
    out[rim] = pal["paper"]
    # paper specks in the ink and ink specks on the paper, like a worn print
    speck = rng.random((h, w))
    out[ink & (speck < 0.012)] = pal["paper"]
    out[~geo & (speck < 0.0015)] = pal["ink"]
    return out


def tag_box(draw, xy, text, size, anchor="mm", pad=None):
    """Box (including drop shadow) that shadow_tag would cover, without drawing."""
    pad = pad or int(size * 0.22)
    l, t, r, b = draw.textbbox(xy, text, font=font(size), anchor=anchor)
    off = max(3, int(size * 0.14))
    return l - pad, t - pad, r + pad + off, b + pad + off


def shadow_tag(draw, xy, text, size, fg, bg, shadow, anchor="mm", pad=None):
    """Caps label on a filled box with a hard offset shadow."""
    pad = pad or int(size * 0.22)
    l, t, r, b = draw.textbbox(xy, text, font=font(size), anchor=anchor)
    off = max(3, int(size * 0.14))
    draw.rectangle((l - pad + off, t - pad + off, r + pad + off, b + pad + off), fill=shadow)
    draw.rectangle((l - pad, t - pad, r + pad, b + pad), fill=bg)
    draw.text(xy, text, font=font(size), fill=fg, anchor=anchor)
    return l - pad, t - pad, r + pad + off, b + pad + off


def wrap(draw, text, f, width):
    lines, cur = [], ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if cur and draw.textlength(test, font=f) > width:
            lines.append(cur)
            cur = word
        else:
            cur = test
    return lines + [cur]


def by_variant(value, variant):
    """poster.json lets any text be one string or {"gm": ..., "player": ...}."""
    return value.get(variant) if isinstance(value, dict) and set(value) <= set(VARIANTS) else value


def load_passes(project):
    r = project / "renders"
    color = np.asarray(Image.open(r / "poster_color.png").convert("RGB"), np.float32) / 255
    normal = np.asarray(Image.open(r / "poster_normal.png").convert("RGB"), np.float32) / 255
    depth = np.asarray(Image.open(r / "poster_depth.png"), np.float32) / 65535   # 16-bit keeps sub-meter steps
    return color, depth, normal, json.loads((r / "poster.json").read_text(encoding="utf-8"))


def compose(project, cfg, variant, dpi, passes, art_cache):
    layout = json.loads((project / "layout.json").read_text(encoding="utf-8"))
    anchors = json.loads((project / "build" / "anchors.json").read_text(encoding="utf-8"))
    color, depth, normal, cam = passes
    pal = {key: rgb(val) for key, val in {**DEFAULT_PALETTE, **(cfg.get("palette") or {})}.items()}
    PAPER, INK, WHITE, ACCENT = pal["paper"], pal["ink"], pal["white"], pal["accent"]
    size_in = cfg.get("size_in", [36, 24])
    W, H = round(size_in[0] * dpi), round(size_in[1] * dpi)
    rw, rh = cam["px"]
    if "art" not in art_cache:
        art_cache["art"] = Image.fromarray(screenprint(color, depth, normal, rw / 2400, cam["depth_span_m"], pal,
                                                      float(cfg.get("exposure", 1.0))))
    ax0, ay0, ax1, ay1 = cfg.get("art_box", [0.015, 0.015, 0.985, 0.985])
    fit = min(W * (ax1 - ax0) / rw, H * (ay1 - ay0) / rh)
    art = art_cache["art"].resize((round(rw * fit), round(rh * fit)), Image.LANCZOS)
    ox = round(W * (ax0 + ax1) / 2 - art.width / 2)
    oy = round(H * (ay0 + ay1) / 2 - art.height / 2)
    poster = Image.new("RGB", (W, H), PAPER)
    poster.paste(art, (ox, oy))
    if variant == "bare":
        return poster
    P0 = to_px(cam)

    def P(x, y, z=0.0):
        u_, v_ = P0(x, y, z)
        return ox + u_ * fit, oy + v_ * fit

    d = ImageDraw.Draw(poster)
    u = min(W / 7200, H / 4800) * float(cfg.get("label_scale", 1.0))   # 1 on a 36 x 24 inch poster at 200 dpi
    wall_cut = float((cfg.get("cutaway") or {}).get("wall", 3.2))
    vent_z = lay.sizes(layout)[3]

    taken = []  # boxes already used by labels and marks, for overlap-aware callout placement

    # routes: accent lines along corridors, dashed ducts, door ticks, blockage marks. The player version leaves out
    # links marked "secret" (the artwork still shows the room: docs/poster.md says what that means).
    kinds = set()
    for link in layout.get("links", []):
        if variant == "player" and link.get("secret"):
            continue
        if link["kind"] == "vent":
            pts = [P(x, y, float(link.get("z", vent_z))) for x, y in link["path"]]
            dashed(d, pts, INK, int(16 * u), 46 * u, 26 * u)
            dashed(d, pts, WHITE, int(9 * u), 40 * u, 32 * u)
            kinds.add("vent")
            continue
        pts = [P(x, y, 0.2) for x, y in link["path"]]
        d.line(pts, fill=ACCENT, width=max(2, int(15 * u)), joint="curve")
        kinds.add(link["kind"])
        for end in link.get("doors", ()):
            x, y = link["path"][0] if end == "a" else link["path"][-1]
            px, py = P(x, y, 0.2)
            secret = bool(link.get("secret"))
            kinds.add("secret" if secret else "door")
            r = 20 * u
            d.rectangle((px - r, py - r, px + r, py + r), fill=INK if secret else WHITE, outline=INK, width=int(6 * u))
            if secret:
                d.text((px, py), "S", font=font(34 * u), fill=WHITE, anchor="mm")
            taken.append((px - r, py - r, px + r, py + r))
    mark = cfg.get("blocked_mark", "BLOCKED")
    for blk in anchors["blockages"]:
        taken.append(shadow_tag(d, P(*blk["at"][:2], 0.5), mark, 40 * u, WHITE, ACCENT, INK))
        kinds.add("blocked")

    rooms = {str(r["id"]): r for r in layout["rooms"]}

    def overlap(box):
        return sum(max(0, min(box[2], b[2]) - max(box[0], b[0])) * max(0, min(box[3], b[3]) - max(box[1], b[1]))
                   for b in taken)

    labels, nudge = cfg.get("room_labels") or {}, cfg.get("room_label_offsets") or {}
    room_tags = []
    for rid, r in rooms.items():
        name = (by_variant(labels.get(rid, r["name"]), variant) or "").upper()
        if not name:
            continue
        dx, dy = nudge.get(rid, (0, 0))
        xy = P((r["min"][0] + r["max"][0]) / 2 + dx, (r["min"][1] + r["max"][1]) / 2 + dy, wall_cut)
        text = f"{rid}. {name}" if cfg.get("number_rooms", True) else name
        taken.append(tag_box(d, xy, text, 88 * u))
        room_tags.append((xy, text, 88 * u))
    for e in anchors["entrances"]:
        if not e.get("label"):
            continue
        x, y = P(*e["at"])
        taken.append(shadow_tag(d, (x, y - 70 * u), e["label"].upper(), 46 * u, WHITE, INK, ACCENT))
    # callouts: ink tags on a leader to a dot, placed where they cover the fewest labels, marks and other dots
    feats = anchors["features"]
    sides = {"se": (1, 1), "ne": (1, -1), "sw": (-1, 1), "nw": (-1, -1), "e": (1, 0), "w": (-1, 0), "s": (0, 1),
             "n": (0, -1)}   # on the sheet: n is up. Tried in this order
    callouts = []
    for c in cfg.get("callouts") or []:
        if variant == "player" and c.get("gm_only"):
            continue
        at = c.get("at") or feats.get(c.get("feature"))
        if at is None:
            raise SystemExit(f"poster: callout {c.get('text')!r} names feature {c.get('feature')!r}, which props.py "
                             "never registered with feat(); known features: " + ", ".join(sorted(feats)))
        if c.get("side") is not None and c["side"] not in sides:
            raise SystemExit(f"poster: callout {c.get('text')!r}: side must be one of {', '.join(sides)}")
        px, py = P(*(list(at) + [0])[:3])
        callouts.append((c, px, py))
        taken.append((px - 16 * u, py - 16 * u, px + 16 * u, py + 16 * u))
    for c, px, py in callouts:
        dirs = [sides[c["side"]]] if c.get("side") else list(sides.values())
        best = None
        for reach in (170, 260, 360, 480):
            for dx, dy in dirs:
                tx, ty = px + dx * reach * u, py + dy * reach * 0.7 * u
                anchor = "lm" if dx >= 0 else "rm"
                box = tag_box(d, (tx, ty), c["text"].upper(), 34 * u, anchor=anchor, pad=int(9 * u))
                off_page = box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H
                score = overlap(box) + (1e9 if off_page else 0)
                if best is None or score < best[0]:
                    best = (score, tx, ty, anchor, box)
            if best[0] == 0:
                break
        _, tx, ty, anchor, box = best
        taken.append(box)
        d.line([(px, py), (tx, ty)], fill=INK, width=max(2, int(5 * u)))
        d.ellipse((px - 11 * u, py - 11 * u, px + 11 * u, py + 11 * u), fill=WHITE, outline=INK, width=max(2, int(5 * u)))
        shadow_tag(d, (tx, ty), c["text"].upper(), 34 * u, WHITE, INK, ACCENT, anchor=anchor, pad=int(9 * u))
    for xy, text, size in room_tags:  # room labels go on top of everything
        shadow_tag(d, xy, text, size, WHITE, ACCENT, INK)

    # title block, text box and legend, each in a corner ("tl", "tr", "bl", "br")
    m = int(120 * u)
    corners = {"title": "bl", "box": "tr", "legend": "br", **(cfg.get("corners") or {})}

    def edge(corner):
        return (m if corner[1] == "l" else W - m), (m if corner[0] == "t" else H - m)

    title, sub, foot = (by_variant(cfg.get(key), variant) for key in ("title", "subtitle", "footer"))
    x, y = edge(corners["title"])
    a = "l" if corners["title"][1] == "l" else "r"
    rows = [(foot, font(42 * u, 500, 75), 60 * u), (sub, font(64 * u, 600, 75), 80 * u),
            (title, font(320 * u, 700, 75), 290 * u)]
    if corners["title"][0] == "t":
        y += sum(h for text, _, h in rows if text)
    for text, f, h in rows:   # bottom line first
        if text:
            d.text((x, y), text.upper(), font=f, fill=INK, anchor=f"{a}s")
            y -= h

    box = by_variant(cfg.get("box"), variant)
    if box:
        bw = int(float(box.get("width", 1500)) * u)
        f_b, f_r = font(52 * u, 700), font(46 * u, 450, 90)
        lines = [(t, f_b) for t in wrap(d, box.get("heading", "").upper(), f_b, bw - 80 * u) if t]
        for para in box.get("lines", []):
            lines += [("", f_r)] + [(t, f_r) for t in wrap(d, para, f_r, bw - 80 * u)]
        box_h = int(60 * u + sum(60 * u if t else 20 * u for t, _ in lines))
        cx, cy = edge(corners["box"])
        bx0 = cx if corners["box"][1] == "l" else cx - bw
        by0 = cy if corners["box"][0] == "t" else cy - box_h
        s = 14 * u
        d.rectangle((bx0 + s, by0 + s, bx0 + bw + s, by0 + box_h + s), fill=INK)
        d.rectangle((bx0, by0, bx0 + bw, by0 + box_h), fill=PAPER, outline=INK, width=max(2, int(6 * u)))
        ty = by0 + 34 * u
        for text, f in lines:
            if text:
                d.text((bx0 + 40 * u, ty), text, font=f, fill=INK)
            ty += 60 * u if text else 20 * u

    if cfg.get("legend", True):
        names = cfg.get("legend_labels") or {}
        items = [(k_, names.get(k_, default)) for k_, default in (
            ("open", "OPEN PASSAGE"), ("door", "DOOR"), ("secret", "SECRET DOOR"), ("vent", "VENT / DUCT"),
            ("blocked", "BLOCKED")) if k_ in kinds]
        step = 560 * u
        cx, cy = edge(corners["legend"])
        lx = cx if corners["legend"][1] == "l" else cx - step * len(items) + 120 * u
        ly = cy + 40 * u if corners["legend"][0] == "t" else cy - 110 * u
        for i, (kind, label) in enumerate(items):
            x0 = lx + i * step
            mid = (x0 + 120 * u, ly)
            if kind in ("open", "blocked"):
                d.line([(x0, ly), (x0 + 240 * u, ly)], fill=ACCENT, width=max(2, int(15 * u)))
                if kind == "blocked":
                    shadow_tag(d, mid, mark, 40 * u, WHITE, ACCENT, INK)
            elif kind == "vent":
                dashed(d, [(x0, ly), (x0 + 240 * u, ly)], INK, int(16 * u), 46 * u, 26 * u)
                dashed(d, [(x0, ly), (x0 + 240 * u, ly)], WHITE, int(9 * u), 40 * u, 32 * u)
            else:
                d.line([(x0, ly), (x0 + 240 * u, ly)], fill=ACCENT, width=max(2, int(15 * u)))
                r = 20 * u
                d.rectangle((mid[0] - r, ly - r, mid[0] + r, ly + r), fill=INK if kind == "secret" else WHITE,
                            outline=INK, width=int(6 * u))
                if kind == "secret":
                    d.text(mid, "S", font=font(34 * u), fill=WHITE, anchor="mm")
            d.text((mid[0], ly + 60 * u), label, font=font(44 * u, 700), fill=INK, anchor="mt")
    return poster


def save(poster, path, dpi, bleed, paper):
    """Write PNG + JPEG, padded with `bleed` inches of flat paper (the background is paper, so bleed is trivial).
    The JPEG (quality 93, no chroma subsampling) stays under the upload limits of retail print counters."""
    b = round(bleed * dpi)
    if b:
        padded = Image.new("RGB", (poster.width + 2 * b, poster.height + 2 * b), paper)
        padded.paste(poster, (b, b))
        poster = padded
    path.parent.mkdir(parents=True, exist_ok=True)
    poster.save(path.with_suffix(".png"), dpi=(dpi, dpi))
    poster.save(path.with_suffix(".jpg"), quality=93, subsampling=0, dpi=(dpi, dpi))
    print("+", path.with_suffix(".png"), poster.size, f"{poster.width / dpi:g} x {poster.height / dpi:g} in")


def make(project, dpi=200, variant="all", bleed=0.0):
    project = Path(project)
    cfg_path = project / "poster.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    passes, cache = load_passes(project), {}
    paper = rgb({**DEFAULT_PALETTE, **(cfg.get("palette") or {})}["paper"])
    for v in (VARIANTS if variant == "all" else (variant,)):
        stem = f"poster_{v}" + ("_bleed" if bleed else "")
        save(compose(project, cfg, v, dpi, passes, cache), project / "out" / stem, dpi, bleed, paper)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--variant", choices=VARIANTS + ("all",), default="all")
    ap.add_argument("--bleed", type=float, default=0.0, help="inches of plain paper added on every side")
    a = ap.parse_args()
    make(a.project, a.dpi, a.variant, a.bleed)


if __name__ == "__main__":
    main()
