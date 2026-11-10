"""Procedural "ink drawing" textures and decals, made with numpy/scipy/Pillow only.

These stand in for the AI-generated images used by the Blender stage when no image
generator is available.  Every texture is a grayscale height-free tone map in
[0.1, 0.95] that tiles seamlessly; every decal is an RGBA sticker whose alpha is
ink coverage.

Approach
--------
Everything is built in *tile units* (0..1 across the image) from a few periodic
building blocks, so seamless tiling is a property of the blocks rather than
something patched afterwards:

* ``_noise``      fractal noise made by band-pass filtering white noise in the FFT
                  domain.  The FFT is circular, so the result wraps exactly;
                  anisotropic filters give streaks and grain.
* ``_cells``      Voronoi cells on a torus (``cKDTree(boxsize=1)``), returning cell
                  ids and the exact distance to the nearest cell border, used for
                  flagstones, rubble, crust plates and cracks.
* ``_Ink``        Pillow drawing that repeats each primitive across tile borders,
                  for line work: cracks, rivets, fibres, nails, pebbles.
* ``_relief``     lighting of a height field with wrapped finite differences, which
                  gives the grey wash; ``_contours`` and ``_hatch`` turn fields into
                  antialiased ink lines (tide marks, wood grain, engraving hatching).
* regular patterns (boards, tiles, ribs, bars, stripes) use integer frequencies per
  tile, so their periods divide the tile at any size.

Each texture recipe composes these into tone, runs it through ``_finish`` (a
percentile stretch into [0.1, 0.95] and a gamma that sets the mean brightness), and
scales every pixel measure by ``size / 1024`` so features keep their real-world size.
Decals are drawn on a free canvas with the same blocks and cropped to the ink.
Seeds go through ``numpy.random.default_rng``; each key gets its own stream.

Review sheets: ``python -m mapkit.proctex --sheet OUTDIR``.
"""

from __future__ import annotations

import argparse
import functools
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.spatial import cKDTree

TAU = 2.0 * np.pi


# --------------------------------------------------------------------------- blocks


def _rng(key: str, seed: int) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(key.encode())])


