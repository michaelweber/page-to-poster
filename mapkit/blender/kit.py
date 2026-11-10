"""Building kit for map models. Import inside Blender: a project's props.py does `from mapkit.blender.kit import *`.

What that gives a props module:
- MB, a mesh builder that collects closed primitives (several materials) into one object
- T, a transform; primitives that add to an MB: box, boxmm, prism, loft, cyl, frustum, tube, dome, sweep, blob, ...
- path helpers (path_at, path_len, project, strip) and Rect, the room rectangle passed to every room function
- feat (name a point so the poster can hang a callout on it), decal / wall_decal, lamp, material
- ready-made props: crate, barrel, tank, shelf, table, bunk, console, pillar, platform, ladder, pits, pools, ...

Conventions: meters, +x east, +y north, floor top at z=0. Floors are SLAB_T slabs under z=0; walls are WALL_T thick
and stand outside the room rectangle. Every solid is a closed mesh (normals are recomputed outward per island), so
the cutaway can slice it and show a solid black section. Everything random takes a seeded `rng`.
docs/props.md explains how to use the kit and shows every ready-made prop; docs/kit-reference.md lists every call
(regenerate it with `python tools/kit_reference.py` after changing a signature or docstring here).
"""
import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from .. import layout as lay
from . import materials as bm

SEED = 1313
WALL_T = 0.6
SLAB_T = 0.3
OVERLAY_TOP = 0.05      # floor overlays (dust, dirt, tile) stay below this; floor decals go on top
DEG = math.pi / 180.0

# Set by init() from layout.json. A props module must be imported after init() so `import *` sees these values.
CW, CH = 3.2, 3.6       # corridor width and height
VW, VZ = 0.9, 2.6       # vent duct width, height of the duct centre above the floor
ROCK_TOP = 4.6          # corridor/junction walls rise above the corridor ceiling
ROOM_MIN_TOP = 4.65     # room walls at least this tall (never coplanar with corridor wall tops)
JUNC_HALF = 2.2         # half-size of the square floor at a corridor junction

C = {}                  # collections by name: structure, walls, vents, blockages, props_<room id>, decals, lights, ...
ANCH = {}               # anchors.json: where rooms, links, entrances and named features ended up
ISSUES = []             # problems the builder worked around or noticed; printed after the build
BUILDERS = []           # every MB made during this build, to catch one that was filled but never output


# ============================================================================================== basics
def init(layout, project):
    """Reset the kit for one build: corridor sizes from layout.json, textures and decals from the project folder."""
    global CW, CH, VW, VZ, ROCK_TOP, ROOM_MIN_TOP, JUNC_HALF
    CW, CH, VW, VZ = lay.sizes(layout)
    ROCK_TOP = CH + 1.0
    ROOM_MIN_TOP = ROCK_TOP + 0.05
    JUNC_HALF = CW / 2 + WALL_T
    C.clear()
    ANCH.clear()
    ANCH.update({"units": "meters", "frame": "+x east, +y north, floor z=0", "rooms": {}, "junctions": {},
                 "links": [], "vents": [], "blockages": [], "entrances": [], "features": {}, "decals": []})
    ISSUES.clear()
    _MAT_DONE.clear()
    BUILDERS.clear()
    bm.configure(Path(project) / "textures", Path(project) / "decals")


def coll(name, parent=None):
    """Create a collection (a named group of objects) and remember it in C[name]."""
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    C[name] = c
    return c


_MAT_DONE = set()


def material(name, color, tex=None, scale=2.0, rough=0.8, metal=0.0, emit=0.0, bump=0.3):
    """Register a material for this build. `tex` is a texture key (textures/<tex>.png in the project) or None;
    `scale` is how many meters one texture tile covers. A strongly red `color` prints in the poster's accent ink."""
    bm.MATS[name] = dict(color=tuple(color), tex=tex, scale=scale, rough=rough, metal=metal, emit=emit, bump=bump)


def MAT(name):
    """The Blender material for a material name."""
    m = bm.get(name)
    if name not in _MAT_DONE:  # viewport/Workbench colour only; the shader itself belongs to materials.py
        spec = bm.MATS[name]
        m.diffuse_color = (*spec["color"], 1.0)
        m.roughness = spec.get("rough", 0.8)
        m.metallic = spec.get("metal", 0.0)
        _MAT_DONE.add(name)
    return m


def feat(name, x, y, z=0.0):
    """Name the point (x, y, z) so poster.json can hang a callout on it. Use "<room id>.<what>" names."""
    ANCH["features"][name] = [round(x, 3), round(y, 3), round(z, 3)]


def T(x=0.0, y=0.0, z=0.0, rz=0.0, rx=0.0, ry=0.0, s=None):
    """Transform: move to (x, y, z), then turn by rz about the vertical (and rx, ry), then scale by s. Chain
    frames with @, as in T(5, 2, 0, rz=0.5) @ T(ry=0.3). Pass the result as a primitive's M."""
    M = Matrix.Translation((x, y, z)) @ Euler((rx, ry, rz), "XYZ").to_matrix().to_4x4()
    if s is not None:
        M = M @ Matrix.Diagonal((s[0], s[1], s[2], 1.0))
    return M


def Rz(a):
    """Turn by `a` radians about the vertical."""
    return T(rz=a)


class MB:
    """Mesh builder: primitives add closed solids (several materials) to it; obj() turns it into one object.
        mb = MB("r3_props"); box(mb, "wood", ...); cyl(mb, "rust_metal", ...); mb.obj(P)
    Use one MB per group of things that belong together. An MB that stays empty makes no object."""

    def __init__(self, name):
        self.name = name
        self.v, self.f, self.fm, self.mats = [], [], [], []
        self.used = False
        BUILDERS.append(self)

    def add(self, verts, faces, mat, M=None):
        if mat not in self.mats:
            if mat not in bm.MATS:   # fail here, on the line that names it, not later when the mesh is made
                raise ValueError(f"unknown material {mat!r}. Known: {', '.join(sorted(bm.MATS))}. "
                                 "Register your own with material(name, color) at the top of props.py.")
            self.mats.append(mat)
        mi = self.mats.index(mat)
        b = len(self.v)
        if M is None:
            self.v.extend(tuple(p) for p in verts)
        else:
            self.v.extend(tuple(M @ Vector(p)) for p in verts)
        for f in faces:
            self.f.append(tuple(b + i for i in f))
            self.fm.append(mi)

    def empty(self):
        return not self.f

    def mesh(self):
        """The collected solids as a mesh, for placing many copies with inst()."""
        self.used = True
        me = bpy.data.meshes.new(self.name)
        me.from_pydata(self.v, [], self.f)
        for n in self.mats:
            me.materials.append(MAT(n))
        me.polygons.foreach_set("material_index", self.fm)
        b = bmesh.new()
        b.from_mesh(me)
        bmesh.ops.recalc_face_normals(b, faces=b.faces[:])
        b.to_mesh(me)
        b.free()
        me.update()
        return me

    def obj(self, collection, name=None, nocap=False):
        """Add the collected solids to `collection` (a room's P) as one object. nocap=True keeps the cutaway
        from slicing it (walkable surfaces such as stairs and ramps)."""
        if self.empty():
            return None
        ob = bpy.data.objects.new(name or self.name, self.mesh())
        collection.objects.link(ob)
        if nocap:
            ob["nocap"] = True
        return ob


def inst(me, collection, M, name=None):
    """Place a copy of mesh `me` (from MB.mesh() or a *_mesh() helper) at transform M."""
    ob = bpy.data.objects.new(name or me.name, me)
    ob.matrix_world = M
    collection.objects.link(ob)
    return ob


# ============================================================================================== primitives
BOX_F = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def box(mb, mat, c, s, M=None):
    """Box centred on c = (x, y, z) with size s = (sx, sy, sz)."""
    hx, hy, hz = s[0] / 2, s[1] / 2, s[2] / 2
    x, y, z = c
    v = [(x - hx, y - hy, z - hz), (x + hx, y - hy, z - hz), (x + hx, y + hy, z - hz), (x - hx, y + hy, z - hz),
         (x - hx, y - hy, z + hz), (x + hx, y - hy, z + hz), (x + hx, y + hy, z + hz), (x - hx, y + hy, z + hz)]
    mb.add(v, BOX_F, mat, M)


def boxmm(mb, mat, lo, hi, M=None):
    """Box from corner lo = (x0, y0, z0) to corner hi = (x1, y1, z1)."""
    box(mb, mat, [(a + b) / 2 for a, b in zip(lo, hi)], [b - a for a, b in zip(lo, hi)], M)


