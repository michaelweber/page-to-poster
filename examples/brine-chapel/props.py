"""Props for The Brine Chapel: one function per room of source/adventure.md.

Most things come ready-made from the kit (pillar, altar, sarcophagus, pool, cave_fill, ...). The rest (pews, the
winding gear, the beam pump, the winch, salt crystals) are put together here from boxes, cylinders and sweeps, to
show how little it takes.
"""
from mapkit.blender.kit import *  # noqa: F401,F403

# A material of our own: bright, so it prints white on the poster. It borrows the rock texture.
material("salt", (0.86, 0.86, 0.82), tex="rock", scale=1.5, rough=0.6, bump=0.5)


def pew(mb, x, y, length=5.0, rz=0.0):
    """A plank bench with a back rest, centred on (x, y) and running along its local x; the sitter faces +y."""
    M = T(x, y, 0, rz=rz)
    boxmm(mb, "planks", (-length / 2, -0.2, 0.42), (length / 2, 0.2, 0.47), M)
    boxmm(mb, "planks", (-length / 2, -0.25, 0.3), (length / 2, -0.2, 0.95), M)
    for ex in (-length / 2, -0.03, length / 2 - 0.06):
        boxmm(mb, "wood", (ex, -0.25, 0.0), (ex + 0.06, 0.2, 0.47), M)


def crystals(mb, x, y, rng, n=7, h=1.6):
    """A cluster of leaning six-sided salt crystals."""
    for _ in range(n):
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(0.0, 0.5)
        hh = h * rng.uniform(0.4, 1.0)
        M = T(x + d * math.cos(a), y + d * math.sin(a), 0, rz=a, ry=rng.uniform(0.1, 0.6))
        frustum(mb, "salt", (0, 0, -0.1), hh * 0.11, hh * 0.07, hh, 6, M)
        frustum(mb, "salt", (0, 0, hh - 0.1), hh * 0.07, 0.01, hh * 0.18, 6, M)


def room_1(r, rng, P):
    """Head House: seized winding gear, lamp-oil crates, candle wax leading east."""
    mb = MB("r1_props")
    gx, gy = r.cx + 2.0, r.maxy - 2.6
    along_x = T(gx, gy, 1.7, ry=math.pi / 2)          # turns an upright cylinder to lie east-west
    for s in (-1, 1):                                 # two timber A-frames carry the drum
        for lean in (-1, 1):
            sweep(mb, "wood", [(gx + s * 1.3, gy + lean * 0.9, 0.0), (gx + s * 1.3, gy, 2.4)], 0.09)
    cyl(mb, "rust_metal", (0, 0, -1.2), 0.55, 2.4, 16, along_x)
    cyl(mb, "rust_metal", (0, 0, -1.6), 0.06, 3.2, 8, along_x)
    tube(mb, "rust_metal", (0, 0), 0.62, 0.72, 1.5, 1.58, 20, along_x)     # the crank wheel
    sweep(mb, "black_plastic", [(gx, gy, 2.2), (gx, gy, r.h)], 0.03)       # rope, up through the roof
    for dx, dy, rz, z in ((0.0, 0.0, 0.1, 0.0), (1.2, 0.2, -0.2, 0.0), (0.3, 1.0, 0.4, 0.0), (0.4, 0.1, 0.3, 0.62)):
        crate(mb, r.maxx - 3.0 + dx, r.miny + 1.2 + dy, rz=rz, z=z)
    barrel(mb, r.maxx - 0.9, r.miny + 3.2, mat="wood")
    mb.obj(P)
    feat("1.winding_gear", gx, gy, 2.4)
    feat("1.lamp_oil", r.maxx - 2.5, r.miny + 1.5, 0.7)
    for k in range(3):
        decal("splatter", r.minx + 3.0 + 3.0 * k, r.cy + rng.uniform(-0.4, 0.4), 0.0, 0.9, rng.uniform(0, 6.28))