@functools.lru_cache(maxsize=4)
def _uv(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Read-only pixel grids in tile units: u across (x), v down (y)."""
    v, u = np.mgrid[0:n, 0:n].astype(np.float32) / np.float32(n)
    u.setflags(write=False)
    v.setflags(write=False)
    return u, v


@functools.lru_cache(maxsize=64)
def _band_filter(n: int, cycles: float, octaves: int, gain: float, stretch: tuple[float, float]) -> np.ndarray:
    fy = (np.fft.fftfreq(n) * n)[:, None]
    fx = (np.fft.rfftfreq(n) * n)[None, :]
    r = np.hypot(fx / stretch[0], fy / stretch[1])
    filt = np.zeros_like(r)
    for o in range(octaves):
        f0 = cycles * 2.0**o
        band = np.exp(-0.5 * ((r - f0) / (0.5 * f0)) ** 2)
        band[0, 0] = 0.0
        filt += gain**o * band / np.sqrt((band**2).sum())
    return filt


def _noise(rng, n, cycles, octaves=4, gain=0.5, stretch=(1.0, 1.0)) -> np.ndarray:
    """Periodic fractal noise, zero mean and unit std.

    ``cycles`` is the feature count across the tile for the coarsest octave; each
    octave doubles it.  ``stretch=(sx, sy)`` multiplies the feature count along x / y,
    e.g. ``(8, 1)`` gives vertical streaks.
    """
    white = np.fft.rfft2(rng.standard_normal((n, n), dtype=np.float32))
    out = np.fft.irfft2(white * _band_filter(n, float(cycles), octaves, gain, tuple(stretch)), s=(n, n))
    out = out.astype(np.float32)
    out -= out.mean()
    out /= out.std() + 1e-8
    return out


def _smooth(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _blur(a, sigma):
    return ndimage.gaussian_filter(a, sigma, mode="wrap")


def _grad(f):
    gx = (np.roll(f, -1, 1) - np.roll(f, 1, 1)) * 0.5
    gy = (np.roll(f, -1, 0) - np.roll(f, 1, 0)) * 0.5
    return gx, gy


def _relief(height, strength=1.0):
    """Lambert shading of a height field (in pixels), lit from the upper left; flat = 1."""
    gx, gy = _grad(height * strength)
    lz = 1.2
    shade = (gx + gy + lz) / np.sqrt(gx * gx + gy * gy + 1.0)
    return shade / lz


def _contours(field, width, spacing=None):
    """Antialiased ink lines ``width`` px wide along the zero level (or every ``spacing``) of a field."""
    gx, gy = _grad(field)
    g = np.sqrt(gx * gx + gy * gy) + 1e-6
    if spacing is None:
        d = np.abs(field) / g
    else:
        d = np.abs((field / spacing + 0.5) % 1.0 - 0.5) * spacing / g
    return np.clip(width * 0.5 - d + 0.5, 0.0, 1.0)


def _hatch(n, cover, fx, fy, wobble=0.0):
    """Parallel ink lines with integer frequency (fx, fy) per tile; ``cover`` is line width / spacing."""
    u, v = _uv(n)
    period = n / np.hypot(fx, fy)
    d = np.abs((fx * u + fy * v + wobble + 0.5) % 1.0 - 0.5) * period
    return np.clip(cover * period * 0.5 - d + 0.5, 0.0, 1.0)


def _jittered(rng, gx, gy, jitter):
    """One point per cell of a gx x gy grid, displaced by up to ``jitter`` cells."""
    j, i = np.mgrid[0:gy, 0:gx]
    x = (i + 0.5 + jitter * (rng.random(i.shape) - 0.5)) / gx
    y = (j + 0.5 + jitter * (rng.random(i.shape) - 0.5)) / gy
    return np.stack([x.ravel(), y.ravel()], 1)


def _cells(points, n, warp=None):
    """Voronoi on the torus.  Returns (cell id, distance in px to the nearest cell border)."""
    pts = np.mod(np.asarray(points, np.float64), 1.0)
    pts[pts >= 1.0] = 0.0
    u, v = _uv(n)
    qx, qy = (u, v) if warp is None else (u + warp[0], v + warp[1])
    q = np.stack([qx.ravel(), qy.ravel()], 1).astype(np.float64) % 1.0
    q[q >= 1.0] = 0.0
    d, i = cKDTree(pts, boxsize=1.0).query(q, k=2)
    dp = pts[i[:, 1]] - pts[i[:, 0]]
    dp -= np.round(dp)
    edge = (d[:, 1] ** 2 - d[:, 0] ** 2) / (2.0 * np.hypot(dp[:, 0], dp[:, 1]) + 1e-12)
    return i[:, 0].reshape(n, n), (edge * n).astype(np.float32).reshape(n, n)


def _warp(rng, n, amount, cycles):
    return amount * _noise(rng, n, cycles, 3), amount * _noise(rng, n, cycles, 3)


def _resample(field, dx, dy):
    """Sample a periodic field at pixel offsets (dx, dy), wrapping around."""
    n = field.shape[0]
    u, v = _uv(n)
    return ndimage.map_coordinates(field, [v * n + dy, u * n + dx], order=1, mode="grid-wrap").astype(np.float32)


class _Ink:
    """Float canvas for Pillow line work.  With ``wrap`` every primitive is repeated
    across the tile borders, so drawings tile.  Fill -1 marks unpainted pixels for ``over``."""

    def __init__(self, n, fill=0.0, wrap=True):
        self.n, self.wrap = n, wrap
        self.img = Image.new("F", (n, n), float(fill))
        self.draw = ImageDraw.Draw(self.img)

    def _copies(self, pts, pad):
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        if not self.wrap:
            return [pts]
        lo = np.floor((pts.min(0) - pad) / self.n).astype(int)
        hi = np.floor((pts.max(0) + pad) / self.n).astype(int)
        return [
            pts - (kx * self.n, ky * self.n)
            for kx in range(lo[0], hi[0] + 1)
            for ky in range(lo[1], hi[1] + 1)
        ]

    def line(self, pts, width, fill):
        w = max(1, int(round(width)))
        for p in self._copies(pts, w):
            self.draw.line(p.ravel().tolist(), fill=float(fill), width=w, joint="curve")

    def taper(self, pts, w0, w1, fill):
        """Polyline whose width goes linearly from w0 to w1."""
        k = len(pts) - 1
        for j in range(k):
            w = w0 + (w1 - w0) * j / max(k - 1, 1)
            self.line(pts[j : j + 2], w, fill)
            if w > 2.5:
                self.ellipse(pts[j + 1], w / 2, w / 2, fill)

    def ellipse(self, c, rx, ry, fill, outline=None, width=1):
        for p in self._copies([c], max(rx, ry) + width):
            x, y = p[0]
            self.draw.ellipse(
                [x - rx, y - ry, x + rx, y + ry],
                fill=None if fill is None else float(fill),
                outline=None if outline is None else float(outline),
                width=max(1, int(round(width))),
            )

    def polygon(self, pts, fill, outline=None, width=1):
        for p in self._copies(pts, width):
            self.draw.polygon(
                p.ravel().tolist(),
                fill=float(fill),
                outline=None if outline is None else float(outline),
                width=max(1, int(round(width))),
            )

    def array(self):
        return np.array(self.img, dtype=np.float32)

    def over(self, base):
        a = self.array()
        return np.where(a >= 0.0, a, base)


def _walk(rng, start, angle, steps, step, jag, pull=0.25):
    """Jagged random-walk polyline that keeps drifting back towards ``angle``."""
    pts = [np.asarray(start, np.float64)]
    a = angle
    for _ in range(steps):
        a += rng.normal(0.0, jag) + pull * (angle - a)
        pts.append(pts[-1] + step * np.array([np.cos(a), np.sin(a)]))
    return np.array(pts)


def _crack(ink, rng, start, angle, length, width, fill, step, depth=2):
    """Tapered jagged crack with recursive side branches."""
    steps = max(2, int(length / step))
    pts = _walk(rng, start, angle, steps, step, 0.45, 0.4)
    ink.taper(pts, width, max(1.0, width * 0.3), fill)
    if depth > 0:
        for _ in range(rng.integers(2, 5)):
            j = int(rng.integers(1, steps))
            side = rng.choice((-1.0, 1.0))
            _crack(
                ink, rng, pts[j], angle + side * rng.uniform(0.5, 1.1), length * rng.uniform(0.2, 0.45),
                max(1.0, width * (1.0 - 0.7 * j / steps) * 0.75), fill, step, depth - 1,
            )


def _blob(rng, c, r, k=14, rough=0.4):
    """Irregular closed polygon around c with mean radius r."""
    a = np.sort(rng.uniform(0, TAU, k))
    rr = r * (1.0 - rough / 2 + rough * rng.random(k))
    return np.stack([c[0] + rr * np.cos(a), c[1] + rr * np.sin(a)], 1)


def _finish(img, mean):
    """Stretch the 0.1-99.9 percentile range onto [0.1, 0.95] and gamma it to ``mean``.

    The extremes are set by the line work, so recipes keep their own tonal balance."""
    sample = img[::2, ::2]
    lo, hi = np.percentile(sample, (0.1, 99.9))
    t = np.clip((img - lo) / (hi - lo + 1e-8), 0.0, 1.0)
    s = t[::4, ::4]
    target = (mean - 0.1) / 0.85
    a, b = -3.0, 3.0
    for _ in range(24):
        m = 0.5 * (a + b)
        if (s ** np.exp(m)).mean() > target:
            a = m
        else:
            b = m
    return (0.1 + 0.85 * t ** np.float32(np.exp(0.5 * (a + b)))).astype(np.float32)


def _dome_field(n, centers, radius):
    """Sum of hemispherical bumps (height in px) at wrapped pixel centers, via FFT convolution."""
    imp = np.zeros((n, n), np.float32)
    c = np.round(np.asarray(centers)).astype(int) % n
    np.add.at(imp, (c[:, 1], c[:, 0]), 1.0)
    d = np.minimum(np.arange(n), n - np.arange(n)).astype(np.float32)
    r = np.hypot(d[None, :], d[:, None]) / radius
    kern = radius * np.sqrt(np.clip(1.0 - r * r, 0.0, None))
    return np.fft.irfft2(np.fft.rfft2(imp) * np.fft.rfft2(kern), s=(n, n)).astype(np.float32)


# ------------------------------------------------------------------------- textures


def _concrete_floor(rng, n):  # 4 m
    s = n / 1024
    img = 0.7 + 0.03 * _noise(rng, n, 3, 5, 0.55) + 0.015 * _noise(rng, n, 40, 3)
    stain = _noise(rng, n, 2, 3, 0.5) + 0.25 * _noise(rng, n, 12, 2)
    img -= 0.14 * _smooth(stain, 1.1, 1.8)
    img -= 0.3 * _contours(stain - 1.1, 1.6 * s)
    img -= 0.15 * _hatch(n, 0.35 * _smooth(stain, 1.3, 2.2), 90, 90, 0.2 * stain)
    img += 0.08 * _blur(rng.standard_normal((n, n), dtype=np.float32), 0.8 * s)
    ink = _Ink(n)
    for _ in range(int(1600 * s * s)):
        r = rng.uniform(0.6, 2.2) * s
        ink.ellipse(rng.random(2) * n, r, r * rng.uniform(0.6, 1.0), 0.6)
    for _ in range(4):
        _crack(ink, rng, rng.random(2) * n, rng.uniform(0, TAU), rng.uniform(0.35, 0.7) * n,
               rng.uniform(2.2, 3.4) * s, 1.0, 7 * s, depth=2)
    return img * (1.0 - 0.85 * _blur(ink.array(), 0.5 * s))


def _concrete_wall(rng, n):  # 4 m, 12.5 cm boards
    s = n / 1024
    u, v = _uv(n)
    boards = 32
    yb = v * boards + 0.5 + 0.03 * _noise(rng, n, 3, 3)
    row = np.floor(yb).astype(int) % boards
    f = yb - np.floor(yb)
    img = (0.68 + rng.normal(0, 0.035, boards).astype(np.float32)[row]
           + 0.04 * _noise(rng, n, 2, 4) + 0.025 * _noise(rng, n, 6, 4, 0.6, stretch=(1, 8)))
    d = np.minimum(f, 1.0 - f) * n / boards
    img -= 0.4 * np.clip(1.3 * s - d + 0.5, 0, 1)
    img += 0.05 * np.exp(-d / (3 * s)) * (f < 0.5)
    butt_x = rng.random(boards).astype(np.float32)[row]
    has_butt = (rng.random(boards) < 0.6)[row]
    dbx = np.abs((u - butt_x + 0.5) % 1.0 - 0.5) * n
    img -= 0.3 * np.clip(1.1 * s - dbx + 0.5, 0, 1) * has_butt
    holes, drips = _Ink(n), _Ink(n)
    cols, rows = 6, 4
    for i in range(cols):
        for j in range(rows):
            x = (i + 0.5 + rng.normal(0, 0.06)) / cols * n
            y = (np.floor((j + 0.5) / rows * boards) + rng.integers(-1, 2)) / boards * n
            holes.ellipse((x, y), 11 * s, 11 * s, 0.3)
            holes.ellipse((x, y), 6 * s, 6 * s, 1.0)
            length = rng.uniform(0.04, 0.2) * n
            path = np.stack([x + rng.normal(0, 2, 6).cumsum() * s, y + np.linspace(0, length, 6)], 1)
            drips.taper(path, rng.uniform(6, 12) * s, 2 * s, rng.uniform(0.4, 1.0))
    streak = _noise(rng, n, 4, 4, 0.6, stretch=(10, 1))
    img -= 0.2 * _blur(drips.array(), 3 * s) * (0.7 + 0.3 * streak)
    img -= 0.12 * _smooth(streak, 0.8, 2.2) * _smooth(_noise(rng, n, 2, 3), 0.0, 1.0)
    img += 0.08 * _blur(rng.standard_normal((n, n), dtype=np.float32), 0.8 * s)
    return img * (1.0 - 0.8 * _blur(holes.array(), 0.5 * s))


def _stone_floor(rng, n):  # 3 m, ~50 cm flags
    s = n / 1024
    pts = _jittered(rng, 6, 6, 0.8)
    idx, edge = _cells(pts, n, _warp(rng, n, 0.012, 6))
    inside = edge + 1.5 * s * _noise(rng, n, 40, 2) - 3.5 * s
    h = np.clip(inside / (10 * s), 0, 1) ** 0.6 + 0.05 * _noise(rng, n, 12, 4)
    tone = (0.62 + rng.normal(0, 0.07, len(pts)).astype(np.float32)[idx]
            + 0.06 * _noise(rng, n, 5, 5) + 0.03 * _noise(rng, n, 50, 2))
    img = tone * _relief(h, 10 * s)
    worn = _noise(rng, n, 3, 3)
    img += 0.06 * _smooth(worn, 0.5, 1.5)
    joint = _smooth(-inside, -s, s)
    img = img * (1 - joint) + (0.16 + 0.05 * _noise(rng, n, 30, 2)) * joint
    img -= 0.3 * np.clip(1.3 * s - np.abs(inside) + 0.5, 0, 1)
    ink = _Ink(n)
    for _ in range(int(500 * s * s)):
        r = rng.uniform(0.8, 2.8) * s
        ink.ellipse(rng.random(2) * n, r, r, 0.5)
    for _ in range(10):
        _crack(ink, rng, rng.random(2) * n, rng.uniform(0, TAU), rng.uniform(0.06, 0.16) * n,
               1.8 * s, 1.0, 6 * s, depth=1)
    return img * (1.0 - 0.7 * _blur(ink.array(), 0.5 * s) * (1 - joint))


def _stone_wall(rng, n):  # 3 m, 8 courses of 37.5 cm
    s = n / 1024
    u, v = _uv(n)
    courses = 8
    cv = v * courses + 0.5
    course = np.floor(cv).astype(int) % courses
    fv = cv % 1.0
    dy = np.minimum(fv, 1.0 - fv) * n / courses
    dx = np.empty_like(dy)
    block = np.empty(dy.shape, int)
    x = u[0]
    for c in range(courses):
        m = int(rng.integers(4, 7))
        lengths = rng.uniform(0.7, 1.3, m)
        breaks = np.sort((rng.random() + np.cumsum(lengths / lengths.sum())) % 1.0)
        bb = np.concatenate([breaks - 1.0, breaks, breaks + 1.0])
        j = np.searchsorted(bb, x)
        rows = course[:, 0] == c
        dx[rows] = (np.minimum(x - bb[j - 1], bb[j] - x) * n)[None, :]
        block[rows] = (c * 16 + j % m)[None, :]
    r = 6 * s
    e = np.where((dx < r) & (dy < r), r - np.hypot(r - dx, r - dy), np.minimum(dx, dy))
    e += 2.0 * s * _noise(rng, n, 30, 3) - 12 * s * np.clip(_noise(rng, n, 14, 2) - 1.5, 0, None)
    inside = e - 4 * s
    tone = (0.6 + rng.normal(0, 0.06, courses * 16).astype(np.float32)[block]
            + 0.05 * _noise(rng, n, 6, 5) + 0.03 * _noise(rng, n, 40, 2))
    h = np.clip(inside / (12 * s), 0, 1) ** 0.5 + 0.04 * _noise(rng, n, 16, 4)
    img = tone * _relief(h, 10 * s)
    joint = _smooth(-inside, -s, s)
    img = img * (1 - joint) + (0.22 + 0.05 * _noise(rng, n, 30, 2)) * joint
    return img - 0.3 * np.clip(1.4 * s - np.abs(inside) + 0.5, 0, 1)


def _rock(rng, n):  # 4 m
    s = n / 1024
    u, v = _uv(n)
    phase = u + 7 * v + 0.1 * _noise(rng, n, 2, 1)
    bed = phase % 1.0
    idx, edge = _cells(rng.random((14, 2)), n, _warp(rng, n, 0.006, 4))
    edge += 2.5 * s * _noise(rng, n, 40, 2)
    fbm = _noise(rng, n, 4, 6, 0.6)
    ridge = 1.0 - np.abs(_noise(rng, n, 8, 4, 0.5))
    height = (22 * s * bed ** 1.5 + 10 * s * fbm + 5 * s * ridge
              - 8 * s * np.exp(-edge / (5 * s)) + 2 * s * (idx % 3))
    shade = _relief(height, 1.0)
    img = 0.6 * shade + 0.05 * _noise(rng, n, 3, 3)
    img -= 0.3 * _hatch(n, np.clip((0.95 - shade) * 1.3, 0, 0.5), 90, 90, 0.3 * fbm)
    img -= 0.35 * _contours(phase, 1.5 * s, spacing=1.0)
    img -= 0.5 * np.clip(1.6 * s - edge + 0.5, 0, 1)
    ink = _Ink(n)
    for _ in range(6):
        _crack(ink, rng, rng.random(2) * n, rng.uniform(0, TAU), rng.uniform(0.1, 0.25) * n,
               2.0 * s, 1.0, 7 * s, depth=1)
    return img * (1.0 - 0.6 * _blur(ink.array(), 0.5 * s))


def _rust_metal(rng, n):  # 2 m: plates 1 m x 1 m, staggered, lap-riveted seams
    s = n / 1024
    u, v = _uv(n)
    prow = np.floor(v * 2 - 0.5).astype(int) % 2
    xs = (u * 2 + 0.5 * prow - 0.5) % 1.0
    ys = (v * 2 - 0.5) % 1.0
    seam_d = np.minimum(np.minimum(xs, 1 - xs), np.minimum(ys, 1 - ys)) * n / 2
    off, pitch = 18 * s, n / 32
    centers = []
    for row in range(2):
        y_seam = (row + 0.5) * n / 2
        for k in range(32):
            centers += [((k + 0.5) * pitch, y_seam - off), ((k + 0.5) * pitch, y_seam + off)]
        for col in range(2):
            x_seam = (col + 0.5 - 0.5 * row) * n / 2
            for k in range(2, 15):
                y = y_seam + k * pitch
                centers += [(x_seam - off, y), (x_seam + off, y)]
    rivets = _dome_field(n, centers, 7.5 * s)
    rust_f = (_noise(rng, n, 3, 5, 0.6) + 0.35 * _noise(rng, n, 20, 3)
              + 1.2 * np.exp(-seam_d / (40 * s)) - 0.4)
    rust = _smooth(rust_f, 0.5, 0.6)
    rough = _noise(rng, n, 60, 2)
    height = 3 * s * _smooth(seam_d, 0, 3 * s) + rivets + rust * (1.2 * s * rough - 1 * s)
    paint = 0.7 + 0.04 * _noise(rng, n, 4, 4) + 0.025 * _noise(rng, n, 6, 3, stretch=(10, 1))
    corrosion = 0.42 + 0.08 * _noise(rng, n, 12, 3) + 0.05 * _noise(rng, n, 4, 3)
    img = (paint * (1 - rust) + corrosion * rust) * _relief(height, 1.0)
    img -= 0.4 * _contours(rust_f - 0.5, 1.6 * s)
    img += 0.1 * _smooth(rust_f, 0.35, 0.5) * (1 - rust)
    img -= 0.18 * _hatch(n, 0.3 * rust, 128, -128, 0.2 * rough)
    img -= 0.5 * np.clip(1.8 * s - seam_d + 0.5, 0, 1)
    img -= 0.4 * _contours(rivets - 0.6 * s, 1.4 * s) * (rivets < 3 * s)
    ink = _Ink(n)
    for c in np.asarray(centers)[rng.random(len(centers)) < 0.2]:
        length = rng.uniform(20, 90) * s
        path = np.stack([c[0] + rng.normal(0, 1, 5).cumsum() * s, c[1] + np.linspace(8 * s, length, 5)], 1)
        ink.taper(path, rng.uniform(3, 6) * s, 1, rng.uniform(0.3, 0.7))
    for _ in range(int(900 * s * s)):
        r = rng.uniform(0.7, 2.0) * s
        ink.ellipse(rng.random(2) * n, r, r, 1.0)
    pits = ink.array()
    return img * (1.0 - 0.5 * _blur(pits * np.where(pits > 0.9, rust, 1.0), 0.7 * s))


def _corrugated_metal(rng, n):  # 2.5 m, 20 ribs of 12.5 cm
    s = n / 1024
    u, v = _uv(n)
    ribs = 20
    dents = _smooth(_noise(rng, n, 2, 2, 0.5), 0.8, 2.5)
    phase = ribs * u + 0.08 * dents + 0.03 * _noise(rng, n, 2, 2)
    c = np.cos(TAU * phase)
    img = 0.58 + 0.32 * np.tanh(1.8 * c) / np.tanh(1.8)
    period = n / ribs
    valley = np.abs((phase - 0.75 + 0.5) % 1.0 - 0.5) * period
    crest = np.abs((phase - 0.25 + 0.5) % 1.0 - 0.5) * period
    img -= 0.3 * np.clip(1.4 * s - valley + 0.5, 0, 1)
    img -= 0.2 * _hatch(n, 0.35 * np.clip(-c, 0, 1) ** 1.5, ribs * 6, 0, 6 * (phase - ribs * u))
    img += 0.12 * np.clip(1.2 * s - crest + 0.5, 0, 1)
    img *= _relief(dents * 12 * s, 1.0)
    streak = _noise(rng, n, 4, 4, 0.6, stretch=(14, 1))
    img -= 0.14 * _smooth(streak, 0.4, 2.0) * _smooth(_noise(rng, n, 2, 3), -0.5, 1.0)
    img -= 0.05 * _noise(rng, n, 3, 4)
    ink = _Ink(n)
    for y in (0.25, 0.75):
        for k in range(0, ribs, 2):
            c0 = ((k + 0.25) / ribs * n, y * n)
            ink.ellipse(c0, 7 * s, 7 * s, 0.35)
            ink.ellipse(c0, 4 * s, 4 * s, 1.0)
    return img * (1.0 - 0.7 * _blur(ink.array(), 0.5 * s))


def _grating(rng, n):  # 1 m, 16 x 16 square mesh of flat bars
    s = n / 1024
    u, v = _uv(n)
    cells, half = 16, 8 * s
    dbx = np.abs((u * cells + 0.5) % 1.0 - 0.5) * n / cells
    dby = np.abs((v * cells + 0.5) % 1.0 - 0.5) * n / cells
    bar = np.maximum(half - dbx, half - dby)
    cover = np.clip(bar + 0.5, 0, 1)
    shade = _relief(np.clip(bar / (4 * s), 0, 1) * 4 * s, 1.0)
    serr = np.where(dbx < dby, _hatch(n, 0.25, 0, n // 8), _hatch(n, 0.25, n // 8, 0)) * (bar > 3 * s)
    grime = _smooth(_noise(rng, n, 4, 4), 0.3, 1.8)
    metal = 0.8 + 0.05 * _noise(rng, n, 10, 3) - 0.2 * grime - 0.1 * serr
    shift = int(round(12 * s))
    shadow = _blur(np.roll(cover, (shift, shift), axis=(0, 1)), 3 * s)
    void = 0.12 + 0.2 * (1 - shadow) * (0.6 + 0.4 * _smooth(_noise(rng, n, 3, 4), -1, 1))
    img = cover * metal * shade + (1 - cover) * void
    img -= 0.4 * np.clip(1.2 * s - np.abs(bar) + 0.5, 0, 1)
    return np.clip(img, 0.1, 0.95)


def _tile(rng, n):  # 1.5 m, 16 x 16 tiles of 9.4 cm
    s = n / 1024
    u, v = _uv(n)
    t = 16
    tx, ty = u * t, v * t
    tid = (np.floor(ty).astype(int) % t) * t + np.floor(tx).astype(int) % t
    dx = (0.5 - np.abs(tx % 1.0 - 0.5)) * n / t
    dy = (0.5 - np.abs(ty % 1.0 - 0.5)) * n / t
    r = 5 * s
    e = np.where((dx < r) & (dy < r), r - np.hypot(r - dx, r - dy), np.minimum(dx, dy))
    e += 0.6 * s * _noise(rng, n, 60, 2) - 10 * s * np.clip(_noise(rng, n, 30, 2) - 2.0, 0, None)
    inside = e - 3 * s
    pick = rng.random(t * t)
    stained = (pick < 0.08)[tid] * _smooth(_noise(rng, n, 12, 3), -0.5, 1.0)
    tone = (0.85 + rng.normal(0, 0.03, t * t).astype(np.float32)[tid]
            - 0.12 * ((pick > 0.08) & (pick < 0.13))[tid] + 0.02 * _noise(rng, n, 30, 2))
    h = np.clip(inside / (6 * s), 0, 1) ** 0.5
    img = tone * _relief(h * 6 * s, 1.0) - 0.25 * stained
    img -= 0.15 * _smooth(_noise(rng, n, 3, 4), 0.8, 2.0)
    joint = _smooth(-inside, -s, s)
    img = img * (1 - joint) + (0.2 + 0.05 * _noise(rng, n, 40, 2)) * joint
    img -= 0.35 * np.clip(1.3 * s - np.abs(inside) + 0.5, 0, 1)
    ink = _Ink(n)
    for k in np.nonzero(pick > 0.96)[0]:
        cx, cy = (k % t + rng.random()) / t * n, (k // t + rng.random()) / t * n
        _crack(ink, rng, (cx, cy), rng.uniform(0, TAU), 0.6 * n / t, 1.5 * s, 1.0, 4 * s, depth=1)
    return img * (1.0 - 0.6 * _blur(ink.array(), 0.5 * s) * (1 - joint))


def _planks(rng, n):  # 2 m, 16 boards of 12.5 cm, joists every 40 cm
    s = n / 1024
    u, v = _uv(n)
    boards = 16
    col = np.floor(u * boards).astype(int) % boards
    xl = (u * boards) % 1.0
    j0 = rng.random(boards)
    j1 = (j0 + rng.uniform(0.35, 0.65, boards)) % 1.0
    two = rng.random(boards) < 0.6
    a0, a1, tw = j0[col].astype(np.float32), j1[col].astype(np.float32), two[col]
    dj0 = np.abs((v - a0 + 0.5) % 1.0 - 0.5)
    dj1 = np.where(tw, np.abs((v - a1 + 0.5) % 1.0 - 0.5), 1.0)
    dj = np.minimum(dj0, dj1) * n
    seg = col * 2 + (tw & ((v - a0) % 1.0 < (a1 - a0) % 1.0))
    srand = rng.random((boards * 2, 2)).astype(np.float32)
    grain = xl * 6 + srand[seg, 0] * 20 + 1.4 * _noise(rng, n, 8, 3, stretch=(1, 0.2))
    for _ in range(6):
        kx, ky = rng.random(2)
        ddx = ((u - kx + 0.5) % 1.0 - 0.5) * boards * 4
        ddy = ((v - ky + 0.5) % 1.0 - 0.5) * 40
        grain += rng.uniform(1.5, 3) * np.exp(-(ddx * ddx + ddy * ddy))
    tone = (0.62 + 0.12 * (srand[seg, 1] - 0.5) + 0.05 * np.sin(TAU * grain)
            + 0.03 * _noise(rng, n, 16, 3, stretch=(1, 0.2)))
    img = tone - 0.25 * _contours(grain, 1.3 * s, spacing=1.0)
    dxp = np.minimum(xl, 1 - xl) * n / boards
    img *= 0.85 + 0.15 * _smooth(dxp, 0, 6 * s)
    img -= 0.5 * np.clip(1.6 * s - dxp + 0.5, 0, 1)
    img -= 0.45 * np.clip(1.2 * s - dj + 0.5, 0, 1)
    ink = _Ink(n)
    nails = [(k + f, (j + 0.5) / 5) for k in range(boards) for f in (0.25, 0.75) for j in range(5)]
    for b in range(boards):
        for jy in [j0[b]] + ([j1[b]] if two[b] else []):
            nails += [(b + f, jy + d) for f in (0.25, 0.75) for d in (-10 / 1024, 10 / 1024)]
    for bx, y in nails:
        c = (bx / boards * n, y * n)
        ink.ellipse(c, 4.5 * s, 4.5 * s, 0.25)
        ink.ellipse(c, 2.8 * s, 2.8 * s, 1.0)
    return img * (1.0 - 0.75 * _blur(ink.array(), 0.5 * s))


def _wood(rng, n):  # 1.5 m: rotten plywood sheets
    s = n / 1024
    u, v = _uv(n)
    col = np.floor(u * 2).astype(int) % 2
    dsx = np.abs((u * 2 + 0.5) % 1.0 - 0.5) * n / 2
    hy = np.where(col == 0, 0.3, 0.8).astype(np.float32)
    dsy = np.abs((v - hy + 0.5) % 1.0 - 0.5) * n
    ds = np.minimum(dsx, dsy)
    face_g = u * 40 + 0.8 * _noise(rng, n, 4, 3, stretch=(1, 0.25)) + 0.15 * _noise(rng, n, 16, 2)
    face = 0.66 + 0.05 * np.sin(TAU * face_g) - 0.3 * _contours(face_g, 1.2 * s, spacing=1.0)
    face += 0.04 * (col - 0.5)
    rot = _noise(rng, n, 3, 3, 0.6)
    swell = _smooth(rot, 0.3, 1.3)
    face *= 1.0 - 0.25 * swell
    face -= 0.15 * _contours(rot, 1.2 * s, spacing=0.35) * _smooth(rot, 0.0, 0.5)
    peel_f = (_noise(rng, n, 4, 4, 0.55) + 0.15 * _noise(rng, n, 12, 2, stretch=(3, 1))
              + 0.7 * swell + 0.6 * np.exp(-ds / (25 * s)))
    peeled = _smooth(peel_f, 1.0, 1.05)
    core_g = v * 30 + 1.2 * _noise(rng, n, 4, 3, stretch=(0.25, 1))
    core = 0.42 + 0.06 * np.sin(TAU * core_g) - 0.2 * _contours(core_g, 1.0 * s, spacing=1.0)
    holes = _smooth(rot + 0.3 * _noise(rng, n, 20, 2), 1.75, 1.8)
    height = (1 - peeled) * 3 * s + swell * 6 * s + 4 * s * np.exp(-ds / (10 * s))
    img = (face * (1 - peeled) + core * peeled) * _relief(height, 1.0)
    img += 0.12 * _smooth(peel_f, 0.85, 1.0) * (1 - peeled)
    img -= 0.4 * _contours(peel_f - 1.0, 1.6 * s)
    img -= 0.5 * np.clip(1.8 * s - ds + 0.5, 0, 1)
    img = img * (1 - holes) + 0.1 * holes
    return img - 0.4 * _contours(rot + 0.3 * _noise(rng, n, 20, 2) - 1.775, 1.6 * s) * (1 - holes)


def _rubble(rng, n):  # 3 m: ~37 cm chunks over ~10 cm gravel and dust
    s = n / 1024
    dust = 0.6 + 0.07 * _noise(rng, n, 3, 5) + 0.04 * _noise(rng, n, 60, 2)
    gid, ge = _cells(rng.random((700, 2)), n, _warp(rng, n, 0.004, 12))
    gkeep = (rng.random(700) < 0.75)[gid]
    gin = ge - 1.5 * s
    gh = np.clip(gin / (10 * s), 0, 1) * gkeep
    cpts = _jittered(rng, 8, 8, 0.9)
    cid, ce = _cells(cpts, n, _warp(rng, n, 0.01, 6))
    ckeep = (rng.random(len(cpts)) < 0.65)[cid]
    shrink = rng.uniform(6, 24, len(cpts)).astype(np.float32)[cid] * s
    core = ce - shrink
    cin = core + 3 * s * _noise(rng, n, 40, 2)
    chunk = (cin > 0) & ckeep
    ch = np.clip(core / (50 * s), 0.02, 1) * chunk
    height = np.where(chunk, 10 * s + 45 * s * ch, 5 * s * gh + 1.5 * s * _noise(rng, n, 50, 2) * (gh == 0))
    shade = _relief(height, 1.0)
    tone = np.where(
        chunk, 0.78 + rng.normal(0, 0.06, len(cpts)).astype(np.float32)[cid],
        np.where(gh > 0, 0.56 + rng.normal(0, 0.08, 700).astype(np.float32)[gid], dust))
    shift = int(round(10 * s))
    cast = _blur(np.roll(chunk.astype(np.float32), (shift, shift), axis=(0, 1)), 3 * s) * ~chunk
    img = tone * shade - 0.35 * cast
    img -= 0.25 * _hatch(n, np.clip((0.85 - shade) * 1.5, 0, 0.5), 90, -90)
    img -= 0.6 * _contours(cin, 2.0 * s) * ckeep
    img -= 0.3 * _contours(gin, 1.0 * s) * gkeep * ~chunk
    return img


def _dirt(rng, n):  # 3 m
    s = n / 1024
    img = 0.64 + 0.035 * _noise(rng, n, 3, 6, 0.6) + 0.02 * _noise(rng, n, 30, 3)
    grime = _noise(rng, n, 2, 4, 0.6)
    img -= 0.12 * _smooth(grime, 0.3, 1.5)
    img -= 0.2 * _contours(grime - 0.3, 1.2 * s, spacing=0.5) * _smooth(grime, 0.0, 0.8)
    img -= 0.15 * _hatch(n, 0.35 * _smooth(grime, 0.6, 1.8), 70, -70, 0.2 * grime)
    img += 0.08 * _blur(rng.standard_normal((n, n), dtype=np.float32), 0.8 * s)
    ink = _Ink(n, fill=-1.0)
    for _ in range(int(40 * s)):
        c = rng.random(2) * n
        path = _walk(rng, c, rng.uniform(0, TAU), 6, rng.uniform(6, 14) * s, 0.15)
        ink.line(path, max(1.0, 1.2 * s), rng.choice((0.35, 0.85)))
    for _ in range(int(2500 * s * s)):
        r = rng.uniform(0.5, 1.3) * s
        ink.ellipse(rng.random(2) * n, r, r, 0.2)
    for _ in range(int(650 * s * s)):
        c = rng.random(2) * n
        r = rng.uniform(1.5, 7.5) * s
        ink.polygon(_blob(rng, c + 0.4 * r, r, 6, 0.5), 0.3)
        ink.polygon(_blob(rng, c, r, 6, 0.5), rng.uniform(0.6, 0.88), 0.12, max(1.0, 1.2 * s))
    return ink.over(img)


def _foam(rng, n):  # 1.5 m: shredded foam and matted fibres
    s = n / 1024
    ink = _Ink(n, fill=-1.0)
    for _ in range(int(2200 * s * s)):
        c = rng.random(2) * n
        tone = rng.uniform(0.6, 0.9)
        if rng.random() < 0.12:
            ink.polygon(_blob(rng, c, rng.uniform(12, 40) * s, 14, 0.6), tone, 0.12, 2 * s)
        else:
            path = _walk(rng, c, rng.uniform(0, TAU), int(rng.integers(8, 20)), 7 * s, 0.22, 0.0)
            w = rng.uniform(1.5, 3.5) * s
            ink.line(path, w + 2.5 * s, 0.1)
            ink.line(path, w, tone)
    img = ink.over(0.22 + 0.06 * _noise(rng, n, 6, 4))
    light = img > 0.5
    img -= 0.25 * _smooth(_noise(rng, n, 90, 2), 1.2, 1.6) * light
    img -= 0.15 * _hatch(n, 0.35 * _smooth(_noise(rng, n, 6, 3), -0.5, 1.5), 96, 96) * light
    return _blur(img, 0.5 * s) * (0.85 + 0.15 * _smooth(_noise(rng, n, 3, 3), -1.5, 1.5))


def _liquid_crust(rng, n):  # 3 m
    s = n / 1024
    field = _noise(rng, n, 3, 4, 0.5)
    swirl = _resample(field, 60 * s * _noise(rng, n, 2, 3), 60 * s * _noise(rng, n, 2, 3))
    liquid = (0.32 + 0.06 * swirl + 0.25 * _contours(swirl, 1.4 * s, spacing=0.5)
              - 0.2 * _contours(swirl - 0.25, 1.2 * s, spacing=0.5))
    liquid -= 0.12 * _hatch(n, 0.25, 0, 128, 0.15 * swirl)
    scum = _noise(rng, n, 3, 5, 0.55) + 0.25 * _noise(rng, n, 14, 3)
    island = _smooth(scum, 0.35, 0.4)
    pid, pe = _cells(rng.random((220, 2)), n, _warp(rng, n, 0.006, 8))
    plate_in = pe - 2.5 * s + 1.2 * s * _noise(rng, n, 50, 2)
    crust = island * _smooth(plate_in, -0.5 * s, 0.5 * s)
    wrinkle = _noise(rng, n, 24, 3)
    plate = 0.76 + rng.normal(0, 0.05, 220).astype(np.float32)[pid] + 0.06 * wrinkle
    height = crust * (2 * s + np.clip(plate_in / (6 * s), 0, 1) * 3 * s + 1.0 * s * wrinkle)
    img = liquid * (1 - crust) + crust * plate * _relief(height, 1.0)
    img -= 0.45 * _contours(scum - 0.375, 1.6 * s)
    img -= 0.3 * _contours(plate_in, 1.2 * s) * island
    bubbles = _Ink(n)
    for _ in range(int(80 * s * s)):
        c, r = rng.random(2) * n, rng.uniform(3, 10) * s
        bubbles.ellipse(c, r, r, None, 1.0, 1.5 * s)
        bubbles.ellipse(c - 0.35 * r, 0.25 * r, 0.25 * r, -1.0)
    b = _blur(bubbles.array(), 0.5 * s) * (1 - island)
    return img - 0.35 * np.clip(b, 0, 1) + 0.4 * np.clip(-b, 0, 1)


def _hazard_stripes(rng, n):  # 1 m, period n/8 along x (128 px at 1024)
    s = n / 1024
    u, v = _uv(n)
    phase = 8 * (u + v) + 0.004 * _noise(rng, n, 60, 2)
    per = n / (8 * np.sqrt(2))
    tw = (phase - 0.25 + 0.5) % 1.0 - 0.5
    sd = (0.25 - np.abs(tw)) * per
    paint = 0.2 + 0.68 * np.clip(sd + 0.5, 0, 1)
    paint -= 0.04 * _smooth(_noise(rng, n, 4, 4), 0, 2) * (sd > 0)
    chip_f = _noise(rng, n, 8, 4, 0.6) + 0.4 * _noise(rng, n, 40, 2) + 0.6 * np.exp(-np.abs(sd) / (6 * s))
    chip = _smooth(chip_f, 1.6, 1.65)
    metal = 0.5 + 0.06 * _noise(rng, n, 30, 2)
    img = paint * (1 - chip) + metal * chip - 0.35 * _contours(chip_f - 1.625, 1.4 * s)
    ink = _Ink(n)
    for _ in range(int(60 * s)):
        c = rng.random(2) * n
        a = rng.uniform(0, TAU)
        length = rng.uniform(20, 120) * s
        ink.line([c, c + length * np.array([np.cos(a), np.sin(a)])], max(1.0, 1.2 * s), 1.0)
    sc = _blur(ink.array(), 0.5 * s)
    return np.clip(img * (1 - sc) + 0.5 * sc, 0.1, 0.95)


# key: (recipe, target mean or None when the recipe sets its own tones)
_TEXTURES = {
    "concrete_floor": (_concrete_floor, 0.62),
    "concrete_wall": (_concrete_wall, 0.66),
    "stone_floor": (_stone_floor, 0.6),
    "stone_wall": (_stone_wall, 0.6),
    "rock": (_rock, 0.57),
    "rust_metal": (_rust_metal, 0.56),
    "corrugated_metal": (_corrugated_metal, 0.58),
    "grating": (_grating, None),
    "tile": (_tile, 0.66),
    "planks": (_planks, 0.6),
    "wood": (_wood, 0.57),
    "rubble": (_rubble, 0.57),
    "dirt": (_dirt, 0.6),
    "foam": (_foam, 0.56),
    "liquid_crust": (_liquid_crust, 0.56),
    "hazard_stripes": (_hazard_stripes, None),
}
TEXTURE_KEYS: tuple[str, ...] = tuple(_TEXTURES)


def texture(key: str, size: int = 1024, seed: int = 0) -> np.ndarray:
    """Seamless grayscale ink texture, float32 (size, size) in [0.1, 0.95]."""
    recipe, mean = _TEXTURES[key]
    img = recipe(_rng(key, seed), size).astype(np.float32)
    return np.clip(img, 0.1, 0.95) if mean is None else _finish(img, mean)


# --------------------------------------------------------------------------- decals


def _rotate(alpha, degrees):
    img = Image.fromarray(alpha.astype(np.float32), "F")
    return np.asarray(img.rotate(degrees, Image.BILINEAR, expand=True), dtype=np.float32)


def _crop(alpha, size, pad=12):
    """RGBA uint8 sticker: black ink, alpha = coverage, cropped to the ink plus ``pad``."""
    a = np.clip(alpha, 0.0, 1.0)
    ys, xs = np.nonzero(a >= 0.5 / 255)
    a = a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    room = size - 2 * pad
    if max(a.shape) > room:
        k = room / max(a.shape)
        w, h = max(1, int(a.shape[1] * k)), max(1, int(a.shape[0] * k))
        a = np.clip(np.asarray(Image.fromarray(a, "F").resize((w, h), Image.BOX)), 0.0, 1.0)
    a = np.pad(a, pad)
    out = np.zeros(a.shape + (4,), np.uint8)
    out[..., 3] = np.round(a * 255).astype(np.uint8)
    return out


def _radial(n, cx=0.5, cy=0.5):
    u, v = _uv(n)
    return np.hypot(u - cx, v - cy), np.arctan2(v - cy, u - cx)


def _d_crack(rng, n):
    s = n / 1024
    ink = _Ink(2 * n, wrap=False)
    _crack(ink, rng, (n, 0.5 * n), np.pi / 2 + rng.normal(0, 0.2), 0.75 * n, 6.5 * s, 1.0, 7 * s, depth=3)
    return ndimage.gaussian_filter(ink.array(), 0.7 * s)


def _d_oil_stain(rng, n):
    r, _ = _radial(n)
    r = r / 0.32
    f = 1.0 - r + 0.22 * _noise(rng, n, 3, 5, 0.55) - 4.0 * np.clip(r - 1.1, 0, None)
    f2 = f + 0.08 * _noise(rng, n, 30, 3)
    core = _smooth(f2, 0.0, 0.03) * np.clip(0.92 + 0.05 * _noise(rng, n, 8, 3), 0, 1)
    halo = _smooth(f2, -0.12, 0.0) * np.clip(0.5 + 0.25 * _noise(rng, n, 16, 3), 0, 1)
    return np.maximum(core, halo)


def _d_puddle(rng, n):
    s = n / 1024
    u, v = _uv(n)
    a = rng.uniform(0, np.pi)
    du, dv = u - 0.5, v - 0.5
    x, y = du * np.cos(a) + dv * np.sin(a), -du * np.sin(a) + dv * np.cos(a)
    f = 1.0 - np.hypot(x / 0.36, y / 0.24) + 0.12 * _noise(rng, n, 2, 2, 0.5)
    lumpy = _noise(rng, n, 6, 2, 0.5)
    for _ in range(3):
        ang, rr = rng.uniform(0, TAU), rng.uniform(0.03, 0.06)
        c = 0.5 + rng.uniform(0.3, 0.38) * np.array([np.cos(ang), np.sin(ang)])
        f = np.maximum(f, 1.0 - np.hypot(u - c[0], v - c[1]) / rr + 0.25 * lumpy)
    f -= 4.0 * np.clip(np.hypot(du, dv) - 0.43, 0, None)
    edge = _smooth(f, 0.0, 2.5 * s / (0.3 * n))
    body = 0.68 + 0.08 * _noise(rng, n, 3, 2) + 0.2 * (1 - _smooth(f, 0.0, 0.08))
    return edge * body


def _d_blood_smear(rng, n):
    s = n / 1024
    u, v = _uv(n)
    t = (u - 0.12) / 0.78
    yc = 0.5 + 0.05 * np.sin(TAU * (0.6 * u + rng.random()))
    halfw = (0.1 - 0.05 * t) * (1 + 0.12 * _noise(rng, n, 4, 3))
    body = _smooth(halfw - np.abs(v - yc), -0.003, 0.003) * _smooth(t, 0.0, 0.02) * _smooth(1 - t, 0.0, 0.05)
    pool = _smooth(0.11 + 0.02 * _noise(rng, n, 6, 3) - np.hypot(u - 0.14, v - yc), -0.003, 0.003)
    streaks = _noise(rng, n, 8, 4, 0.5, stretch=(0.25, 2))
    cover = body * np.clip(0.55 + 0.3 * streaks + 0.4 * (1 - t), 0, 1) * _smooth(streaks + 2.2 - 3.2 * t, -0.2, 0.2)
    ink = _Ink(n, wrap=False)
    for _ in range(25):
        x = rng.uniform(0.15, 0.85)
        y = 0.5 + 0.05 * np.sin(TAU * (0.6 * x)) + rng.choice((-1, 1)) * rng.uniform(0.08, 0.16)
        r = rng.uniform(2, 9) * s
        ink.ellipse((x * n, y * n), r, r, rng.uniform(0.7, 1.0))
    for _ in range(4):
        x = rng.uniform(0.15, 0.6) * n
        y0 = (0.5 + 0.06) * n
        length = rng.uniform(30, 90) * s
        path = np.stack([x + rng.normal(0, 1, 6).cumsum() * s, y0 + np.linspace(0, length, 6)], 1)
        ink.taper(path, rng.uniform(4, 7) * s, 2 * s, 0.9)
        ink.ellipse(path[-1], 4 * s, 5 * s, 0.9)
    a = np.maximum(np.maximum(cover, pool * 0.95), _blur(ink.array(), 0.6 * s))
    return _rotate(a, rng.uniform(-35, 35))


def _d_rust_streak(rng, n):
    u, v = _uv(n)
    vert = _noise(rng, n, 2, 2, 0.5, stretch=(0.05, 1))
    streaks = _noise(rng, n, 6, 3, 0.5, stretch=(3, 0.3))
    a = np.zeros_like(u)
    for dx, y0, length, width in [(0.0, 0.12, 0.8, 0.045)] + [
        (rng.uniform(-0.03, 0.03), 0.13, rng.uniform(0.25, 0.5), 0.015) for _ in range(2)
    ]:
        t = (v - y0) / length
        xc = 0.5 + dx + 0.006 * vert
        halfw = width * np.clip(1 - 0.35 * t - 0.65 * t ** 4, 0, None) * (1 + 0.12 * vert)
        body = _smooth(halfw - np.abs(u - xc), -0.004, 0.004) * _smooth(t, -0.02, 0.0) * (t < 1)
        fade = np.clip(1.1 - np.clip(t, 0, 1) ** 1.3 + 0.3 * streaks, 0, 1)
        a = np.maximum(a, body * fade * np.clip(0.7 + 0.25 * streaks, 0, 1))
    r = np.hypot(u - 0.5, v - 0.12)
    bloom = _smooth(0.05 + 0.012 * _noise(rng, n, 10, 3) - r, -0.004, 0.004)
    a = np.maximum(a, bloom * np.clip(0.85 + 0.15 * _noise(rng, n, 30, 2), 0, 1))
    return a


def _d_splatter(rng, n):
    s = n / 1024
    ink = _Ink(n, wrap=False)
    c0 = np.array([0.5, 0.5]) * n
    for _ in range(70):
        ang = rng.uniform(0, TAU)
        dist = 0.3 * n * np.sqrt(rng.random())
        c = c0 + dist * np.array([np.cos(ang), np.sin(ang)])
        r = (3 + 22 * rng.random() ** 3) * s
        ink.polygon(_blob(rng, c, r, 16, 0.35), 1.0)
        if rng.random() < 0.35:
            tail = c + (r + rng.uniform(10, 50) * s) * np.array([np.cos(ang), np.sin(ang)])
            ink.taper(np.linspace(c, tail, 6), 1.2 * r, 1.5 * s, 1.0)
            ink.ellipse(tail, 0.4 * r + s, 0.4 * r + s, 1.0)
    for _ in range(150):
        ang = rng.uniform(0, TAU)
        c = c0 + 0.4 * n * np.sqrt(rng.random()) * np.array([np.cos(ang), np.sin(ang)])
        r = rng.uniform(2.5, 5) * s
        ink.ellipse(c, r, r, 1.0)
    return _smooth(ndimage.gaussian_filter(ink.array(), 2.5 * s), 0.35, 0.6)


def _d_scorch(rng, n):
    r, theta = _radial(n)
    k = np.arange(1, 65)
    amp = rng.standard_normal(64) / k**0.6
    ph = rng.uniform(0, TAU, 64)
    angles = np.linspace(-np.pi, np.pi, 4097)
    profile = (amp[:, None] * np.cos(k[:, None] * angles[None] + ph[:, None])).sum(0)
    rays = np.interp(theta, angles, profile / profile.std()).astype(np.float32)
    f = r / 0.33 + 0.2 * _noise(rng, n, 4, 4) - 0.08 * rays
    core = 1.0 - _smooth(f, 0.25, 0.6)
    feather = (1.0 - _smooth(f, 0.3, 1.05)) ** 1.5 * np.clip(0.65 + 0.25 * rays, 0, 1)
    speck = np.clip(0.9 + 0.1 * _noise(rng, n, 120, 2), 0, 1)
    return np.maximum(0.97 * core, 0.8 * feather * speck) * _smooth(0.48 - r, 0.0, 0.03)


def _boot(L):
    """Alpha of a left boot print, length L px, toe up."""
    w = 0.38 * L
    img = Image.new("F", (int(w * 1.6), int(L * 1.25)), 0.0)
    d = ImageDraw.Draw(img)
    cx, top = img.width / 2, 0.1 * L
    d.ellipse([cx - w / 2 + 0.04 * L, top, cx + w / 2, top + 0.6 * L], fill=1.0)
    d.polygon([(cx - w / 2 + 0.07 * L, top + 0.35 * L), (cx + w / 2, top + 0.35 * L),
               (cx + 0.3 * w, top + 0.66 * L), (cx - 0.3 * w, top + 0.66 * L)], fill=1.0)
    d.rounded_rectangle([cx - 0.4 * w, top + 0.7 * L, cx + 0.4 * w, top + L], radius=0.12 * L, fill=1.0)
    gap = max(1, int(0.025 * L))
    for y in np.arange(top + 0.08 * L, top + 0.62 * L, 0.075 * L):
        d.line([(cx - w, y), (cx + w, y)], fill=0.0, width=gap)
    for y in np.arange(top + 0.77 * L, top + 0.97 * L, 0.075 * L):
        d.line([(cx - w, y), (cx + w, y)], fill=0.0, width=gap)
    d.line([(cx, top + 0.05 * L), (cx, top + 0.6 * L)], fill=0.0, width=gap)
    return np.asarray(img, dtype=np.float32)


def _d_footprints(rng, n):
    s = n / 1024
    m = int(1.2 * n)
    canvas = np.zeros((m, m), np.float32)
    left = _boot(0.11 * n)
    heading = np.radians(45 + rng.normal(0, 6))
    fwd = np.array([np.cos(heading), np.sin(heading)])
    side = np.array([-fwd[1], fwd[0]])
    start = np.array([0.15, 0.15]) * m
    for i in range(8):
        foot = 1 if i % 2 else -1
        pos = start + i * 0.13 * n * fwd + foot * 0.04 * n * side + rng.normal(0, 3, 2) * s
        stamp = left if foot < 0 else left[:, ::-1]
        deg = -np.degrees(heading) - 90 - foot * 8 + rng.normal(0, 3)
        st = _rotate(stamp, deg) * (1.0 - 0.06 * i)
        y0, x0 = int(pos[1] - st.shape[0] / 2), int(pos[0] - st.shape[1] / 2)
        view = canvas[y0 : y0 + st.shape[0], x0 : x0 + st.shape[1]]
        np.maximum(view, st[: view.shape[0], : view.shape[1]], out=view)
    wear = np.clip(0.8 + 0.25 * _noise(rng, m, 20, 3), 0, 1)
    return ndimage.gaussian_filter(canvas, 0.6 * s) * wear


_DECALS = {
    "crack": _d_crack,
    "oil_stain": _d_oil_stain,
    "puddle": _d_puddle,
    "blood_smear": _d_blood_smear,
    "rust_streak": _d_rust_streak,
    "splatter": _d_splatter,
    "scorch": _d_scorch,
    "footprints": _d_footprints,
}
DECAL_KEYS: tuple[str, ...] = tuple(_DECALS)


def decal(key: str, size: int = 1024, seed: int = 0) -> np.ndarray:
    """RGBA uint8 decal: RGB zero, alpha = ink coverage, cropped to the ink plus 12 px."""
    recipe = _DECALS[key]
    return _crop(recipe(_rng(key, seed), size), size)


# ---------------------------------------------------------------------------- sheet


def _sheet(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=20)
    cell, label = 512, 28
    cols = 4
    rows = -(-len(TEXTURE_KEYS) // cols)
    sheet = Image.new("L", (cols * cell, rows * (cell + label)), 40)
    draw = ImageDraw.Draw(sheet)
    for i, key in enumerate(TEXTURE_KEYS):
        t0 = time.perf_counter()
        tex = texture(key)
        print(f"{key:18s} {time.perf_counter() - t0:5.2f} s  mean {tex.mean():.3f}")
        tiled = Image.fromarray(np.round(np.tile(tex, (2, 2)) * 255).astype(np.uint8))
        x, y = (i % cols) * cell, (i // cols) * (cell + label)
        sheet.paste(tiled.resize((cell, cell), Image.LANCZOS), (x, y + label))
        draw.text((x + 6, y + 3), key, fill=255, font=font)
    sheet.save(outdir / "textures_tiled.png")

    rows = -(-len(DECAL_KEYS) // cols)
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label)), (128, 128, 128))
    draw = ImageDraw.Draw(sheet)
    for i, key in enumerate(DECAL_KEYS):
        t0 = time.perf_counter()
        img = Image.fromarray(decal(key), "RGBA")
        print(f"{key:18s} {time.perf_counter() - t0:5.2f} s  {img.width}x{img.height}")
        img.thumbnail((cell - 16, cell - 16), Image.LANCZOS)
        x, y = (i % cols) * cell, (i // cols) * (cell + label)
        sheet.paste(img, (x + (cell - img.width) // 2, y + label + (cell - img.height) // 2), img)
        draw.text((x + 6, y + 3), key, fill=(255, 255, 255), font=font)
    sheet.save(outdir / "decals.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sheet", type=Path, required=True, metavar="OUTDIR",
                        help="write textures_tiled.png and decals.png for review")
    _sheet(parser.parse_args().sheet)


if __name__ == "__main__":
    main()