def prism(mb, mat, poly, z0, z1, M=None):
    """Extrude the polygon `poly` (a list of (x, y)) from height z0 to z1."""
    n = len(poly)
    v = [(p[0], p[1], z0) for p in poly] + [(p[0], p[1], z1) for p in poly]
    f = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))] + \
        [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    mb.add(v, f, mat, M)


def loft(mb, mat, rings, M=None):
    """Skin a stack of rings (each a list of the same number of (x, y, z) points) and cap both ends."""
    n = len(rings[0])
    v = [p for r in rings for p in r]
    f = []
    for k in range(len(rings) - 1):
        a, b = k * n, (k + 1) * n
        f += [(a + i, a + (i + 1) % n, b + (i + 1) % n, b + i) for i in range(n)]
    last = (len(rings) - 1) * n
    f.append(tuple(range(n - 1, -1, -1)))
    f.append(tuple(range(last, last + n)))
    mb.add(v, f, mat, M)


def ring(r, z, n, cx=0.0, cy=0.0, phase=0.0, sx=1.0, sy=1.0):
    """n points on a circle of radius r at height z (an ellipse with sx, sy), for loft()."""
    return [(cx + r * sx * math.cos(phase + 2 * math.pi * i / n), cy + r * sy * math.sin(phase + 2 * math.pi * i / n), z)
            for i in range(n)]


def cyl(mb, mat, c, r, h, n=16, M=None):
    """Upright cylinder: base centre c = (x, y, z), radius r, height h, n sides."""
    x, y, z = c
    loft(mb, mat, [ring(r, z, n, x, y), ring(r, z + h, n, x, y)], M)


def frustum(mb, mat, c, r0, r1, h, n=16, M=None, phase=0.0):
    """Upright cone section: radius r0 at the base c, r1 at the top, height h."""
    x, y, z = c
    loft(mb, mat, [ring(max(r0, 0.005), z, n, x, y, phase), ring(max(r1, 0.005), z + h, n, x, y, phase)], M)


def sqfrustum(mb, mat, c, s0, s1, h, M=None):
    """Square frustum: side s0 at the bottom, s1 at the top."""
    frustum(mb, mat, c, s0 / math.sqrt(2), s1 / math.sqrt(2), h, 4, M, phase=math.pi / 4)


def tube(mb, mat, c, rin, rout, z0, z1, n=24, M=None):
    """Upright ring or pipe section around c = (x, y): inner radius rin, outer rout, from height z0 to z1."""
    x, y = c[0], c[1]
    v = ring(rout, z0, n, x, y) + ring(rout, z1, n, x, y) + ring(rin, z0, n, x, y) + ring(rin, z1, n, x, y)
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j, n + i), (2 * n + j, 2 * n + i, 3 * n + i, 3 * n + j),
              (n + i, n + j, 3 * n + j, 3 * n + i), (j, i, 2 * n + i, 2 * n + j)]
    mb.add(v, f, mat, M)


def dome(mb, mat, c, r, h, n=16, rings_=5, M=None):
    """Closed dome (flat base at c.z, height h)."""
    x, y, z = c
    rs = []
    for k in range(rings_ + 1):
        a = (math.pi / 2) * k / rings_
        rs.append(ring(max(r * math.cos(a), 0.01 * r), z + h * math.sin(a), n, x, y))
    loft(mb, mat, rs, M)


def arc_slab(mb, mat, cx, cy, rin, rout, a0, a1, z0, z1, step=0.14):
    """Curved slab: the part of a ring (rin..rout around cx, cy) between angles a0 and a1, from z0 to z1."""
    n = max(1, int(abs(a1 - a0) / step))
    for i in range(n):
        b0, b1 = a0 + (a1 - a0) * i / n, a0 + (a1 - a0) * (i + 1) / n
        poly = [(cx + rout * math.cos(b0), cy + rout * math.sin(b0)), (cx + rout * math.cos(b1), cy + rout * math.sin(b1)),
                (cx + rin * math.cos(b1), cy + rin * math.sin(b1)), (cx + rin * math.cos(b0), cy + rin * math.sin(b0))]
        prism(mb, mat, poly, z0, z1)


def arc_pts(cx, cy, r, a0, a1, z, step=0.12):
    """Points along an arc at height z, for railing() or sweep()."""
    n = max(1, int(abs(a1 - a0) / step))
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n), z) for i in range(n + 1)]


def xprism(mb, mat, poly_yz, x0, x1, M=None):
    """Extrude the profile `poly_yz` (a list of (y, z)) along x from x0 to x1: wedges, ramps, gutters."""
    loft(mb, mat, [[(x0, y, z) for y, z in poly_yz], [(x1, y, z) for y, z in poly_yz]], M)


def sweep(mb, mat, pts, r, n=6, M=None):
    """Round bar of radius r through the 3D points `pts`: pipes, rails, cables, bars."""
    P = [Vector(p) for p in pts]
    P = [p for i, p in enumerate(P) if i == 0 or (p - P[i - 1]).length > 1e-5]
    m = len(P)
    if m < 2:
        return
    tans = []
    for i in range(m):
        if i == 0:
            t = P[1] - P[0]
        elif i == m - 1:
            t = P[-1] - P[-2]
        else:
            t = (P[i + 1] - P[i]).normalized() + (P[i] - P[i - 1]).normalized()
            if t.length < 1e-6:
                t = P[i + 1] - P[i]
        tans.append(t.normalized())
    up = Vector((0, 0, 1)) if abs(tans[0].z) < 0.9 else Vector((1, 0, 0))
    u = (up - up.dot(tans[0]) * tans[0]).normalized()
    rings_ = []
    for i in range(m):
        t = tans[i]
        u = (u - u.dot(t) * t)
        u = u.normalized() if u.length > 1e-6 else (Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0)))
        w = t.cross(u)
        rings_.append([tuple(P[i] + r * (math.cos(2 * math.pi * k / n) * u + math.sin(2 * math.pi * k / n) * w))
                       for k in range(n)])
    loft(mb, mat, rings_, M)


def chamfer(pts, d):
    """Cut the corners of a 3D polyline back by d, so a sweep() through it bends instead of kinking."""
    out = [tuple(pts[0])]
    for i in range(1, len(pts) - 1):
        p = Vector(pts[i])
        a = p - Vector(pts[i - 1])
        b = Vector(pts[i + 1]) - p
        dd = min(d, a.length * 0.45, b.length * 0.45)
        out += [tuple(p - a.normalized() * dd), tuple(p + b.normalized() * dd)]
    out.append(tuple(pts[-1]))
    return out


_ICO = None


def _ico():
    global _ICO
    if _ICO is None:
        b = bmesh.new()
        bmesh.ops.create_icosphere(b, subdivisions=1, radius=1.0)
        b.verts.index_update()
        _ICO = ([tuple(v.co) for v in b.verts], [tuple(v.index for v in f.verts) for f in b.faces])
        b.free()
    return _ICO


def blob(mb, mat, c, rad, rng, jit=0.25, rz=0.0, M=None):
    """Lumpy boulder centred on c with radii rad = (rx, ry, rz): rubble, rock, sacks, organic lumps. jit is how
    irregular it is (0 to about 0.4)."""
    v, f = _ico()
    R = Euler((0, 0, rz)).to_matrix()
    out = []
    for p in v:
        k = 1 + jit * (rng.random() * 2 - 1)
        q = R @ Vector((p[0] * rad[0] * k, p[1] * rad[1] * k, p[2] * rad[2] * k))
        out.append((c[0] + q.x, c[1] + q.y, c[2] + q.z))
    mb.add(out, f, mat, M)


