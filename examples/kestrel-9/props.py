"""Props for Kestrel-9 (kestrel-9.pdf p2): one function per area.

Each function gets
  r    the room: r.minx, r.miny, r.maxx, r.maxy, r.cx, r.cy (centre), r.w, r.d (size) and r.h (height), in meters
  rng  a seeded random.Random: use it for every random choice, so each build comes out the same
  P    the collection this room's objects go into: finish each mesh builder with mb.obj(P)
and, if it names a fourth parameter,
  cut  the floor cutter: solids added to it are subtracted from the room's floor (pits, pools, wells)
"""
from mapkit.blender.kit import *  # noqa: F401,F403

# Bright, bluish ice: prints white.
material("ice", (0.84, 0.90, 0.95), tex="rock", scale=2.5, rough=0.25, bump=0.4)
# The pit floor: black glass.
material("black_glass", (0.02, 0.02, 0.03), rough=0.05)
# The wall screen's green trace.
material("screen_green", (0.25, 1.0, 0.45), emit=3.0)


def frost(mb, x, y, r, rng, n=10):
    """Low drifted patches of frost on the floor around (x, y)."""
    for _ in range(n):
        a, d = rng.uniform(0, 6.28), rng.uniform(0, r)
        flat_blob(mb, "ice", x + d * math.cos(a), y + d * math.sin(a), rng.uniform(0.3, 0.7), 0.0, 0.04, rng)


def room_1(r, rng, P):
    """Airlock: outer door iced ajar (layout entrance), six suit lockers on the W wall, a bench, boot prints."""
    mb = MB("r1_props")
    lockers(mb, T(r.minx + 0.55, r.miny + 0.6, 0, rz=math.pi / 2), n=6, rng=rng)
    # bench along the east wall
    boxmm(mb, "planks", (r.maxx - 0.55, r.miny + 0.6, 0.42), (r.maxx - 0.1, r.miny + 3.2, 0.48))
    for y in (r.miny + 0.7, r.cy, r.miny + 3.0):
        boxmm(mb, "painted_metal", (r.maxx - 0.5, y - 0.04, 0.0), (r.maxx - 0.15, y + 0.04, 0.42))
    # two suits hanging by the bench, a helmet on it
    for y in (r.miny + 1.2, r.miny + 2.4):
        blob(mb, "fabric", (r.maxx - 0.3, y, 1.3), (0.2, 0.3, 0.55), rng, jit=0.15)
    cyl(mb, "glass", (r.maxx - 0.33, r.miny + 1.8, 0.48), 0.17, 0.3, 12)
    # frost blown in through the open outer door
    frost(mb, r.cx, r.miny + 0.6, 1.4, rng, n=12)
    mb.obj(P)
    for k in range(3):
        decal("footprints", r.cx + rng.uniform(-0.2, 0.2), r.miny + 0.9 + 1.0 * k, size=0.9, rot=math.pi / 2)
    feat("1.outer_door", r.cx, r.miny, 1.4)
    feat("1.lockers", r.minx + 0.3, r.miny + 1.8, 1.9)
    feat("1.bench", r.maxx - 0.3, r.miny + 3.0, 0.5)
    feat("1.prints", r.cx, r.cy, 0.0)


