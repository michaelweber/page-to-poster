"""Prop zoo: one of every ready-made prop in mapkit.blender.kit, each named with feat() so the poster labels it.

Not an adventure. It is the kit's test scene and the picture in docs/props.md.
"""
from mapkit.blender.kit import *  # noqa: F401,F403


def room_1(r, rng, P, cut):
    mb = MB("zoo_props")

    def show(name, x, y, z=0.0):
        feat(f"1.{name}", x, y, z)

    # row y = 24: big equipment
    container(mb, T(2.0, 22.8, 0.0), holes_s=[(1.0, 2.2, 1.0, 1.8)])
    show("container", 5.0, 22.8, 2.6)
    tank(mb, 13.0, 23.5, r=1.5, h=5.0, rz=-math.pi / 2)
    show("tank", 13.0, 22.0, 2.5)
    machine(mb, T(19.0, 23.5, 0.0), (2.2, 1.3, 1.7), random.Random(1))
    machine(mb, T(23.5, 23.5, 0.0), (1.6, 1.6, 2.2), random.Random(4))
    show("machine", 21.2, 22.6, 1.0)
    console(mb, T(28.0, 24.0, 0.0))
    show("console", 28.0, 23.6, 1.2)
    lockers(mb, T(31.0, 24.5, 0.0), n=6, rng=rng)
    show("lockers", 32.2, 24.5, 1.9)
    shelf(mb, T(36.0, 24.6, 0.0), length=3.0, load="wood", rng=rng)
    show("shelf", 37.5, 24.6, 2.0)

    # row y = 18.5: structure and furniture
    platform(mb, 2.0, 17.0, 6.0, 20.0, 2.2, open_sides="E")
    ladder(mb, 6.15, 18.5, 0.0, 2.2)
    show("platform", 4.0, 17.0, 2.2)
    show("ladder", 6.15, 18.5, 1.2)
    walk_seg(mb, T(8.5, 18.5, 1.6), 4.0)
    for x in (8.7, 12.3):
        box(mb, "rust_metal", (x, 18.5, 0.75), (0.12, 1.2, 1.5))
    show("walk_seg", 10.5, 17.8, 1.6)
    pillar(mb, 15.0, 18.5, h=r.h)
    pillar(mb, 17.6, 18.5, h=r.h, round_=True, mat="stone_wall")
    show("pillar", 15.0, 18.0, 0.3)
    partition(mb, 20.0, 18.5, 25.0, 18.5, doors=[(1.8, 1.2)])
    show("partition", 24.0, 18.4, 2.8)
    door_leaf(mb, T(26.3, 18.0, 0.0, rz=0.9), width=1.2)
    show("door_leaf", 26.6, 18.4, 2.2)
    bunk(mb, T(28.5, 17.6, 0.0))
    show("bunk", 29.5, 17.6, 1.7)
    table(mb, T(33.5, 18.5, 0.0))
    chair, bottle = chair_mesh(), bottle_mesh()
    inst(chair, P, T(33.2, 17.7, 0.0), "zoo_chair_1")
    inst(chair, P, T(33.9, 19.3, 0.0, rz=math.pi), "zoo_chair_2")
    for k in range(3):
        inst(bottle, P, T(33.2 + 0.3 * k, 18.5, 0.75), f"zoo_bottle_{k}")
    show("table", 33.5, 18.1, 0.75)
    inst(desk_mesh(), P, T(38.5, 18.6, 0.0), "zoo_desk")
    inst(cabinet_mesh(), P, T(40.3, 18.9, 0.0, rz=math.pi), "zoo_cabinet")
    show("desk_mesh", 38.5, 18.3, 0.74)

    # row y = 13: holes, liquids, pipes
    rect_pit(mb, cut, 3.0, 12.0, 6.0, 14.5, 2.0)
    show("rect_pit", 4.5, 12.0, 0.0)
    round_pit(mb, cut, 10.0, 13.2, 1.5, 3.0)
    show("round_pit", 10.0, 11.7, 0.0)
    pool(mb, cut, 14.0, 12.0, 18.0, 14.5, rim=None)
    show("pool", 16.0, 12.0, 0.0)
    round_pool(mb, cut, 21.8, 13.2, 1.6, liquid="liquid_acid")
    show("round_pool", 21.8, 11.6, 0.0)
    well(mb, cut, 26.2, 13.2)
    show("well", 26.2, 12.0, 0.8)
    pipe_run(mb, [(29.0, 12.0, 0.4), (29.0, 14.5, 0.4), (32.5, 14.5, 0.4), (32.5, 14.5, 3.0), (32.5, 12.0, 3.0)], r=0.15)
    show("pipe_run", 30.5, 14.5, 0.6)
    for x in (34.5, 38.5):
        cyl(mb, "rust_metal", (x, 13.2, 0.0), 0.06, 3.2, 8)
    cable(mb, (34.5, 13.2, 3.2), (38.5, 13.2, 3.2), sag=1.0)
    show("cable", 36.5, 13.2, 2.2)

    # row y = 7.5: loose things
    crate(mb, 3.0, 7.5, rz=0.3)
    crate(mb, 4.2, 7.9, size=(0.8, 0.8, 0.8), rz=-0.2)
    crate(mb, 3.1, 7.5, size=(0.7, 0.5, 0.5), rz=0.5, z=0.62)
    show("crate", 3.5, 7.0, 0.6)
    barrel(mb, 7.2, 7.8)
    barrel(mb, 8.0, 7.2, mat="hazard")
    barrel(mb, 8.4, 8.3, lying=True, rz=0.6)
    show("barrel", 7.8, 7.0, 0.9)
    rubble_pile(mb, 12.2, 7.6, 1.5, 1.4, rng)
    show("rubble_pile", 12.2, 6.4, 0.3)
    growth(mb, 16.2, 7.6, 1.0, rng, n=24)
    show("growth", 16.2, 6.8, 0.1)
    stalagmites(mb, 20.2, 7.6, 1.1, rng, n=7)
    show("stalagmites", 20.2, 6.7, 0.5)
    debris(mb, Rect("patch", "patch", 22.5, 6.2, 25.5, 9.0, 6, 6), rng, n=14, mats=("dirt", "rust_metal"), margin=0.3)
    show("debris", 24.0, 6.6, 0.0)
    bones(mb, 27.6, 7.6, rng, n=12)
    show("bones", 27.6, 7.1, 0.0)
    corpse(31.0, 7.6, 0.4, rng, P)
    show("corpse", 31.0, 7.2, 0.1)
    lamp("zoo_warm", "POINT", (35.0, 7.6, 2.5), 300.0, WARM)
    cyl(mb, "emissive_warm", (35.0, 7.6, 0.0), 0.12, 0.3, 10)
    show("lamp", 35.0, 7.4, 0.3)

    # row y = 3.5: old stone
    sarcophagus(mb, T(9.5, 3.5, 0.0), ajar=0.25)
    show("sarcophagus", 9.5, 3.0, 0.9)
    altar(mb, T(21.5, 3.6, 0.0))
    show("altar", 21.5, 3.0, 1.1)
    chest(mb, 25.0, 3.6, rz=0.3)
    show("chest", 25.0, 3.3, 0.5)
    statue(mb, 27.8, 3.6, rng=rng)
    show("statue", 27.8, 3.1, 1.5)
    brazier(mb, 34.5, 3.6)
    show("brazier", 34.5, 3.4, 1.0)
    mb.obj(P)

    # decals, along y = 10.3 (only the ones whose images exist are drawn)
    for k, name in enumerate(("crack", "oil_stain", "puddle", "blood_smear", "splatter", "scorch", "footprints")):
        decal(name, 3.0 + 5.0 * k, 10.3, 0.0, 2.2)
        show(f"decal_{name}", 3.0 + 5.0 * k, 10.3, 0.0)
    wall_decal("rust_streak", r, "N", 28.0, 3.0, 3.0)


def room_2(r, rng, P):
    mb = MB("zoo_cave")
    passages = [[(52.0, 14.0), (57.0, 12.0), (61.0, 17.0), (66.0, 15.0), (72.0, 14.0)], [(61.0, 17.0), (62.0, 24.0)]]
    cave_fill(mb, r, passages, rng, half_width=1.5)
    mb.obj(P)
    feat("2.cave_fill", 61.0, 17.0, 0.0)


def room_3(r, rng, P):
    mb = MB("zoo_annex")
    for k in range(3):
        bunk(mb, T(r.minx + 0.4, r.miny + 0.6 + 3.2 * k, 0.0))
    mb.obj(P)


def room_4(r, rng, P):
    mb = MB("zoo_vault")
    for x, y in ((r.minx + 2, r.miny + 2), (r.maxx - 2, r.miny + 2), (r.minx + 2, r.maxy - 2), (r.maxx - 2, r.maxy - 2)):
        pillar(mb, x, y, size=0.7, h=r.h, round_=True, mat="stone_wall")
    chest(mb, r.cx, r.cy)
    mb.obj(P)