def room_2(r, rng, P):
    """Tally Room: desk with the ledger, pigeonholes, lockers, weighing table."""
    mb = MB("r2_props")
    shelf(mb, T(r.minx + 0.3, r.maxy - 0.6, 0), length=r.w - 0.6, mat="wood", load="wood", rng=rng)
    lockers(mb, T(r.minx + 0.55, r.miny + 0.5, 0, rz=math.pi / 2), n=8, rng=rng)
    table(mb, T(r.maxx - 1.2, r.cy, 0, rz=math.pi / 2), length=2.4)
    mb.obj(P)
    inst(desk_mesh(), P, T(r.cx, r.cy + 0.4, 0), "r2_desk")
    inst(chair_mesh(), P, T(r.cx, r.cy + 1.2, 0), "r2_chair")
    feat("2.ledger", r.cx, r.cy + 0.4, 0.8)


def room_3(r, rng, P):
    """Nave: eight pillars, pews north of the cross-aisle, salt altar, braziers, the Saint and a broken twin."""
    pillars = MB("r3_pillars")
    for x in (r.minx + 4.0, r.maxx - 4.0):
        for y in (1.5, 8.0, 11.0, 14.0):              # leaves the cross-aisle (y 3.5..6.5) clear
            pillar(pillars, x, y, size=1.0, h=r.h, round_=True, mat="salt")
    pillars.obj(P)
    mb = MB("r3_props")
    for y in (8.0, 9.6, 11.2):
        pew(mb, r.cx - 4.0, y)
        pew(mb, r.cx + 4.0, y)
    pew(mb, r.cx - 5.0, 1.0, rz=0.5)                  # one dragged into the narthex
    altar(mb, T(r.cx, r.maxy - 2.2, 0), mat="salt")
    brazier(mb, r.cx - 1.8, r.maxy - 2.2)
    brazier(mb, r.cx + 1.8, r.maxy - 2.2)
    statue(mb, r.cx - 4.5, r.maxy - 1.4, h=3.6, rng=rng, mat="salt")
    box(mb, "salt", (r.cx + 4.5, r.maxy - 1.4, 0.3), (0.9, 0.9, 0.6))      # the empty plinth
    rubble_pile(mb, r.cx + 5.0, r.maxy - 2.6, 1.0, 0.6, rng, mat="salt")
    debris(mb, r, rng, n=16, mats=("dirt",), margin=1.5)
    mb.obj(P)
    feat("3.altar", r.cx, r.maxy - 2.2, 1.1)
    feat("3.braziers", r.cx + 1.8, r.maxy - 2.2, 1.0)
    feat("3.saint", r.cx - 4.5, r.maxy - 1.4, 3.0)
    feat("3.broken_statue", r.cx + 5.0, r.maxy - 2.6, 0.5)
    feat("3.secret_slab", r.cx, r.maxy, 1.5)
    feat("3.pews", r.cx + 4.0, 9.6, 0.9)


