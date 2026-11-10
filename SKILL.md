---
name: page-to-poster
description: Turn a written adventure location (PDF pages or text) plus rough mapping guidelines into a 3D Blender model and a print-ready isometric cutaway map poster in a screenprint style, with GM and player versions. Use when asked to map, model or make a poster or battle map of a dungeon, bunker, ship, station or building from an RPG adventure or any written description of a place.
---

# Page to poster

You read the adventure and write five small files that describe the place. The code in this folder checks them,
builds a 3D model in headless Blender, renders it and composes the poster.

```
adventure (PDF/text) + guidelines
   -> brief.md        what the text says about the place          (you write)
   -> layout.json     rooms, passages, entrances, in meters       (you write; `check` validates and draws it)
   -> props.py        what stands in each room                    (you write; `build` models it)
   -> poster.json     title, labels, callouts, text box           (you write; `poster` renders and composes)
   -> out/poster_gm.png, poster_player.png, poster_bare.png  (+ .jpg)
```

`p2p.py` is in this folder. Call it by its full path from the user's working directory, so that project folders
land there and not in here; the commands below write `python p2p.py` for short. Use `python3` where `python` is
not Python 3.

## Rules that hold throughout

1. **Look at every picture you make.** Each step writes an image; open it with your image-reading tool before
   going on. The steps name the file. Most mistakes are obvious in the picture and invisible in the numbers.
2. **Meters, +x east, +y north, floor at z = 0.** Angles in `props.py` are radians.
3. **Say what you estimated.** Adventures rarely give sizes or exact positions. Estimate, write "est." in
   `brief.md`, and list the estimates when you report back. Never present an invented fact as the book's.
4. **Blender runs headless** through `p2p.py`. Do not open its window and do not use a Blender MCP server.
5. **Drafts are cheap.** Draft posters at `--dpi 100`; render the final at `--dpi 200` once the draft is right.
6. The project folder holds the adventure's text and maps. Keep it out of public repositories unless the adventure
   is yours to publish.

## Step 0: check the machine

```
python p2p.py doctor
```

It must end with `Core pipeline: ready`. If Python packages are missing: `pip install -r requirements.txt`. If
Blender is missing, follow [docs/setup.md](docs/setup.md). No GPU is needed (rendering falls back to the CPU), and
ComfyUI is optional: only pass `--comfy URL` if the user gave you one ([docs/comfyui.md](docs/comfyui.md)).

## Step 1: start the project and read the source

```
python p2p.py new PROJECT --source adventure.pdf --pages 53-56
```

`PROJECT` is a new folder for this map, for example `maps/the-site-name` in the user's working directory.
`--pages` are the PDF's own page numbers (first page is 1) for the location to map; leave it out for a short PDF.
A text or Markdown source is copied as is. The command writes templates for the five files and, for a PDF,
`source/page_NNN.txt` and `source/page_NNN.png`.

Read all of the text. Then **look at every page image**: maps, diagrams and sidebars are only in the pictures. If
the book has a map, that map decides where rooms sit; the text decides what is in them.

Fill in `guidelines.md` from what the user asked for; anything they did not mention keeps its default. Ask the user
only if you cannot tell which location to map.

Fill in `brief.md`: one row per room, one per connection, one per way in. Quote page numbers. This is the step
where you decide what is given and what is estimated.

Sizes when the text gives none: a door is 1 m, a corridor 2.5 to 3.5 m, a cell or office 4 x 5 m, an ordinary
room 6 x 8 m, a hall 15 x 25 m, a cavern or hangar 30 x 50 m. Ceilings: 3 m ordinary, 5 to 8 m halls. One map
square is usually 5 feet (1.5 m) in fantasy games; a "pace" is about a meter.

## Step 2: layout.json

Replace the template with the site's rooms, passages and entrances. [docs/layout.md](docs/layout.md) has every key.
Two complete projects to learn from: `examples/brine-chapel/` (a stone dungeon, from text with no map) and
`examples/kestrel-9/` (a prefab station, from a PDF with a sketch map). The essentials:

- A **room** is an axis-aligned rectangle of clear floor: `"min"` is its south-west corner, `"max"` its north-east.
  Leave at least 0.6 m (one wall) between any two rooms.
- A **link** joins two rooms along a `"path"` of points. The first point lies on the edge of room `a`, the last on
  the edge of room `b`; the path leaves each room at a right angle to the wall. `"kind"` is `open`, `blocked`
  (a collapse plugs it) or `vent` (a duct or crawlway, room to room; `"z"` sets its height). `"doors": ["a", "b"]`
  puts doors at those ends. `"secret": true` marks a link the players must not see: its doors become secret
  doors and the player poster leaves its route mark out.