def room_2(r, rng, P):
    """Common Room: long table, six chairs, galley along the N wall, wall screen, four half-eaten meals."""
    mb = MB("r2_props")
    table(mb, T(r.cx, r.cy, 0), length=4.8, depth=1.1)
    # four plates (bright) and two knocked mugs; one place pushed back
    for k, x in enumerate((r.cx - 1.6, r.cx, r.cx + 1.6, r.cx - 0.8)):
        y = r.cy + (0.3 if k % 2 == 0 else -0.3)
        cyl(mb, "tile", (x, y, 0.75), 0.17, 0.03, 14)
        blob(mb, "dirt", (x, y, 0.8), (0.08, 0.07, 0.04), rng)
    # galley counter along the north wall, leaving the north door clear
    for x0, x1 in ((r.minx + 0.2, r.cx - 1.4), (r.cx + 1.4, r.maxx - 0.2)):
        boxmm(mb, "painted_metal", (x0, r.maxy - 0.65, 0.0), (x1, r.maxy - 0.05, 0.9))
        boxmm(mb, "tile", (x0, r.maxy - 0.68, 0.9), (x1, r.maxy - 0.03, 0.95))
    boxmm(mb, "painted_metal", (r.minx + 0.2, r.maxy - 0.45, 1.5), (r.cx - 1.4, r.maxy - 0.05, 2.2))   # cupboards
    boxmm(mb, "glass", (r.maxx - 1.4, r.maxy - 0.65, 0.95), (r.maxx - 0.4, r.maxy - 0.1, 1.35))        # oven
    # the wall screen on the east wall, south of nothing: north of the east door
    boxmm(mb, "black_plastic", (r.maxx - 0.12, r.maxy - 3.0, 1.1), (r.maxx - 0.02, r.maxy - 0.9, 2.2))
    sweep(mb, "screen_green", [(r.maxx - 0.13, r.maxy - 2.9 + 0.1 * k, 1.65 + (0.25 if k in (8, 10) else 0.0))
                               for k in range(20)], 0.025)
    # a supply crate and a cabinet in the south-west corner
    crate(mb, r.minx + 0.8, r.miny + 0.7, rz=0.2)
    crate(mb, r.minx + 0.8, r.miny + 0.7, size=(0.7, 0.5, 0.4), z=0.6, rz=0.5)
    mb.obj(P)
    for k, x in enumerate((r.cx - 1.6, r.cx, r.cx + 1.6)):
        inst(chair_mesh(), P, T(x, r.cy + 0.85, 0), "r2_chair_n%d" % k)
        rz = math.pi + (0.6 if k == 2 else 0.0)
        inst(chair_mesh(), P, T(x + (0.3 if k == 2 else 0.0), r.cy - 0.9 - (0.4 if k == 2 else 0.0), 0, rz=rz),
             "r2_chair_s%d" % k)
    feat("2.table", r.cx + 1.6, r.cy, 0.8)
    feat("2.meals", r.cx - 1.6, r.cy + 0.3, 0.8)
    feat("2.galley", r.minx + 2.0, r.maxy - 0.4, 0.95)
    feat("2.screen", r.maxx - 0.1, r.maxy - 2.0, 1.9)


def room_3(r, rng, P):
    """Bunks: three two-tier bunks (one stripped), six lockers."""
    mb = MB("r3_props")
    for k, y in enumerate((r.miny + 0.2, r.miny + 2.55, r.maxy - 1.1)):
        bunk(mb, T(r.minx + 0.1, y, 0), bedding="painted_metal" if k == 1 else "fabric")
    lockers(mb, T(r.maxx - 2.6, r.maxy - 0.55, 0), n=6, rng=rng)
    crate(mb, r.minx + 3.0, r.miny + 0.5, size=(0.8, 0.5, 0.4), rz=0.1)
    boxmm(mb, "wood", (r.maxx - 1.4, r.miny + 0.2, 0.0), (r.maxx - 0.2, r.miny + 0.8, 0.75))   # small desk
    mb.obj(P)
    inst(chair_mesh(), P, T(r.maxx - 0.8, r.miny + 1.3, 0, rz=math.pi), "r3_chair")
    feat("3.bunks", r.minx + 1.0, r.maxy - 0.6, 1.6)
    feat("3.stripped", r.minx + 1.0, r.miny + 3.0, 0.6)
    feat("3.lockers", r.maxx - 1.4, r.maxy - 0.3, 1.9)