def flat_blob(mb, mat, cx, cy, r, z0, z1, rng, n=14, irr=0.35, sx=1.0, sy=1.0, rot=0.0, M=None):
    """Irregular flat patch about r across from height z0 to z1: puddles of dirt, flakes, rugs, moss."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1 + irr * (rng.random() * 2 - 1))
        x, y = rr * math.cos(a) * sx, rr * math.sin(a) * sy
        pts.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    prism(mb, mat, pts, z0, z1, M)


def stairs(mb, mat, x, y, ang, width, n, rise, run, z0=0.0, base=None, M=None):
    """Solid flight: bottom front edge centred on (x, y), climbing along heading `ang`."""
    base = z0 if base is None else base
    F = T(x, y, 0, rz=ang)
    if M is not None:
        F = M @ F
    for i in range(n):
        boxmm(mb, mat, (i * run, -width / 2, base), ((i + 1) * run, width / 2, z0 + (i + 1) * rise), F)


def railing(mb, mat, pts, h=1.05, r=0.03, spacing=1.4, M=None):
    """Handrail + mid rail along base points pts (3D), posts every `spacing`."""
    sweep(mb, mat, [(p[0], p[1], p[2] + h) for p in pts], r, 6, M)
    sweep(mb, mat, [(p[0], p[1], p[2] + h * 0.5) for p in pts], r * 0.8, 6, M)
    acc = 0.0
    posts = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        L = math.dist(a, b)
        while acc + spacing < L:
            acc += spacing
            t = acc / L
            posts.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
        acc -= L
    posts.append(pts[-1])
    for p in posts:
        cyl(mb, mat, (p[0], p[1], p[2]), r, h, 6, M)


def panel_rects(length, z0, z1, holes):
    """Rectangles (x0, x1, z0, z1) that tile a wall of `length` from z0 to z1 around its holes."""
    xs = sorted({0.0, length, *[min(max(h[0], 0.0), length) for h in holes], *[min(max(h[1], 0.0), length) for h in holes]})
    rects = []
    for a, b in zip(xs, xs[1:]):
        if b - a < 1e-4:
            continue
        mid = (a + b) / 2
        cov = sorted((h[2], h[3]) for h in holes if h[0] < mid < h[1])
        z = z0
        for h0, h1 in cov:
            if h0 > z + 1e-4:
                rects.append((a, b, z, min(h0, z1)))
            z = max(z, h1)
            if z >= z1:
                break
        if z < z1 - 1e-4:
            rects.append((a, b, z, z1))
    return rects


def panel(mb, mat, M, length, z0, z1, thick, holes=(), y0=0.0):
    """Wall panel in frame M: x 0..length, y y0..y0+thick, z z0..z1, rectangular holes (x0, x1, z0, z1)."""
    for a, b, za, zb in panel_rects(length, z0, z1, list(holes)):
        boxmm(mb, mat, (a, y0, za), (b, y0 + thick, zb), M)


def rest_z(ly, lz, tilt):
    """Centre height of a box (depth ly, height lz) tilted by `tilt` about x so its lowest edge sits 2 cm deep."""
    return ly / 2 * abs(math.sin(tilt)) + lz / 2 * math.cos(tilt) - 0.02


# ============================================================================================== path utils
def seglen(a, b):
    """Length of the 2D segment a-b."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def trim_start(path, d):
    """The path with d meters cut off its first segment."""
    a, b = path[0], path[1]
    t = d / seglen(a, b)
    return [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)] + list(path[1:])


def trim_end(path, d):
    """The path with d meters cut off its last segment."""
    return list(reversed(trim_start(list(reversed(path)), d)))


def path_len(path):
    """Length of a 2D polyline."""
    return sum(seglen(a, b) for a, b in zip(path, path[1:]))


def path_at(path, s):
    """Point and unit tangent at arc length s."""
    for a, b in zip(path, path[1:]):
        L = seglen(a, b)
        if s <= L or b is path[-1]:
            t = min(max(s / L, 0.0), 1.0)
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        s -= L
    raise ValueError


def project(path, p):
    """Nearest point on a polyline to p: (distance, point, unit tangent, arc length along the path)."""
    best = None
    s0 = 0.0
    for a, b in zip(path, path[1:]):
        L = seglen(a, b)
        t = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (L * L)
        t = min(max(t, 0.0), 1.0)
        q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        d = math.dist(q, p)
        if best is None or d < best[0]:
            best = (d, q, ((b[0] - a[0]) / L, (b[1] - a[1]) / L), s0 + t * L)
        s0 += L
    return best


def dist_poly(path, p):
    """Distance from point p to a polyline."""
    return project(path, p)[0]


def offset_pts(path, o):
    """The polyline moved sideways by o meters (positive: to its left), with mitred corners."""
    segs = []
    for a, b in zip(path, path[1:]):
        L = seglen(a, b)
        segs.append((-(b[1] - a[1]) / L, (b[0] - a[0]) / L))
    out = []
    n = len(path)
    for i in range(n):
        if i == 0:
            nx, ny = segs[0]
        elif i == n - 1:
            nx, ny = segs[-1]
        else:
            a, b = segs[i - 1], segs[i]
            mx, my = a[0] + b[0], a[1] + b[1]
            L = math.hypot(mx, my)
            mx, my = mx / L, my / L
            c = mx * a[0] + my * a[1]
            nx, ny = mx / c, my / c
        out.append((path[i][0] + nx * o, path[i][1] + ny * o))
    return out


def strip(mb, mat, path, oa, ob, z0, z1):
    """Band along a polyline between sideways offsets oa and ob, from height z0 to z1: floors, kerbs, walls,
    conveyor beds, carpets."""
    A, B = offset_pts(path, oa), offset_pts(path, ob)
    for i in range(len(path) - 1):
        prism(mb, mat, [A[i], A[i + 1], B[i + 1], B[i]], z0, z1)


# ============================================================================================== rooms & openings
class Rect:
    """A room (or a corridor junction): the rectangle minx..maxx, miny..maxy of clear floor, ceiling height `h`
    and wall top `top`. Room functions get one as `r` and place props relative to it (r.cx, r.cy, r.minx, ...)."""

    def __init__(self, rid, name, minx, miny, maxx, maxy, h, top, junction=False, floor="concrete_floor",
                 wall="concrete_wall"):
        self.id, self.name, self.junction = rid, name, junction
        self.minx, self.miny, self.maxx, self.maxy, self.h, self.top = minx, miny, maxx, maxy, h, top
        self.cx, self.cy = (minx + maxx) / 2, (miny + maxy) / 2
        self.w, self.d = maxx - minx, maxy - miny
        self.floor, self.wall = floor, wall
        self.openings = []   # (side, along, width, z0, z1)

    def rand(self, rng, margin=1.0):
        """A random floor point at least `margin` meters from the walls."""
        return rng.uniform(self.minx + margin, self.maxx - margin), rng.uniform(self.miny + margin, self.maxy - margin)

    def on_boundary(self, p, tol=0.05):
        """True when 2D point p lies on the room's edge."""
        x, y = p
        inside_x = self.minx - tol <= x <= self.maxx + tol
        inside_y = self.miny - tol <= y <= self.maxy + tol
        return (inside_x and (abs(y - self.miny) < tol or abs(y - self.maxy) < tol)) or \
               (inside_y and (abs(x - self.minx) < tol or abs(x - self.maxx) < tol))

    def side_of(self, p):
        """(wall letter nearest to p, p's coordinate along that wall)."""
        d = {"S": abs(p[1] - self.miny), "N": abs(p[1] - self.maxy), "W": abs(p[0] - self.minx), "E": abs(p[0] - self.maxx)}
        s = min(d, key=d.get)
        return s, (p[0] if s in "SN" else p[1])

    def add_opening(self, p, width, z0, z1, toward=None):
        """Cut a doorway `width` wide from height z0 to z1 in the wall at p."""
        if toward is not None:  # junction: side from the direction toward the next path point
            dx, dy = toward[0] - self.cx, toward[1] - self.cy
            s = ("E" if dx > 0 else "W") if abs(dx) > abs(dy) else ("N" if dy > 0 else "S")
            along = self.cx if s in "SN" else self.cy
        else:
            s, along = self.side_of(p)
        self.openings.append((s, along, width, z0, z1))

    def inner_face(self, side, along, inset=0.012):
        """2D point just inside wall `side` at coordinate `along`, for fixing things to the wall."""
        return {"S": (along, self.miny + inset), "N": (along, self.maxy - inset),
                "W": (self.minx + inset, along), "E": (self.maxx - inset, along)}[side]


SIDE_NORMAL_ROT = {"N": 0.0, "S": math.pi, "E": -math.pi / 2, "W": math.pi / 2}


def build_walls(t, mb, mat=None):
    """Four walls around room t with its openings cut out."""
    mat = mat or t.wall
    W, z0, z1 = WALL_T, -SLAB_T, t.top
    ops = {k: [] for k in "SNWE"}
    for s, along, w, oz0, oz1 in t.openings:
        ops[s].append((along, w, oz0, oz1))

    def holes(side, origin):
        return [(a - origin - w / 2, a - origin + w / 2, oz0, oz1) for a, w, oz0, oz1 in ops[side]]

    Lx = t.maxx - t.minx + 2 * W
    panel(mb, mat, T(t.minx - W, t.miny - W, 0), Lx, z0, z1, W, holes("S", t.minx - W))
    panel(mb, mat, T(t.minx - W, t.maxy, 0), Lx, z0, z1, W, holes("N", t.minx - W))
    Ly = t.maxy - t.miny
    panel(mb, mat, T(t.minx, t.miny, 0, rz=math.pi / 2), Ly, z0, z1, W, holes("W", t.miny))
    panel(mb, mat, T(t.maxx + W, t.miny, 0, rz=math.pi / 2), Ly, z0, z1, W, holes("E", t.miny))


