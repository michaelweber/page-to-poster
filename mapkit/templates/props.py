"""Props for this site: one function per room (docs/props.md lists every helper; SKILL.md step 4 says how).

Each function gets
  r    the room: r.minx, r.miny, r.maxx, r.maxy, r.cx, r.cy (centre), r.w, r.d (size) and r.h (height), in meters
  rng  a seeded random.Random: use it for every random choice, so each build comes out the same
  P    the collection this room's objects go into: finish each mesh builder with mb.obj(P)
and, if it names a fourth parameter,
  cut  the floor cutter: solids added to it are subtracted from the room's floor (pits, pools, wells)
Name the spots that should get a poster callout with feat("<room id>.<name>", x, y, z).
"""
from mapkit.blender.kit import *  # noqa: F401,F403


def room_1(r, rng, P):
    mb = MB("r1_props")
    pillar(mb, r.cx - 3, r.cy, h=r.h)
    pillar(mb, r.cx + 3, r.cy, h=r.h)
    for k in range(4):
        x, y = r.rand(rng, 1.5)
        crate(mb, x, y, rz=rng.uniform(0, 3.14))
    mb.obj(P)
    feat("1.crates", r.cx, r.cy)
    decal("oil_stain", r.cx, r.cy - 2, size=2.0)


def room_2(r, rng, P):
    mb = MB("r2_props")
    shelf(mb, T(r.minx + 0.3, r.maxy - 0.8, 0), length=r.w - 0.6, load="wood", rng=rng)
    mb.obj(P)