def room_4(r, rng, P):
    """Control: two consoles, the relay rack with its master breaker, the log; the hatch is behind the rack."""
    mb = MB("r4_props")
    # relay rack against the east wall, in front of the maintenance hatch
    rx0, rx1, ry0, ry1 = r.maxx - 0.8, r.maxx - 0.1, r.cy - 1.3, r.cy + 1.3
    boxmm(mb, "painted_metal", (rx0, ry0, 0.0), (rx1, ry1, 2.3))
    for k in range(5):
        boxmm(mb, "black_plastic", (rx0 - 0.03, ry0 + 0.1, 0.3 + 0.4 * k), (rx0, ry1 - 0.1, 0.6 + 0.4 * k))
        for j in range(6):
            box(mb, "emissive_warm", (rx0 - 0.04, ry0 + 0.3 + 0.35 * j, 0.5 + 0.4 * k), (0.02, 0.06, 0.06))
    boxmm(mb, "hazard", (rx0 - 0.2, r.cy - 0.25, 1.1), (rx0, r.cy + 0.25, 1.6))        # master breaker
    # two consoles: one on the north wall, one on the south wall
    console(mb, T(r.cx - 0.3, r.maxy - 0.45, 0), width=1.8)
    console(mb, T(r.cx - 0.3, r.miny + 0.45, 0, rz=math.pi), width=1.8)
    boxmm(mb, "tile", (r.cx - 0.6, r.maxy - 0.75, 0.95), (r.cx - 0.2, r.maxy - 0.45, 1.0))   # the log
    cable(mb, (rx0, ry1, 2.2), (r.cx + 0.6, r.maxy - 0.3, 1.0), sag=0.4)
    cable(mb, (rx0, ry0, 2.2), (r.cx + 0.6, r.miny + 0.3, 1.0), sag=0.4)
    mb.obj(P)
    inst(chair_mesh(), P, T(r.cx - 0.3, r.maxy - 1.4, 0, rz=math.pi), "r4_chair_n")
    inst(chair_mesh(), P, T(r.cx - 0.1, r.miny + 1.4, 0, rz=0.3), "r4_chair_s")
    feat("4.rack", rx0, ry1 - 0.2, 2.3)
    feat("4.breaker", rx0 - 0.1, r.cy, 1.35)
    feat("4.log", r.cx - 0.4, r.maxy - 0.6, 1.0)
    feat("4.consoles", r.cx - 0.3, r.miny + 0.45, 1.1)
    feat("4.hatch", r.maxx, r.cy, 1.8)


def room_5(r, rng, P):
    """Generator Hall: two generators, fuel tank on a cradle, a rank of drums, catwalk on the E wall."""
    mb = MB("r5_props")
    # two generators side by side in the west half
    for k, y in enumerate((r.cy - 2.0, r.cy + 2.0)):
        machine(mb, T(r.minx + 3.5, y, 0), size=(5.0, 2.6, 2.8), rng=rng)
        pipe_run(mb, [(r.minx + 6.0, y, 2.4), (r.minx + 7.5, y, 2.4), (r.minx + 7.5, y, 5.4),
                      (r.maxx - 1.8, y, 5.4)], r=0.14)
    # fuel tank lying north-south on a cradle
    tx, ty = r.cx + 2.5, r.cy + 1.5
    cyl(mb, "rust_metal", (0, 0, -3.0), 1.3, 6.0, 24, T(tx, ty, 1.8, rx=-math.pi / 2))
    for s in (-1, 1):
        cyl(mb, "rust_metal", (0, 0, 3.0 * s - (0.1 if s > 0 else 0.0)), 1.35, 0.1, 24, T(tx, ty, 1.8, rx=-math.pi / 2))
        boxmm(mb, "hazard", (tx - 1.3, ty + 2.0 * s - 0.25, 0.0), (tx + 1.3, ty + 2.0 * s + 0.25, 0.9))
    ladder(mb, tx - 1.45, ty, 0.0, 3.0, rz=0.0)
    # a rank of fuel drums along the south wall, east of the door
    for k in range(9):
        barrel(mb, r.cx + 2.0 + 0.72 * k, r.miny + 0.5)
    for k in range(5):
        barrel(mb, r.cx + 2.4 + 0.72 * k, r.miny + 1.2)
    barrel(mb, r.cx + 1.6, r.miny + 1.6, lying=True, rz=0.7)
    # catwalk along the east wall, with a ladder at its south end
    platform(mb, r.maxx - 1.5, r.miny + 1.6, r.maxx - 0.05, r.maxy - 0.3, 3.4, open_sides="E")
    ladder(mb, r.maxx - 0.8, r.miny + 1.3, 0.0, 3.4, rz=math.pi / 2)
    # cable tray from the catwalk to the duct mouth
    pipe_run(mb, [(r.maxx - 0.3, r.maxy - 5.0, 3.4), (r.maxx - 0.3, r.maxy - 2.0, 3.4),
                  (r.maxx - 0.3, r.maxy - 2.0, 2.2)], r=0.1, flanges=0)
    frost(mb, r.cx, r.maxy - 1.0, 2.5, rng, n=16)          # drift creeping under the ramp slab
    mb.obj(P)
    decal("oil_stain", r.minx + 3.5, r.cy, size=2.5, rot=0.4)
    decal("oil_stain", r.minx + 6.5, r.cy + 3.5, size=1.5, rot=1.4)
    feat("5.generators", r.minx + 3.5, r.cy + 2.0, 2.8)
    feat("5.tank", tx, ty, 3.1)
    feat("5.drums", r.cx + 5.0, r.miny + 0.5, 0.9)
    feat("5.catwalk", r.maxx - 0.8, r.cy - 2.0, 3.5)
    feat("5.duct", r.maxx, r.maxy - 2.0, 1.8)


