"""Export the client mouse cursors (data\\sprite\\cursors.spr + .act).

RO keeps every mouse-cursor state in one SPR/ACT pair: `cursors.act` has one
*action* per state (normal arrow, talk hand, the animated skill-target ring, …)
and each action is a short frame animation. The client swaps action by state
and cycles its frames.

We render each action's frames to individual PNGs plus a small JSON, so the
Godot side can set a custom mouse cursor (and animate it):

  cursor/<name>/f00.png, f01.png, ...   frames, RGBA, tightly composited
  cursor/<name>/cursor.json             { "delay": ms, "hotspot": [x, y],
                                          "frames": ["f00.png", ...] }

The ground half of the cursor ships separately and is written alongside them:

  cursor/grid.png                       data\\texture\\grid.tga

That is the cell bracket the official client draws under the pointer -- four
white corner marks with a hollow middle, not a filled square. It is a TGA with
real alpha rather than a magenta-keyed BMP, so it needs no keying.

`hotspot` is where the click actually lands (the ACT origin), in the PNG's
pixel space — what `Input.set_custom_mouse_cursor` wants.

Action names follow RO's conventional cursor order; unknown indices fall back
to `action_NN`.

Usage:
    python -m ragnadot.cursor_export --out C:/Users/pedro/Documents/ragnarok
    python -m ragnadot.cursor_export --only target --out ...
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from ragx.formats import act as actfmt, spr as sprfmt
from ragx.grf import GrfStack

# RO's cursor action order (data\sprite\cursors.act). Names are ours.
CURSOR_NAMES = {
	0: "normal", 1: "wait", 2: "click", 3: "target_lock", 4: "select",
	5: "sign", 6: "no", 7: "warp", 8: "forbidden", 9: "grab",
	10: "target", 11: "target2", 12: "sign2", 13: "arrow",
}
DELAY_UNIT_MS = 25   # ACT delay is in 25 ms units


def _composite(frame, spr) -> tuple[bytes, int, int, int, int]:
	"""Composite one ACT frame's layers to a PNG. Returns (png, w, h, hx, hy)
	where (hx, hy) is the ACT origin (0,0) — the cursor hotspot — in the PNG."""
	from PIL import Image

	placed = []
	minx = miny = 10_000
	maxx = maxy = -10_000
	for layer in frame.layers:
		if layer.sprite_index < 0:
			continue
		w, h, rgba = spr.frame_rgba(layer.sprite_type, layer.sprite_index)
		im = Image.frombytes("RGBA", (w, h), bytes(rgba))
		if layer.mirror:
			im = im.transpose(Image.FLIP_LEFT_RIGHT)
		# top-left of this layer in ACT space (origin at 0,0)
		lx = layer.x - im.width // 2
		ly = layer.y - im.height // 2
		placed.append((lx, ly, im))
		minx, miny = min(minx, lx), min(miny, ly)
		maxx, maxy = max(maxx, lx + im.width), max(maxy, ly + im.height)

	if not placed:
		return b"", 0, 0, 0, 0
	w, h = maxx - minx, maxy - miny
	canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
	for lx, ly, im in placed:
		canvas.alpha_composite(im, (lx - minx, ly - miny))
	out = io.BytesIO()
	canvas.save(out, "PNG")
	# hotspot = ACT origin (0,0) mapped into the cropped canvas
	return out.getvalue(), w, h, -minx, -miny


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--only", help="export just this cursor name (e.g. target)")
	parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
	parser.add_argument("--out", required=True, help="Godot project root")
	args = parser.parse_args()

	grf = GrfStack([str(Path(args.client) / "data.grf"),
					str(Path(args.client) / "event.grf")])
	act = actfmt.parse(grf.read("data\\sprite\\cursors.act"))
	spr = sprfmt.parse(grf.read("data\\sprite\\cursors.spr"))

	root = Path(args.out) / "cursor"
	root.mkdir(parents=True, exist_ok=True)

	# The cell bracket. Not an ACT action -- it is a loose texture -- but it is
	# the same thing to the player (where the pointer is), so it lives with the
	# cursors rather than in a folder of its own.
	if not args.only or args.only == "grid":
		_export_grid(grf, root)

	for ai, action in enumerate(act.actions):
		name = CURSOR_NAMES.get(ai, "action_%02d" % ai)
		if args.only and name != args.only:
			continue
		if not action.frames:
			continue
		folder = root / name
		folder.mkdir(exist_ok=True)
		files = []
		hotspot = [0, 0]
		for fi, frame in enumerate(action.frames):
			png, w, h, hx, hy = _composite(frame, spr)
			if not png:
				continue
			fname = "f%02d.png" % fi
			(folder / fname).write_bytes(png)
			files.append(fname)
			hotspot = [hx, hy]   # frames share a hotspot; last wins
		if files:
			meta = {"delay": round(action.delay * DELAY_UNIT_MS),
					"hotspot": hotspot, "frames": files}
			(folder / "cursor.json").write_text(
				json.dumps(meta, separators=(",", ":")), encoding="utf-8")
			print("cursor %-12s %d frame(s) -> %s" % (name, len(files), folder))


def _export_grid(grf: GrfStack, root: Path) -> None:
	"""`data\\texture\\grid.tga` -> `cursor/grid.png`, unchanged but for the
	container. Already RGBA with real alpha, so there is nothing to key."""
	from PIL import Image

	try:
		blob = grf.read(r"data\texture\grid.tga")
	except Exception:                                # noqa: BLE001
		print("cursor grid          MISSING (data/texture/grid.tga)")
		return
	image = Image.open(io.BytesIO(blob)).convert("RGBA")
	out = io.BytesIO()
	image.save(out, "PNG")
	(root / "grid.png").write_bytes(out.getvalue())
	print("cursor %-12s %dx%d -> %s" % ("grid", image.width, image.height, root / "grid.png"))

if __name__ == "__main__":
	main()