# ============================================================================================== decals & lights
def decal(name, x, y, z=0.0, size=1.0, rot=0.0, color=None):
    """Lay the ink sticker decals/<name>.png flat on a surface at (x, y, z), `size` meters across. A decal whose
    image is missing is skipped (returns None), so props can ask for decals that only some projects generate."""
    ANCH["decals"].append({"name": name, "at": [round(x, 3), round(y, 3), round(z, 3)], "size": size,
                           "rot": round(rot, 3)})
    return bm.decal(name, (x, y, z), size, rot, color=color, collection=C["decals"])


def wall_decal(name, room, side, along, z, size, color=None):
    """Vertical decal on the inner face of a room wall (decal plane rotated 90 deg to face into the room)."""
    x, y = room.inner_face(side, along)
    ANCH["decals"].append({"name": name, "at": [round(x, 3), round(y, 3), round(z, 3)], "size": size,
                           "wall": side, "room": room.id})
    ob = bm.decal(name, (x, y, z), size, 0.0, color=color, lift=0.0, collection=C["decals"])
    if ob is not None:
        ob.rotation_euler = (math.pi / 2, 0.0, SIDE_NORMAL_ROT[side])
    return ob


def lamp(name, kind, loc, energy, color=(1.0, 1.0, 1.0), rot=(0.0, 0.0, 0.0), spot_deg=40.0, radius=0.15,
         map_dim=1.0):
    """A practical light (kind POINT, SPOT or AREA; energy in watts). Map and poster renders multiply its energy by
    `map_dim`: use a small value for lights sized for a dark interior that would wash out the lit map view."""
    ld = bpy.data.lights.new(name, kind)
    ld.energy = energy
    ld.color = color
    if kind == "SPOT":
        ld.spot_size = spot_deg * DEG
        ld.spot_blend = 0.25
    if kind in ("POINT", "SPOT"):
        ld.shadow_soft_size = radius
    ob = bpy.data.objects.new(name, ld)
    ob.location = loc
    ob.rotation_euler = rot
    ob["map_dim"] = float(map_dim)
    C["lights"].objects.link(ob)
    return ob


WARM = (1.0, 0.78, 0.5)
RED = (1.0, 0.12, 0.08)


# ============================================================================================== ready-made props
# Each prop adds closed solids to an MB. Props placed by `M` use a local frame (origin on the floor, z up): build it
# with T(x, y, z, rz=heading). Props placed by x, y stand on the floor at that point. docs/props.md shows them all.
def crate(mb, x, y, size=(1.0, 0.7, 0.6), rz=0.0, z=0.0, mat="wood"):
    """Crate with corner battens and a lid."""
    lx, ly, lz = size
    M = T(x, y, z, rz=rz)
    boxmm(mb, mat, (-lx / 2, -ly / 2, 0), (lx / 2, ly / 2, lz), M)
    for sx in (-1, 1):
        for sy in (-1, 1):
            box(mb, mat, (sx * lx / 2, sy * ly / 2, lz / 2 + 0.008), (0.08, 0.08, lz + 0.016), M)
    box(mb, mat, (0, 0, lz + 0.012), (lx + 0.03, ly + 0.03, 0.03), M)


def barrel(mb, x, y, r=0.3, h=0.9, z=0.0, mat="rust_metal", lying=False, rz=0.0):
    """Drum with two rolled hoops. lying=True puts it on its side along heading rz."""
    M = T(x, y, z + (r if lying else 0.0), rz=rz)
    if lying:
        M = M @ T(ry=math.pi / 2) @ T(z=-h / 2)
    cyl(mb, mat, (0, 0, 0), r, h, 14, M)
    for f in (0.3, 0.7):
        tube(mb, mat, (0, 0), r, r + 0.015, h * f - 0.02, h * f + 0.02, 14, M)


def tank(mb, x, y, r=1.5, h=4.0, z=0.0, rz=0.0, mat="rust_metal", ladder=True):
    """Upright cylindrical tank on a low plinth, with band rings, a conical top and a ladder (toward heading rz)."""
    M = T(x, y, z, rz=rz)
    cyl(mb, "concrete_floor", (0, 0, 0), r + 0.25, 0.3, 28, M)
    body = h - 0.3 - 0.35 * r
    cyl(mb, mat, (0, 0, 0.305), r, body, 32, M)
    frustum(mb, mat, (0, 0, 0.305 + body), r, r * 0.3, 0.35 * r, 32, M)
    n = max(1, int(body / 2.2))
    for i in range(1, n + 1):
        zz = 0.305 + body * i / (n + 1)
        tube(mb, mat, (0, 0), r, r + 0.06, zz - 0.07, zz + 0.07, 32, M)
    if ladder:
        for s in (-0.22, 0.22):
            sweep(mb, mat, [(r + 0.2, s, 0.3), (r + 0.2, s, 0.3 + body)], 0.022, 5, M)
        k = 0
        while 0.6 + k * 0.3 < body:
            sweep(mb, mat, [(r + 0.2, -0.22, 0.6 + k * 0.3), (r + 0.2, 0.22, 0.6 + k * 0.3)], 0.014, 4, M)
            k += 1


def machine(mb, M, size=(2.0, 1.2, 1.6), rng=None, mat="painted_metal", detail="rust_metal", light="emissive_red"):
    """Generic machine in frame M (centred on the origin): a housing on a skid with an upper block, a stack or a
    hopper, side panels, a pipe and a status light. Vary `size` and the rng for different silhouettes."""
    rng = rng or random.Random(0)
    lx, ly, lz = size
    boxmm(mb, detail, (-lx / 2 - 0.08, -ly / 2 - 0.08, 0), (lx / 2 + 0.08, ly / 2 + 0.08, 0.12), M)
    boxmm(mb, mat, (-lx / 2, -ly / 2, 0.12), (lx / 2, ly / 2, lz * 0.7), M)
    ux = rng.uniform(0.35, 0.55) * lx
    left = rng.random() < 0.5
    x0 = -lx / 2 + 0.05 if left else lx / 2 - 0.05 - ux
    boxmm(mb, mat, (x0, -ly / 2 + 0.1, lz * 0.7), (x0 + ux, ly / 2 - 0.1, lz), M)
    cxs = (x0 + ux + lx / 2) / 2 if left else (-lx / 2 + x0) / 2
    rr = min(ly, lx - ux) * 0.3
    if rng.random() < 0.5:
        cyl(mb, detail, (cxs, 0, lz * 0.7), rr * 0.6, lz * rng.uniform(0.4, 0.9), 12, M)
    else:
        sqfrustum(mb, detail, (cxs, 0, lz * 0.7), rr, rr * 2.0, lz * 0.35, M)
    for k in range(3):
        u0 = -lx / 2 + 0.15 + k * (lx - 0.3) / 3
        boxmm(mb, detail, (u0, -ly / 2 - 0.02, 0.3), (u0 + 0.8 * (lx - 0.3) / 3, -ly / 2 + 0.01, lz * 0.6), M)
    side = lx / 2 if left else -lx / 2
    out = side + (0.4 if left else -0.4)
    sweep(mb, detail, chamfer([(side - (0.1 if left else -0.1), 0, lz * 0.4), (out, 0, lz * 0.4), (out, 0, 0.0)], 0.15),
          0.08, 8, M)
    box(mb, light, (x0 + ux / 2, -ly / 2 + 0.09, lz * 0.85), (0.15, 0.03, 0.1), M)


def console(mb, M, width=1.4, depth=0.7, mat="painted_metal", screen="emissive_red"):
    """Control console in frame M (centred, operator stands at -y): plinth, sloped desk and a lit screen."""
    w, d = width / 2, depth / 2
    boxmm(mb, mat, (-w, -d + 0.15, 0), (w, d, 0.8), M)
    xprism(mb, mat, [(-d, 0.8), (d - 0.2, 0.8), (d - 0.2, 1.1), (-d, 0.86)], -w, w, M)
    boxmm(mb, mat, (-w, d - 0.2, 0.8), (w, d, 1.5), M)
    boxmm(mb, screen, (-w + 0.1, d - 0.215, 1.15), (w - 0.1, d - 0.19, 1.45), M)


def shelf(mb, M, length=2.0, depth=0.5, height=2.0, levels=4, mat="painted_metal", load=None, rng=None):
    """Open shelving in frame M (x 0..length, y 0..depth). With `load` (a material) and `rng`, boxes sit on it."""
    for x in (0.0, length - 0.05):
        for y in (0.0, depth - 0.05):
            boxmm(mb, mat, (x, y, 0), (x + 0.05, y + 0.05, height), M)
    gap = (height - 0.2) / max(levels - 1, 1)
    for i in range(levels):
        zz = 0.12 + gap * i
        boxmm(mb, mat, (0.01, 0.01, zz), (length - 0.01, depth - 0.01, zz + 0.04), M)
        if load and rng and i < levels - 1:
            u = 0.1
            while u < length - 0.5:
                w, hgt = rng.uniform(0.2, 0.45), rng.uniform(0.12, max(0.14, min(0.4, gap - 0.1)))
                if rng.random() < 0.75:
                    boxmm(mb, load, (u, 0.06, zz + 0.04), (u + w, depth - 0.06, zz + 0.04 + hgt), M)
                u += w + rng.uniform(0.03, 0.25)