- Where three or more passages meet, name the spot under `"junctions"` and end the paths there.
- An **entrance** is a way in from outside the map: `stairs`, `ramp`, `tunnel` or `door` on a wall
  (`"side": "N|S|E|W"`, `"at"`: position along that wall), or a `shaft` at a point inside a room. A passage that
  leaves the map and is caved in is a `tunnel` with `"blocked": true`.
- A room that is not a rectangle: give it its bounding rectangle, then shape it in `props.py` (`cave_fill` for
  caves and collapses, `partition` for internal walls), or split it into two rooms joined by a short wide link.
- One poster shows one level. For a site with several levels make one project per level and join them with
  entrances labelled "Stairs to level 2".

Lay the site out so it is compact and roughly as deep as it is wide; that fills a 3:2 sheet best in the isometric
view. Put the first room near (0, 0) and work outwards on a 1 m grid.

```
python p2p.py check PROJECT
```

Fix every error (each message says what to change). Then open `PROJECT/build/layout_check.png` and compare it with
the book's map or with your brief: every room present, neighbours on the correct sides, every connection drawn,
doors and entrances on the correct walls. Repeat until it is right. Do not go on with a layout you have not looked at.

## Step 3: textures

```
python p2p.py textures PROJECT
```

This draws the surface textures and decals by code, in a few seconds. With a ComfyUI that passed
`doctor --comfy URL`, add `--comfy URL` to have them painted instead (slower, richer). Run this before the first
build; if you remake textures later, build again.