def room_4(r, rng, P, cut):
    """Brine Sump: the pool, a plank walkway across it, the beam pump on the north bank, the drain."""
    mb = MB("r4_props")
    x0, y0, x1, y1 = r.minx + 3.0, r.miny + 2.5, r.maxx - 2.0, r.maxy - 3.0
    pool(mb, cut, x0, y0, x1, y1, depth=3.0, level=-0.4, wall="stone_wall", floor="stone_floor", rim=None)
    wx = r.cx + 2.0
    boxmm(mb, "planks", (wx - 0.6, y0 - 0.5, 0.06), (wx + 0.6, y1 + 0.5, 0.14))
    y = y0 + 1.0
    while y < y1:                                      # trestles standing in the brine
        for s in (-1, 1):
            boxmm(mb, "wood", (wx + s * 0.5 - 0.07, y - 0.07, -3.0), (wx + s * 0.5 + 0.07, y + 0.07, 0.06))
        boxmm(mb, "wood", (wx - 0.6, y - 0.06, -0.08), (wx + 0.6, y + 0.06, 0.06))
        y += 2.0
    px, py = r.maxx - 3.0, r.maxy - 1.5                # the beam pump
    boxmm(mb, "stone_wall", (px - 0.8, py - 0.6, 0.0), (px + 0.8, py + 0.6, 0.4))
    cyl(mb, "rust_metal", (px, py, 0.4), 0.35, 1.3, 14)
    cyl(mb, "rust_metal", (px, py, 1.7), 0.07, 0.6, 8)
    boxmm(mb, "wood", (px - 1.5, py - 0.09, 0.0), (px - 1.32, py + 0.09, 2.3))
    box(mb, "wood", (0, 0, 0), (3.0, 0.14, 0.16), T(px - 0.9, py, 2.3, ry=0.12))
    pipe_run(mb, [(px, py - 0.35, 0.8), (px, y1 - 0.5, 0.8), (px, y1 - 0.5, -2.4)], r=0.1, flanges=0)
    bones(mb, r.maxx - 1.0, r.miny + 4.0, rng, n=8)
    mb.obj(P)
    feat("4.pool", r.cx - 1.5, r.cy - 1.0, -0.4)
    feat("4.walkway", wx, r.cy - 2.0, 0.2)
    feat("4.pump", px, py, 1.8)
    feat("4.drain", 59.0, r.maxy, 0.6)
    decal("puddle", r.minx + 1.6, r.cy + 2.5, 0.0, 1.8, 0.6)


def room_5(r, rng, P):
    """Ossuary: bone niches on two walls, three sarcophagi (the middle one is a bed), the keeper's chest."""
    mb = MB("r5_props")
    for k, dx in enumerate((-3.2, 0.0, 3.2)):
        sarcophagus(mb, T(r.cx + dx, r.cy + 0.6, 0, rz=math.pi / 2), ajar=0.4 if k == 1 else 0.0)
    shelf(mb, T(r.minx + 0.3, r.maxy - 0.6, 0), length=r.w - 0.6, height=2.4, mat="stone_wall", load="bone", rng=rng)
    shelf(mb, T(r.minx + 0.6, r.miny + 0.4, 0, rz=math.pi / 2), length=r.d - 1.4, height=2.4, mat="stone_wall",
          load="bone", rng=rng)
    chest(mb, r.maxx - 1.4, r.maxy - 1.7, rz=-0.5)
    bones(mb, r.minx + 2.6, r.miny + 1.6, rng, n=10)
    mb.obj(P)
    feat("5.bed", r.cx, r.cy + 0.6, 0.9)
    feat("5.chest", r.maxx - 1.4, r.maxy - 1.7, 0.5)
    feat("5.niches", r.cx - 3.0, r.maxy - 0.4, 1.8)


def room_6(r, rng, P):
    """Collapsed Gallery: rock falls with two crawls that meet in the middle; the way east is plugged."""
    mb = MB("r6_rockfall")
    crawls = [[(26.0, r.maxy), (27.0, -16.0), (31.0, -19.0), (36.5, -18.5), (r.maxx, -18.0)],
              [(32.0, r.maxy), (33.0, -15.5), (31.0, -19.0)]]
    cave_fill(mb, r, crawls, rng, half_width=1.2, mat="rubble")
    mb.obj(P)
    feat("6.crawls", 31.0, -19.0, 0.3)