def lockers(mb, M, n=6, w=0.4, depth=0.5, height=1.9, mat="painted_metal", rng=None, open_doors=None):
    """Row of n lockers in frame M (x 0..n*w, y 0..depth, doors on the -y side). With rng, some doors hang open;
    or name the open ones yourself with open_doors=(1, 4) (0 is the first locker)."""
    boxmm(mb, mat, (0, 0.03, 0), (n * w, depth, height), M)
    for i in range(n):
        D = M @ T(i * w + 0.02, 0.03, 0.08)
        if (i in open_doors) if open_doors is not None else (rng is not None and rng.random() < 0.25):
            D = D @ T(rz=-(rng.uniform(0.5, 1.6) if rng is not None else 1.2))
        boxmm(mb, mat, (0, -0.025, 0), (w - 0.04, 0, height - 0.16), D)
        boxmm(mb, "black_plastic", (w - 0.12, -0.04, height * 0.5), (w - 0.08, -0.025, height * 0.5 + 0.12), D)


def table(mb, M, length=1.6, depth=0.8, height=0.75, mat="wood", leg="painted_metal"):
    """Table in frame M, centred on the origin."""
    boxmm(mb, mat, (-length / 2, -depth / 2, height - 0.05), (length / 2, depth / 2, height), M)
    for sx in (-1, 1):
        for sy in (-1, 1):
            box(mb, leg, (sx * (length / 2 - 0.08), sy * (depth / 2 - 0.08), (height - 0.05) / 2),
                (0.06, 0.06, height - 0.05), M)


def bunk(mb, M, length=2.0, width=0.9, tiers=2, mat="painted_metal", bedding="fabric"):
    """Bunk bed in frame M (x 0..length, y 0..width); tiers=1 is a cot."""
    top = 0.4 + (tiers - 1) * 0.95 + 0.4
    for x in (0.0, length - 0.05):
        for y in (0.0, width - 0.05):
            boxmm(mb, mat, (x, y, 0), (x + 0.05, y + 0.05, top), M)
    for t in range(tiers):
        zz = 0.4 + t * 0.95
        boxmm(mb, mat, (0.01, 0.01, zz), (length - 0.01, width - 0.01, zz + 0.05), M)
        boxmm(mb, bedding, (0.06, 0.05, zz + 0.05), (length - 0.06, width - 0.05, zz + 0.17), M)
        boxmm(mb, "foam", (0.1, 0.12, zz + 0.17), (0.5, width - 0.12, zz + 0.25), M)


def container(mb, M, holes_s=(), holes_n=(), mat="corrugated"):
    """20 ft shipping container in frame M (x 0..6.06, y 0..2.44, z 0..2.59), doors open at x=L. holes_s / holes_n
    cut windows or doorways in the long sides as (x0, x1, z0, z1)."""
    L, Wd, H, t = 6.06, 2.44, 2.59, 0.08
    boxmm(mb, mat, (0, 0, 0), (L, Wd, 0.12), M)
    boxmm(mb, mat, (0, 0, H - t), (L, Wd, H), M)
    panel(mb, mat, M, L, 0.12, H - t, t, holes_s, y0=0.0)
    panel(mb, mat, M, L, 0.12, H - t, t, holes_n, y0=Wd - t)
    boxmm(mb, mat, (0, t, 0.12), (t, Wd - t, H - t), M)
    boxmm(mb, mat, (0.02, 0, H - 0.25), (L - 0.02, 0.1, H - 0.12), M)  # top rail outside (corrugation lip)
    boxmm(mb, mat, (0, 0, 0), (0.06, 1.22, H - 0.25), M @ T(L, 0, 0.1, rz=-100 * DEG))
    boxmm(mb, mat, (0, -1.22, 0), (0.06, 0, H - 0.25), M @ T(L, Wd, 0.1, rz=100 * DEG))


def pillar(mb, x, y, size=0.8, h=4.0, mat="concrete_wall", round_=False, z=0.0):
    """Pillar with a wider base and cap: square (side `size`) or, with round_=True, a column (diameter `size`)."""
    if round_:
        cyl(mb, mat, (x, y, z), size / 2 + 0.12, 0.25, 20)
        cyl(mb, mat, (x, y, z + 0.25), size / 2, h - 0.5, 20)
        cyl(mb, mat, (x, y, z + h - 0.25), size / 2 + 0.12, 0.25, 20)
    else:
        box(mb, mat, (x, y, z + 0.125), (size + 0.24, size + 0.24, 0.25))
        box(mb, mat, (x, y, z + h / 2), (size, size, h - 0.5))
        box(mb, mat, (x, y, z + h - 0.125), (size + 0.24, size + 0.24, 0.25))


def partition(mb, x0, y0, x1, y1, h=2.8, t=0.2, mat="concrete_wall", doors=()):
    """Interior wall from (x0, y0) to (x1, y1), h high and t thick. `doors` lists doorways as (distance from the
    start, width) or (distance, width, height). Use it to split a room into cells, closets or stalls."""
    M = T(x0, y0, 0, rz=math.atan2(y1 - y0, x1 - x0))
    holes = [(d[0], d[0] + d[1], -0.01, d[2] if len(d) > 2 else 2.2) for d in doors]
    panel(mb, mat, M, math.hypot(x1 - x0, y1 - y0), 0.0, h, t, holes, y0=-t / 2)


def door_leaf(mb, M, width=1.4, height=2.2, t=0.08, mat="painted_metal", handle=True):
    """Door slab in frame M: hinge on the origin, leaf along +x. Turn M to hang it ajar."""
    boxmm(mb, mat, (0, -t / 2, 0.02), (width, t / 2, height), M)
    if handle:
        boxmm(mb, "black_plastic", (width - 0.2, -t / 2 - 0.03, height * 0.45), (width - 0.08, t / 2 + 0.03, height * 0.5),
              M)


def platform(mb, x0, y0, x1, y1, z, mat="grating", frame="rust_metal", rail=True, open_sides=""):
    """Raised deck from (x0, y0) to (x1, y1) with its top at height z, on legs. Railings run along every side not
    named in `open_sides` (letters from "NSEW")."""
    boxmm(mb, mat, (x0, y0, z - 0.1), (x1, y1, z))
    nx, ny = max(1, int((x1 - x0) / 3.0)), max(1, int((y1 - y0) / 3.0))
    for i in range(nx + 1):
        for j in range(ny + 1):
            px, py = x0 + 0.1 + (x1 - x0 - 0.2) * i / nx, y0 + 0.1 + (y1 - y0 - 0.2) * j / ny
            box(mb, frame, (px, py, (z - 0.1) / 2), (0.12, 0.12, z - 0.1))
    if rail:
        e = 0.05
        sides = {"S": [(x0 + e, y0 + e, z), (x1 - e, y0 + e, z)], "N": [(x0 + e, y1 - e, z), (x1 - e, y1 - e, z)],
                 "W": [(x0 + e, y0 + e, z), (x0 + e, y1 - e, z)], "E": [(x1 - e, y0 + e, z), (x1 - e, y1 - e, z)]}
        for s, pts in sides.items():
            if s not in open_sides:
                railing(mb, frame, pts)


def walk_seg(mb, M, length, width=1.4):
    """Catwalk section in frame M: grating from x=0 to x=length at z=0, with stringers and railings both sides."""
    boxmm(mb, "grating", (0, -width / 2, -0.08), (length, width / 2, 0), M)
    for s in (-1, 1):
        boxmm(mb, "rust_metal", (0, s * width / 2 - 0.04, -0.3), (length, s * width / 2 + 0.04, -0.08), M)
        railing(mb, "rust_metal", [(0.03, s * (width / 2 - 0.03), 0.0), (length - 0.03, s * (width / 2 - 0.03), 0.0)],
                spacing=1.3, M=M)


def ladder(mb, x, y, z0, z1, rz=0.0, mat="rust_metal", width=0.45):
    """Vertical ladder from height z0 to z1 at (x, y). rz turns it (the rungs run along local y)."""
    M = T(x, y, 0, rz=rz)
    for s in (-width / 2, width / 2):
        sweep(mb, mat, [(0, s, z0), (0, s, z1)], 0.022, 5, M)
    zz = z0 + 0.3
    while zz < z1 - 0.05:
        sweep(mb, mat, [(0, -width / 2, zz), (0, width / 2, zz)], 0.014, 4, M)
        zz += 0.3