def room_6(r, rng, P, cut):
    """Ice Lab: drill rig over a round pit rimmed with warning paint, core racks on the S wall, Maro in the pit."""
    mb = MB("r6_props")
    px, py = r.cx, r.cy + 0.5
    round_pit(mb, cut, px, py, 1.5, 8.0, wall="ice", floor="black_glass")
    # Dr. Maro at the bottom, frozen in to the chest
    blob(mb, "ice", (px + 0.3, py, -7.7), (0.9, 0.8, 0.45), rng)
    cyl(mb, "fabric", (px + 0.3, py, -7.5), 0.22, 0.6, 10)
    blob(mb, "bone", (px + 0.3, py, -6.75), (0.13, 0.13, 0.15), rng, jit=0.1)
    # the drill rig: four legs, a head frame, a motor and the drill string
    for sx in (-1, 1):
        for sy in (-1, 1):
            sweep(mb, "rust_metal", [(px + 2.1 * sx, py + 2.1 * sy, 0.0), (px + 0.5 * sx, py + 0.5 * sy, 2.6)], 0.08)
    boxmm(mb, "painted_metal", (px - 0.7, py - 0.7, 2.5), (px + 0.7, py + 0.7, 2.8))
    boxmm(mb, "hazard", (px - 0.4, py - 0.4, 1.9), (px + 0.4, py + 0.4, 2.5))
    cyl(mb, "rust_metal", (px, py, -6.0), 0.1, 7.9, 10)
    # core racks along the south wall, either side of the door
    for x0, x1 in ((r.minx + 0.2, r.cx - 1.6), (r.cx + 1.6, r.maxx - 0.2)):
        shelf(mb, T(x0, r.miny + 0.15, 0), length=x1 - x0, depth=0.5, height=1.8, levels=4)
        for z in (0.4, 0.85, 1.3, 1.75):
            for k in range(int((x1 - x0) / 0.4)):
                cyl(mb, "ice", (0, 0, 0), 0.06, 0.45, 8,
                    T(x0 + 0.2 + 0.4 * k, r.miny + 0.18, z - 0.3, rx=-math.pi / 2))
    # a bench with instruments on the west wall (keeping the duct mouth at y = maxy - 4 clear below it)
    boxmm(mb, "painted_metal", (r.minx + 0.1, r.cy - 3.0, 0.0), (r.minx + 0.8, r.cy - 0.5, 0.9))
    for k in range(3):
        box(mb, "glass", (r.minx + 0.45, r.cy - 2.6 + 0.8 * k, 1.05), (0.4, 0.4, 0.3))
    # a sample freezer and crates in the north-west
    boxmm(mb, "tile", (r.minx + 0.2, r.maxy - 1.0, 0.0), (r.minx + 1.6, r.maxy - 0.2, 1.1))
    crate(mb, r.maxx - 1.0, r.maxy - 1.0, rz=0.3)
    crate(mb, r.maxx - 1.0, r.cy - 1.0, rz=-0.2)
    # loose things already drifted toward the pit
    for k in range(5):
        a = rng.uniform(0, 6.28)
        crate(mb, px + 2.6 * math.cos(a), py + 2.6 * math.sin(a), size=(0.4, 0.3, 0.25), rz=a)
    frost(mb, 24.0, r.maxy - 0.8, 1.5, rng, n=12)
    mb.obj(P)
    feat("6.rig", px, py - 0.5, 2.8)
    feat("6.pit", px - 1.4, py - 0.3, 0.05)
    feat("6.maro", px + 0.4, py + 0.4, 0.0)
    feat("6.cores", r.minx + 2.0, r.miny + 0.4, 1.8)
    feat("6.freezer", r.minx + 0.9, r.maxy - 0.6, 1.1)