def room_7(r, rng, P):
    """Lower Winch: a hand winch over the shaft (the shaft itself is an entrance in layout.json), pitch, rope."""
    mb = MB("r7_props")
    sx, sy = 57.0, -19.0
    for s in (-1, 1):
        boxmm(mb, "wood", (sx + s * 1.7 - 0.1, sy - 0.1, 0.0), (sx + s * 1.7 + 0.1, sy + 0.1, 2.6))
        for lean in (-1, 1):
            sweep(mb, "wood", [(sx + s * 1.7, sy + lean * 1.0, 0.0), (sx + s * 1.7, sy, 2.2)], 0.07)
    boxmm(mb, "wood", (sx - 1.9, sy - 0.1, 2.6), (sx + 1.9, sy + 0.1, 2.8))
    cyl(mb, "wood", (0, 0, -1.6), 0.22, 3.2, 10, T(sx, sy, 1.5, ry=math.pi / 2))
    sweep(mb, "black_plastic", [(sx, sy, 1.5), (sx, sy, -7.5)], 0.03)
    for k in range(5):
        barrel(mb, r.maxx - 1.0 - 0.8 * (k % 3), r.miny + 1.0 + 0.8 * (k // 3), mat="wood")
    shelf(mb, T(r.minx + 0.3, r.miny + 0.3, 0), length=4.0, mat="wood", load="fabric", rng=rng)
    boxmm(mb, "wood", (58.0 - 1.1, r.maxy - 0.28, 1.0), (58.0 + 1.1, r.maxy - 0.12, 1.16))   # the bar on the door
    mb.obj(P)
    feat("7.winch", sx, sy, 2.7)
    feat("7.barred_door", 58.0, r.maxy, 1.1)
    feat("7.pitch", r.maxx - 1.8, r.miny + 1.4, 0.9)


def room_8(r, rng, P):
    """Crystal Grotto: a cave (rock everywhere but an open loop), salt crystals, the smugglers' cache."""
    rock = MB("r8_rock")
    open_floor = [[(6.0, r.maxy), (6.5, -16.0), (4.0, -19.0), (r.minx, -19.0)],
                  [(6.5, -16.0), (10.0, -18.5), (9.5, -22.5), (5.0, -23.0), (4.0, -19.0)]]
    cave_fill(rock, r, open_floor, rng, half_width=2.3)
    rock.obj(P)
    mb = MB("r8_props")
    for x, y in ((7.3, -19.9), (11.3, -21.6), (3.4, -22.4)):
        crystals(mb, x, y, rng)
    stalagmites(mb, 8.0, -24.0, 0.9, rng, n=5)
    crate(mb, 10.6, -17.0, rz=0.2)
    crate(mb, 11.6, -17.7, rz=-0.3)
    crate(mb, 10.7, -17.1, size=(0.7, 0.5, 0.5), rz=0.6, z=0.62)
    barrel(mb, 9.5, -16.4, mat="wood")
    barrel(mb, 12.0, -16.6, mat="wood", lying=True, rz=0.4)
    flat_blob(mb, "fabric", 9.0, -18.4, 0.9, 0.0, 0.08, rng, sx=1.8)        # the bedroll
    cyl(mb, "emissive_warm", (9.9, -18.9, 0.0), 0.07, 0.25, 8)              # the shuttered lantern
    mb.obj(P)
    lamp("r8_lantern", "POINT", (9.9, -18.9, 0.5), 60.0, WARM, map_dim=0.3)
    feat("8.crystals", 7.3, -19.9, 1.2)
    feat("8.cache", 10.9, -17.3, 0.7)


def extras(rooms, rng, P):
    """The signpost at the three-way junction, and the wax trail from the head house to the nave."""
    mb = MB("signpost")
    x, y = 7.3, -4.7
    cyl(mb, "wood", (x, y, 0.0), 0.07, 2.2, 8)
    for k, rz in enumerate((math.pi / 2, 0.0, -math.pi / 2)):              # boards pointing north, east, south
        boxmm(mb, "planks", (0.05, -0.02, 1.9 - 0.28 * k), (0.9, 0.02, 2.1 - 0.28 * k), T(x, y, 0, rz=rz))
    mb.obj(P)
    feat("J1.signpost", x, y, 2.0)
    for k in range(3):
        decal("splatter", 13.5 + 2.2 * k, 5.0 + rng.uniform(-0.5, 0.5), 0.0, 0.7, rng.uniform(0, 6.28))