def pipe_run(mb, pts, r=0.12, mat="rust_metal", flanges=2.5, bend=0.4):
    """Pipe through the 3D points `pts` with rounded bends and a flange collar every `flanges` meters (0: none)."""
    sweep(mb, mat, chamfer(pts, bend) if len(pts) > 2 else list(pts), r, 10)
    if not flanges:
        return
    for a, b in zip(pts, pts[1:]):
        A, B = Vector(a), Vector(b)
        n = int((B - A).length / flanges)
        d = (B - A).normalized() * 0.05
        for i in range(1, n + 1):
            p = A.lerp(B, i / (n + 1))
            sweep(mb, mat, [tuple(p - d), tuple(p + d)], r + 0.05, 10)


def cable(mb, a, b, sag=0.6, r=0.03, mat="black_plastic"):
    """Cable hanging between the 3D points a and b, drooping `sag` meters in the middle."""
    pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t - sag * 4 * t * (1 - t))
           for t in [i / 10 for i in range(11)]]
    sweep(mb, mat, pts, r, 5)


def rect_pit(mb, cut, x0, y0, x1, y1, depth, wall="concrete_wall", floor="concrete_floor", rim="hazard"):
    """Rectangular pit from (x0, y0) to (x1, y1), `depth` meters deep, with a painted rim (rim=None: none).
    `cut` is the room's floor cutter: the fourth argument of a room function."""
    boxmm(cut, floor, (x0 - 0.02, y0 - 0.02, -1.0), (x1 + 0.02, y1 + 0.02, 1.0))
    t = 0.4
    boxmm(mb, wall, (x0 - t, y0 - t, -depth - 0.3), (x1 + t, y0, -0.02))
    boxmm(mb, wall, (x0 - t, y1, -depth - 0.3), (x1 + t, y1 + t, -0.02))
    boxmm(mb, wall, (x0 - t, y0, -depth - 0.3), (x0, y1, -0.02))
    boxmm(mb, wall, (x1, y0, -depth - 0.3), (x1 + t, y1, -0.02))
    boxmm(mb, floor, (x0, y0, -depth - 0.3), (x1, y1, -depth))
    if rim:
        for (a, b, c, d) in ((x0 - 0.2, y0 - 0.2, x1 + 0.2, y0), (x0 - 0.2, y1, x1 + 0.2, y1 + 0.2),
                             (x0 - 0.2, y0, x0, y1), (x1, y0, x1 + 0.2, y1)):
            boxmm(mb, rim, (a, b, 0.0), (c, d, 0.015))


def round_pit(mb, cut, x, y, r, depth, wall="concrete_wall", floor="concrete_floor", rim="hazard"):
    """Round pit of radius r, `depth` meters deep. `cut` is the room's floor cutter."""
    cyl(cut, floor, (x, y, -1.0), r + 0.02, 2.0, 40)
    tube(mb, wall, (x, y), r, r + 0.4, -depth - 0.3, -0.02, 40)
    cyl(mb, floor, (x, y, -depth - 0.3), r + 0.1, 0.3, 40)
    if rim:
        tube(mb, rim, (x, y), r, r + 0.2, 0.0, 0.015, 40)


def pool(mb, cut, x0, y0, x1, y1, depth=0.8, level=-0.15, liquid="water", **pit):
    """Rectangular basin filled with `liquid` up to z=`level` (below 0). Extra keywords go to rect_pit."""
    rect_pit(mb, cut, x0, y0, x1, y1, depth, **pit)
    boxmm(mb, liquid, (x0 + 0.01, y0 + 0.01, -depth + 0.01), (x1 - 0.01, y1 - 0.01, level))


def round_pool(mb, cut, x, y, r, depth=0.8, level=-0.15, liquid="water", **pit):
    """Round basin filled with `liquid` up to z=`level` (below 0). Extra keywords go to round_pit."""
    round_pit(mb, cut, x, y, r, depth, **pit)
    cyl(mb, liquid, (x, y, -depth + 0.01), r - 0.01, depth + level - 0.01, 40)


def rubble_pile(mb, x, y, r, h, rng, mat="rubble", n=None):
    """Heap of broken chunks about 2*r across and h high."""
    for k in range(n or max(6, int(r * r * 6))):
        a, d = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.random())
        f = 1 - d / r
        s = rng.uniform(0.25, 0.55) * (0.7 + 0.6 * f) * min(1.0, r)
        hz = max(0.12, h * (0.25 + 0.5 * f))
        blob(mb, mat, (x + d * math.cos(a), y + d * math.sin(a), hz * 0.5), (s, s * rng.uniform(0.7, 1.0), hz), rng,
             0.3, rng.uniform(0, 3))


def debris(mb, r, rng, n=30, mats=("dirt",), size=(0.3, 1.2), margin=1.0):
    """Flat patches of litter, dust or flakes scattered over the floor of room r."""
    for i in range(n):
        x, y = r.rand(rng, margin)
        flat_blob(mb, mats[i % len(mats)], x, y, rng.uniform(*size), 0.0, 0.01 + 0.002 * (i % 9), rng,
                  sx=rng.uniform(0.7, 1.4), rot=rng.uniform(0, math.pi))


def growth(mb, x, y, r, rng, n=14, size=0.12, mat="mushroom"):
    """Cluster of small rounded growths (fungus, eggs, coral, nodules) within r meters of (x, y)."""
    for k in range(n):
        a, d = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.random())
        s = size * rng.uniform(0.6, 1.3)
        blob(mb, mat, (x + d * math.cos(a), y + d * math.sin(a), s * 0.7), (s, s * rng.uniform(0.7, 1.0), s), rng,
             0.12, rng.uniform(0, 3))


def stalagmites(mb, x, y, r, rng, n=5, h=1.5, mat="rock"):
    """Group of n rock spikes within r meters of (x, y), up to h high."""
    for k in range(n):
        a, d = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.random())
        hh = h * rng.uniform(0.35, 1.0)
        frustum(mb, mat, (x + d * math.cos(a), y + d * math.sin(a), 0.0), hh * rng.uniform(0.16, 0.26), 0.03, hh, 7,
                phase=rng.uniform(0, 1))


def cave_fill(mb, r, passages, rng, half_width=1.3, mat="rock", step=1.3):
    """Fill room r with rock except along `passages` (polylines of (x, y) points), leaving winding tunnels about
    2 * half_width wide. Turns a rectangle into a cave, a collapsed hall or a crawl space. Passages should start
    and end at the room's doorways."""
    y = r.miny + 0.5
    while y < r.maxy - 0.3:
        x = r.minx + 0.5
        while x < r.maxx - 0.3:
            px, py = x + rng.uniform(-0.4, 0.4), y + rng.uniform(-0.4, 0.4)
            left = min(dist_poly(p, (px, py)) for p in passages) - half_width * (
                1 + 0.2 * math.sin(px * 0.7) * math.cos(py * 0.5))
            if left > 0.35:
                dw = min(px - r.minx, r.maxx - px, py - r.miny, r.maxy - py)
                rad = max(0.3, min(rng.uniform(0.7, 1.25), left, (dw + 0.45) / 1.3))
                hz = rng.uniform(1.1, 1.6) if left > 1.0 else rng.uniform(0.6, 1.1)
                blob(mb, mat, (px, py, max(0.2, min(hz * 0.9, r.h - hz * 1.05))), (rad, rad * rng.uniform(0.7, 1.0), hz),
                     rng, 0.28, rng.uniform(0, 3))
            elif left < -0.2 and rng.random() < 0.2:
                blob(mb, mat, (px, py, 0.0), (rng.uniform(0.12, 0.3), rng.uniform(0.1, 0.25), rng.uniform(0.06, 0.15)),
                     rng, 0.3)
            x += step
        y += step


def sarcophagus(mb, M, length=2.2, width=0.9, mat="stone_wall", ajar=0.0):
    """Stone coffin in frame M (centred). ajar slides the lid sideways by that many meters."""
    boxmm(mb, mat, (-length / 2 - 0.1, -width / 2 - 0.1, 0), (length / 2 + 0.1, width / 2 + 0.1, 0.15), M)
    boxmm(mb, mat, (-length / 2, -width / 2, 0.15), (length / 2, width / 2, 0.75), M)
    boxmm(mb, mat, (-length / 2 - 0.05, -width / 2 - 0.05, 0.75), (length / 2 + 0.05, width / 2 + 0.05, 0.9),
          M @ T(0, ajar, 0, rz=ajar * 0.25))


def altar(mb, M, length=1.8, width=0.9, mat="stone_wall"):
    """Altar or plinth in frame M (centred): a step, a block and a slab."""
    boxmm(mb, mat, (-length / 2 - 0.4, -width / 2 - 0.4, 0), (length / 2 + 0.4, width / 2 + 0.4, 0.2), M)
    boxmm(mb, mat, (-length / 2 + 0.1, -width / 2 + 0.1, 0.2), (length / 2 - 0.1, width / 2 - 0.1, 1.0), M)
    boxmm(mb, mat, (-length / 2, -width / 2, 1.0), (length / 2, width / 2, 1.12), M)


