"""``ragx cursors`` — export the client mouse cursors.

RO keeps every mouse-cursor state in one SPR/ACT pair (``data\\sprite\\
cursors.spr`` + ``.act``): one *action* per state (normal arrow, talk hand, the
animated skill-target ring, forbidden, …), each a short frame animation the
client cycles.

Each action is written as individual frame PNGs plus a small JSON so a renderer
can set a custom mouse cursor and animate it:

  <out>/cursor/<name>/f00.png, f01.png, ...   frames, RGBA, tightly composited
  <out>/cursor/<name>/cursor.json             { "delay": ms, "hotspot": [x, y],
                                                "frames": [...] }

``hotspot`` is where the click actually lands (the ACT origin) in the PNG's
pixel space. Action names follow RO's conventional cursor order; unknown
indices fall back to ``action_NN``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from .. import client as client_mod
from ..formats import act as actfmt, spr as sprfmt

CURSOR_NAMES = {
	0: "normal", 1: "wait", 2: "click", 3: "target_lock", 4: "select",
	5: "sign", 6: "no", 7: "warp", 8: "forbidden", 9: "grab",
	10: "target", 11: "target2", 12: "sign2", 13: "arrow",
}
DELAY_UNIT_MS = 25


def _composite(frame, spr) -> tuple[bytes, int, int]:
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
		lx, ly = layer.x - im.width // 2, layer.y - im.height // 2
		placed.append((lx, ly, im))
		minx, miny = min(minx, lx), min(miny, ly)
		maxx, maxy = max(maxx, lx + im.width), max(maxy, ly + im.height)
	if not placed:
		return b"", 0, 0
	w, h = maxx - minx, maxy - miny
	canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
	for lx, ly, im in placed:
		canvas.alpha_composite(im, (lx - minx, ly - miny))
	out = io.BytesIO()
	canvas.save(out, "PNG")
	return out.getvalue(), -minx, -miny   # + hotspot (ACT origin in the PNG)


def run(args) -> int:
	sys.stdout.reconfigure(encoding="utf-8", errors="replace")
	grf = client_mod.open_stack(args.client)
	act = actfmt.parse(grf.read("data\\sprite\\cursors.act"))
	spr = sprfmt.parse(grf.read("data\\sprite\\cursors.spr"))
	grf.close()

	root = Path(args.out) / "cursor"
	root.mkdir(parents=True, exist_ok=True)

	for ai, action in enumerate(act.actions):
		name = CURSOR_NAMES.get(ai, "action_%02d" % ai)
		if args.only and name != args.only:
			continue
		if not action.frames:
			continue
		folder = root / name
		folder.mkdir(exist_ok=True)
		files, hotspot = [], [0, 0]
		for fi, frame in enumerate(action.frames):
			png, hx, hy = _composite(frame, spr)
			if not png:
				continue
			(folder / ("f%02d.png" % fi)).write_bytes(png)
			files.append("f%02d.png" % fi)
			hotspot = [hx, hy]
		if files:
			(folder / "cursor.json").write_text(json.dumps(
				{"delay": round(action.delay * DELAY_UNIT_MS),
				 "hotspot": hotspot, "frames": files},
				separators=(",", ":")), encoding="utf-8")
			print("cursor %-12s %d frame(s)" % (name, len(files)))
	return 0