def room_7(r, rng, P):
    """Antenna Shaft: foot of the mast (the open shaft with its ladder is an entrance), Rask's nest, dead heater."""
    mb = MB("r7_props")
    mx, my = r.maxx - 1.2, r.maxy - 1.4                       # the shaft entrance in layout.json
    for sx in (-1, 1):                                       # the mast's four legs and braces
        for sy in (-1, 1):
            sweep(mb, "rust_metal", [(mx + 0.95 * sx, my + 0.95 * sy, 0.0), (mx + 0.6 * sx, my + 0.6 * sy, r.h)], 0.07)
    for z in (1.0, 3.0, 5.0, 7.0):
        pts = [(mx - 0.9, my - 0.9), (mx + 0.9, my - 0.9), (mx + 0.9, my + 0.9), (mx - 0.9, my + 0.9), (mx - 0.9, my - 0.9)]
        sweep(mb, "rust_metal", [(x, y, z) for x, y in pts], 0.04)
    frost(mb, mx, my, 1.0, rng, n=8)
    # Rask's nest in the south-west corner: bedding, ration tins
    flat_blob(mb, "fabric", r.minx + 1.1, r.miny + 0.9, 0.9, 0.0, 0.18, rng, sx=1.2)
    blob(mb, "fabric", (r.minx + 0.6, r.miny + 0.5, 0.25), (0.4, 0.3, 0.2), rng)
    for k in range(6):
        cyl(mb, "tile", (r.minx + 2.2 + 0.18 * (k % 3), r.miny + 0.4 + 0.18 * (k // 3), 0.0), 0.08, 0.14, 8)
    crate(mb, r.minx + 2.6, r.miny + 1.0, size=(0.6, 0.4, 0.35), rz=0.4)
    # the heater that does not work
    boxmm(mb, "painted_metal", (r.maxx - 1.0, r.miny + 0.15, 0.0), (r.maxx - 0.2, r.miny + 0.55, 0.8))
    boxmm(mb, "black_plastic", (r.maxx - 0.9, r.miny + 0.53, 0.2), (r.maxx - 0.3, r.miny + 0.58, 0.65))
    mb.obj(P)
    feat("7.nest", r.minx + 1.1, r.miny + 0.9, 0.3)
    feat("7.heater", r.maxx - 0.6, r.miny + 0.35, 0.8)
    feat("7.mast", mx, my, 4.0)
    feat("7.hatch", r.minx, r.cy, 1.8)


def extras(rooms, rng, P):
    """The old tunnel north of the lab, packed with ice from the fallen roof."""
    mb = MB("tunnel_ice")
    x = 24.0
    y = 33.4
    while y < 40.5:
        for dx in (-0.7, 0.0, 0.7):
            blob(mb, "ice", (x + dx + rng.uniform(-0.2, 0.2), y, rng.uniform(0.4, 1.4)),
                 (rng.uniform(0.5, 0.8), rng.uniform(0.5, 0.8), rng.uniform(0.5, 0.9)), rng, jit=0.3)
        y += 0.9
    mb.obj(P)
    feat("6.tunnel_ice", x, 34.5, 2.0)