def chest(mb, x, y, rz=0.0, mat="wood", band="rust_metal"):
    """Treasure chest with a barrel lid and two iron bands."""
    M = T(x, y, 0, rz=rz)
    boxmm(mb, mat, (-0.45, -0.3, 0), (0.45, 0.3, 0.4), M)
    A = M @ T(0, 0, 0.4) @ T(ry=math.pi / 2)
    cyl(mb, mat, (0, 0, -0.45), 0.3, 0.9, 14, A)
    for u in (-0.28, 0.28):
        tube(mb, band, (0, 0), 0.3, 0.32, u - 0.03, u + 0.03, 14, A)
        boxmm(mb, band, (u - 0.03, -0.32, 0), (u + 0.03, 0.32, 0.4), M)


def brazier(mb, x, y, lit=True, mat="rust_metal"):
    """Standing fire bowl; lit=True adds glowing coals and a warm light."""
    cyl(mb, mat, (x, y, 0), 0.22, 0.06, 10)
    cyl(mb, mat, (x, y, 0.06), 0.04, 0.8, 8)
    frustum(mb, mat, (x, y, 0.86), 0.12, 0.38, 0.25, 12)
    if lit:
        cyl(mb, "emissive_warm", (x, y, 1.07), 0.3, 0.06, 12)
        lamp(f"brazier_{len(C['lights'].objects)}", "POINT", (x, y, 1.6), 80.0, WARM, radius=0.3)


def well(mb, cut, x, y, r=1.0, depth=6.0, mat="stone_wall"):
    """Well: a round shaft with a raised stone rim."""
    round_pit(mb, cut, x, y, r, depth, wall=mat, rim=None)
    tube(mb, mat, (x, y), r, r + 0.35, 0.0, 0.8, 32)


def statue(mb, x, y, h=2.4, rng=None, mat="stone_wall"):
    """Weathered figure on a plinth, h meters high overall."""
    rng = rng or random.Random(0)
    box(mb, mat, (x, y, 0.3), (0.9, 0.9, 0.6))
    b = h - 0.6
    blob(mb, mat, (x, y, 0.6 + b * 0.22), (0.24, 0.2, b * 0.24), rng, 0.08)
    blob(mb, mat, (x, y, 0.6 + b * 0.58), (0.3, 0.2, b * 0.22), rng, 0.08)
    blob(mb, mat, (x, y, 0.6 + b * 0.9), (0.14, 0.14, b * 0.1), rng, 0.06)


def bones(mb, x, y, rng, n=8, r=0.6, mat="bone"):
    """Scatter of bones within r meters of (x, y)."""
    for k in range(n):
        a, d, t = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.random()), rng.uniform(0, math.pi)
        px, py, L = x + d * math.cos(a), y + d * math.sin(a), rng.uniform(0.15, 0.4)
        sweep(mb, mat, [(px - L / 2 * math.cos(t), py - L / 2 * math.sin(t), 0.03),
                        (px + L / 2 * math.cos(t), py + L / 2 * math.sin(t), 0.03)], 0.02, 5)
    blob(mb, mat, (x, y, 0.09), (0.1, 0.08, 0.09), rng, 0.05)


def corpse(x, y, rot, rng, coll_):
    """A body on the floor at (x, y), heading `rot`, added to collection coll_ (a room's P)."""
    mb = MB("corpse")
    M = T(x, y, 0.0, rz=rot)
    blob(mb, "fabric", (0, 0, 0.13), (0.32, 0.2, 0.13), rng, 0.08, M=M)
    blob(mb, "mushroom", (0.45, 0.02, 0.1), (0.12, 0.1, 0.1), rng, 0.05, M=M)
    for s in (-1, 1):
        sweep(mb, "fabric", [(-0.25, s * 0.1, 0.09), (-0.6, s * 0.16, 0.08), (-0.95, s * 0.22, 0.07)], 0.075, 6, M)
        sweep(mb, "fabric", [(0.25, s * 0.2, 0.1), (0.35, s * 0.45, 0.07), (0.6, s * 0.6, 0.05)], 0.055, 6, M)
    mb.obj(coll_)


# Small meshes to place many times with inst(mesh, P, T(...)): build the mesh once, instance it per copy.
def bottle_mesh():
    """A bottle, 0.29 m tall, standing on the origin."""
    mb = MB("bottle")
    prof = [(0.033, 0.0), (0.036, 0.015), (0.036, 0.17), (0.03, 0.2), (0.013, 0.24), (0.013, 0.29)]
    loft(mb, "glass", [ring(r, z, 8) for r, z in prof])
    return mb.mesh()


def desk_mesh():
    """An office desk 1.4 x 0.7 m, 0.74 m high, centred on the origin."""
    mb = MB("desk")
    boxmm(mb, "wood", (-0.7, -0.35, 0.7), (0.7, 0.35, 0.74))
    for sx in (-1, 1):
        boxmm(mb, "painted_metal", (sx * 0.66 - 0.03, -0.32, 0), (sx * 0.66 + 0.03, 0.32, 0.7))
    boxmm(mb, "painted_metal", (0.25, -0.3, 0.1), (0.62, 0.3, 0.68))   # drawer pedestal
    return mb.mesh()


def chair_mesh():
    """A swivel chair centred on the origin, backrest on the +y side (the sitter faces -y)."""
    mb = MB("chair")
    cyl(mb, "black_plastic", (0, 0, 0), 0.28, 0.05, 5)
    cyl(mb, "painted_metal", (0, 0, 0.05), 0.03, 0.38, 6)
    boxmm(mb, "black_plastic", (-0.23, -0.23, 0.43), (0.23, 0.23, 0.5))
    boxmm(mb, "black_plastic", (-0.22, 0.2, 0.5), (0.22, 0.26, 0.95))
    return mb.mesh()


def cabinet_mesh():
    """A four-drawer filing cabinet 0.48 x 0.6 m, 1.3 m high, drawers on the -y side."""
    mb = MB("filing_cabinet")
    boxmm(mb, "painted_metal", (-0.24, -0.3, 0), (0.24, 0.3, 1.3))
    for k in range(4):
        boxmm(mb, "black_plastic", (-0.06, -0.32, 0.25 + k * 0.3), (0.06, -0.3, 0.28 + k * 0.3))
    return mb.mesh()


# ============================================================================================== structure helpers
def blockage(q, tan, name, rng, width=None, mat="rubble", slab="concrete_wall", bars=True):
    """Collapse plugging a corridor of `width` at point q (corridor direction `tan`): `mat` to the ceiling, fallen
    slabs of `slab` and, with bars=True, twisted reinforcing bars."""
    hw = (width or CW) / 2
    mb = MB(name)
    M = T(q[0], q[1], 0.0, rz=math.atan2(tan[1], tan[0]))
    boxmm(mb, mat, (-1.3, -hw - 0.1, 0.0), (1.3, hw + 0.1, CH), M)
    for k in range(48):
        t = rng.uniform(-3.8, 3.8)
        env = CH * max(0.12, min(1.0, 1.0 - (abs(t) - 1.2) / 2.8))
        ry_ = rng.uniform(0.5, 0.9)
        lat = rng.uniform(-(hw + 0.4 - ry_), hw + 0.4 - ry_)
        blob(mb, mat, (t, lat, env * 0.45), (rng.uniform(0.5, 1.0), ry_, env * 0.55), rng, 0.3, rng.uniform(-0.5, 0.5), M)
    for k in range(4):
        s = rng.choice([-1, 1])
        a = rng.uniform(25, 55) * DEG
        boxmm(mb, slab, (-hw + 0.2, -0.8, -0.15), (hw - 0.2, 0.8, 0.15),
              M @ T(s * rng.uniform(1.4, 2.4), rng.uniform(-0.6, 0.6), rng.uniform(1.0, 2.2),
                    rz=math.pi / 2 + rng.uniform(-0.3, 0.3)) @ T(rx=s * a))
    for k in range(12 if bars else 0):
        p0 = (rng.uniform(-2.5, 2.5), rng.uniform(-hw + 0.2, hw - 0.2), rng.uniform(0.3, CH - 0.4))
        p1 = (p0[0] + rng.uniform(-0.9, 0.9), p0[1] + rng.uniform(-0.5, 0.5), min(CH, p0[2] + rng.uniform(-0.5, 0.8)))
        sweep(mb, "rust_metal", [p0, p1], 0.025, 4, M)
    mb.obj(C["blockages"])