Open two or three of `PROJECT/textures/_preview/<texture>_tiled.png` (`concrete_floor`, `stone_wall`, `rust_metal`,
`planks`, `tile`, ...) and choose floor and wall materials that suit the place; set them as `"floor"` and `"wall"`
in `layout.json`, for the site or per room. The materials table in
[docs/kit-reference.md](docs/kit-reference.md#materials) lists every material and the texture it uses.

## Step 4: props.py, then build

`props.py` holds one function per room, named `room_<id>`, that places what the brief lists for that room.
[docs/props.md](docs/props.md) explains the kit and shows every ready-made prop;
[docs/kit-reference.md](docs/kit-reference.md) lists every call; `examples/brine-chapel/props.py` is a complete
module. A minimal room:

```python
def room_3(r, rng, P):
    mb = MB("r3_props")                                  # a mesh builder: add solids, then make one object
    pillar(mb, r.minx + 2, r.cy, h=r.h)                  # ready-made props take x, y in meters ...
    altar(mb, T(r.cx, r.maxy - 2, 0, rz=0))              # ... or a transform T(x, y, z, rz=angle)
    box(mb, "wood", (r.cx, r.cy, 0.4), (2.0, 1.0, 0.8))  # anything else: boxes, cylinders, sweeps
    mb.obj(P)                                            # P is this room's collection
    feat("3.altar", r.cx, r.maxy - 2, 1.1)               # a named point a poster callout can hang on
```

What makes a room read on the poster:

- Model what the text names first, and give each a `feat()`. Then add 3 to 10 ordinary things that say what the
  room is for. An empty room looks like a mistake.
- Shapes read by silhouette and tone. Bright materials (`tile`, `bone`, `ice`, `mushroom`, `glass`) print white, red
  ones (`hazard`, `liquid_acid`, `emissive_red`) print in the accent ink, dark ones print black. Things smaller
  than about 0.3 m disappear.
- Keep 1.5 m clear in front of every doorway, and keep props inside the room rectangle.
- Use `rng` for every random choice so each build is identical.
- Pits and pools need the fourth parameter: `def room_4(r, rng, P, cut)`.

```
python p2p.py build PROJECT
python p2p.py render PROJECT closeup --rooms 3,4
```

`build` takes seconds and lists the feature names you registered. Open `PROJECT/build/iso_preview.png` (the whole
model, untextured) after every build, and `PROJECT/renders/closeup_<id>.png` for each room you worked on. Check:
nothing floating, nothing through a wall, doorways clear, the room recognizable from the text. A Python error in
`props.py` is printed with its line number; the full log is `build/build.log`.

Closeups use the view and the cutaway from `poster.json`, the same as the poster will. Walls are cut open at
`"cutaway": {"wall": 3.2}` meters, and each of the two walls nearest the viewer hides a strip of floor about as
wide as that height. That suits halls. **If the rooms are small (under about 8 m) lower it now**, to 1.5 or 2,
or everything along the near walls disappears.

## Step 5: poster.json, then the poster

Edit the template: title, subtitle, footer, one callout per notable feature, and the text boxes.
[docs/poster.md](docs/poster.md) has every key.

- A **callout** is `{"feature": "3.altar", "text": "Salt altar"}`; the feature name is one you registered with
  `feat()`. Add `"gm_only": true` to keep it off the player version. Keep the text under about eight words.
- **`box`** holds the GM's notes (`"gm"`: an encounter table, timers, the one-line truth) and what the players are
  told (`"player"`). Six to eight short lines each.
- The **player** version drops `gm_only` callouts and links marked `"secret"`. The artwork is the same for both, so
  a secret room is still drawn; hide its name with `"room_labels": {"5": {"gm": "Ossuary", "player": ""}}` and tell
  the user that the room itself shows.
- Pick a **palette** that suits the place (paper, ink, white, accent), or keep the default acid yellow-green.
- **`size_in`** is the sheet in inches, `[36, 24]` unless the user asked for another size. Labels shrink with the
  sheet; on a small one raise `"label_scale"` until the callouts are comfortable to read.

```
python p2p.py poster PROJECT --dpi 100
```

Open `PROJECT/out/poster_gm.png` and `poster_player.png`. Check, and fix in `poster.json`:

| Problem | Fix |
|---|---|
| A room label covers the thing that matters in the room | `"room_label_offsets": {"3": [dx, dy]}`, in meters east and north. In the default view east is right-and-up on the sheet and north is left-and-up: `[d, -d]` moves a label right, `[d, d]` up |
| A callout tag sits on something | `"side": "nw"` on that callout puts its tag on that side of the dot (`n` is up the sheet); or shorten, merge or drop callouts in a crowded room |
| Things along the near walls are hidden | lower `"cutaway": {"wall": 1.6}`, then render again |
| Labels too small or too large for the site | `"label_scale": 1.3` |
| Title, box or legend sits on the artwork | `"corners": {"title": "tl", "box": "br", "legend": "bl"}`, or shrink the art with `"art_box"` |
| The interesting side of the site faces away | `"view": {"azimuth": 135}` (try -45, 45, 135, -135), then render again |
| Tall props hide what is behind them | `"cutaway": {"props": {"3": 4.0}}` cuts room 3's props at 4 m |
| Whole print too dark or too pale | `"exposure": 1.15` (lighter) or `0.9` (darker) |
| A callout points at nothing | its `feat()` coordinates in `props.py` are off; fix, build, render |

After changing only text or labels, add `--reuse-render` to skip the render. After changing `layout.json`,
`props.py`, the view or the cutaway, build and render again. When the draft is right:

```
python p2p.py poster PROJECT --dpi 200
```

That writes `out/poster_gm`, `poster_player` and `poster_bare` as PNG and JPEG at the sheet size (36 x 24 inches is
7200 x 4800 pixels). Open the final GM poster once more before you report.

## Optional extras

- **Battle maps.** `python p2p.py render PROJECT plan` gives a top-down plan of the site (`renders/plan.png`);
  `render PROJECT rooms --rooms 3` gives one room at 70 pixels per meter (`renders/room_3.png`).
- **Room paintings** (needs ComfyUI with the depth control LoRA; see [docs/comfyui.md](docs/comfyui.md)). Write
  `paint.json` with one sentence per room, then:
  `python p2p.py render PROJECT eye` and `python p2p.py paint PROJECT --comfy URL`.
  The paintings in `out/paintings/` keep each room's layout because the model's depth map steers them.
  `--strength` (default 1.2, useful range 1.0 to 1.4) sets how firmly.

## Report back

Give the user: the paths of the posters; what you estimated or invented (sizes, positions, anything the text left
open); anything in the guidelines you could not do; and how to change things (edit the files, rerun the command).

## When something goes wrong

| Message or symptom | Cause and fix |
|---|---|
| `Blender: MISSING` | Install it or set the `BLENDER` environment variable to the executable: docs/setup.md |
| `layout.json has errors` on build | Run `check`; each line says what to change |
| `callout ... names feature ..., which props.py never registered` | Spelling differs from the `feat()` name; `build` prints the valid names |
| Surfaces are flat grey | `textures` was not run before `build`; run it, then build again |
| `Failed to retain CUDA context`, or the GPU runs out of memory | Another program is using the GPU: add `--device cpu`, or `--gpu NAME` to pick a card |
| `no GPU device matching ...` | The name given to `--gpu` matches no card; `doctor` lists them |
| The render is slow | It is on the CPU. Draft at `--dpi 100`; the final still finishes, just later |
| A prop is cut off flat at the top | It is taller than the cutaway; that is intended. Raise `"cutaway": {"tall": ...}` to keep it whole |