def cut_holes(ob, cutter_mb):
    """Subtract the solids in cutter_mb from object ob (pits and pools cut out of a floor)."""
    cut = cutter_mb.obj(C["structure"], ob.name + "_cutter")
    mod = ob.modifiers.new("holes", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cut
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.remove(mod)
    old = ob.data
    ob.data = me
    me.name = old.name
    bpy.data.meshes.remove(old)
    cm = cut.data
    bpy.data.objects.remove(cut)
    bpy.data.meshes.remove(cm)


# ============================================================================================== poche contacts
GROUND_LIFT = 0.004


def lift_ground_contacts():
    """Raise vertices lying exactly on the floor plane (z=0) of every non-floor solid by 4 mm.

    A ray entering a solid cut by the plan plane must hit that solid's own bottom (a back face -> black poche) before
    anything else; a floor top coplanar with the solid's bottom would win that depth tie and show through the cut."""
    done = 0
    for me in bpy.data.meshes:
        users = [o for o in bpy.data.objects if o.data is me]
        if not users or any(o.name.startswith(("floor", "floors")) or any(c.name == "decals" for c in o.users_collection)
                            for o in users):
            continue
        ok = all(abs(o.matrix_world[2][3]) < 1e-6 and abs(o.matrix_world[2][0]) < 1e-6 and abs(o.matrix_world[2][1]) < 1e-6
                 for o in users)
        if not ok:
            continue
        n = 0
        for v in me.vertices:
            if abs(v.co.z) < 1e-5:
                v.co.z = GROUND_LIFT
                n += 1
        if n:
            me.update()
            done += 1
    print(f"[mapkit] lifted floor contacts on {done} meshes")


# ============================================================================================== wall cap
CAP_SKIP = {"vents", "decals", "ceilings"}


def apply_cap(cap):
    """Slice every object at height `cap` (plus its own cap_base), except those marked nocap and the vents, decals
    and ceilings. Used for the top-down plan."""
    removed = cut = 0
    for ob in list(bpy.data.objects):
        if ob.type != "MESH" or ob.get("nocap") or any(c.name in CAP_SKIP for c in ob.users_collection):
            continue
        result = cap_object(ob, cap + float(ob.get("cap_base", 0.0)))
        cut += result == "cut"
        removed += result == "removed"
    print(f"[mapkit] wall cap {cap} m: {cut} objects cut, {removed} removed")


def cap_object(ob, h):
    """Slice mesh object `ob` at world height `h` and close the hole: 'cut', 'removed' (entirely above) or None."""
    zs = [(ob.matrix_world @ Vector(c)).z for c in ob.bound_box]
    if max(zs) <= h + 1e-3:
        return None
    if min(zs) >= h - 1e-3:
        bpy.data.objects.remove(ob)
        return "removed"
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    Mw = ob.matrix_world
    A = Mw.to_3x3()
    n_local = (A.transposed() @ Vector((0, 0, 1))).normalized()
    c_local = Mw.inverted() @ Vector((0, 0, h))
    b = bmesh.new()
    b.from_mesh(ob.data)
    bmesh.ops.bisect_plane(b, geom=b.verts[:] + b.edges[:] + b.faces[:], dist=1e-5, plane_co=c_local, plane_no=n_local,
                           clear_outer=True)
    cap_holes(b, n_local)
    bmesh.ops.recalc_face_normals(b, faces=b.faces[:])
    b.to_mesh(ob.data)
    b.free()
    return "cut"


def cap_holes(b, normal):
    """Close the cut: scanfill each island's boundary loops together so tubes/rings get annular caps, not lids."""
    island = {}
    for v in b.verts:
        if v in island:
            continue
        k = len(island)
        stack = [v]
        island[v] = k
        while stack:
            u = stack.pop()
            for e in u.link_edges:
                w = e.other_vert(u)
                if w not in island:
                    island[w] = k
                    stack.append(w)
    groups = {}
    for e in b.edges:
        if e.is_boundary:
            groups.setdefault(island[e.verts[0]], []).append(e)
    for edges in groups.values():
        mat = next((f.material_index for e in edges for f in e.link_faces), 0)
        res = bmesh.ops.triangle_fill(b, use_beauty=True, use_dissolve=False, edges=edges, normal=normal)
        faces = [g for g in res["geom"] if isinstance(g, bmesh.types.BMFace)]
        if not faces:
            faces = bmesh.ops.holes_fill(b, edges=edges, sides=0)["faces"]
        for f in faces:
            f.material_index = mat


# ============================================================================================== previews
def scene_bbox():
    """(low corner, high corner) of everything that renders."""
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for ob in bpy.data.objects:
        if ob.type != "MESH" or any(c.name == "ceilings" for c in ob.users_collection):
            continue
        for c in ob.bound_box:
            p = ob.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def render_previews(outdir, azimuth=-45.0):
    """Quick plan_preview.png and iso_preview.png of the model (Workbench engine, no lighting or textures). The
    iso preview looks from the same side as the poster (poster.json "view": {"azimuth": ...})."""
    outdir.mkdir(parents=True, exist_ok=True)
    scn = bpy.context.scene
    scn.render.engine = "BLENDER_WORKBENCH"
    sh = scn.display.shading
    sh.light = "STUDIO"
    if any(s.name == "studio.sl" for s in bpy.context.preferences.studio_lights):
        sh.studio_light = "studio.sl"
    scn.view_settings.view_transform = "Standard"
    sh.color_type = "MATERIAL"
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_shadows = True
    sh.shadow_intensity = 0.45
    scn.display.light_direction = (0.03, 0.04, 0.998)   # view space: near-vertical so clipped geometry casts no big shadows
    world = bpy.data.worlds.new("preview_world")
    world.color = (0.93, 0.93, 0.9)
    scn.world = world
    scn.render.film_transparent = False
    C["decals"].hide_render = True   # alpha-cut ink stickers only read in Cycles/EEVEE; Workbench draws them as blank quads
    lo, hi = scene_bbox()
    # ---- plan
    cam = bpy.data.cameras.new("plan_cam")
    cam.type = "ORTHO"
    w, h = hi.x - lo.x + 4, hi.y - lo.y + 4
    cam.ortho_scale = max(w, h)
    cam.clip_start = 0.001
    cam.clip_end = 60.0
    co = bpy.data.objects.new("plan_cam", cam)
    co.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, 4.0)
    scn.collection.objects.link(co)
    scn.render.resolution_x = int(2000 * w / max(w, h))
    scn.render.resolution_y = int(2000 * h / max(w, h))
    scn.render.resolution_percentage = 100
    scn.camera = co
    wall = MAT("concrete_wall")
    keep = tuple(wall.diffuse_color)
    wall.diffuse_color = (0.06, 0.06, 0.06, 1.0)
    scn.render.filepath = str(outdir / "plan_preview.png")
    bpy.ops.render.render(write_still=True)
    wall.diffuse_color = keep
    # ---- iso
    cam2 = bpy.data.cameras.new("iso_cam")
    cam2.type = "ORTHO"
    rot = Euler((math.radians(54.7356), 0.0, math.radians(azimuth)), "XYZ")
    R = rot.to_matrix()
    ctr = (lo + hi) / 2
    pts = []
    for ob in bpy.data.objects:
        if ob.type == "MESH" and not any(c.name == "ceilings" for c in ob.users_collection):
            pts += [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    loc = [R.transposed() @ (p - ctr) for p in pts]
    xs, ys = [p.x for p in loc], [p.y for p in loc]
    w2, h2 = max(xs) - min(xs), max(ys) - min(ys)
    off = R @ Vector(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, 0.0))
    cam2.ortho_scale = max(w2, h2) * 1.02
    cam2.clip_start = 1.0
    cam2.clip_end = 2000.0
    co2 = bpy.data.objects.new("iso_cam", cam2)
    co2.rotation_euler = rot
    co2.location = ctr + off + R @ Vector((0, 0, 600.0))
    scn.collection.objects.link(co2)
    scn.render.resolution_x = int(2000 * w2 / max(w2, h2))
    scn.render.resolution_y = int(2000 * h2 / max(w2, h2))
    scn.camera = co2
    scn.display.light_direction = (-0.45, 0.35, 0.82)
    scn.render.filepath = str(outdir / "iso_preview.png")
    bpy.ops.render.render(write_still=True)
    for o in (co, co2):
        bpy.data.objects.remove(o)
    C["decals"].hide_render = False


def stats():
    """(mesh objects, triangles, {collection: (objects, triangles)})."""
    per = {}
    meshes = tris = 0
    for ob in bpy.data.objects:
        n = sum(len(p.vertices) - 2 for p in ob.data.polygons) if ob.type == "MESH" else 0
        if ob.type == "MESH":
            meshes += 1
            tris += n
        for c in ob.users_collection:
            o, t = per.get(c.name, (0, 0))
            per[c.name] = (o + 1, t + n)
    return meshes, tris, per

