"""Export palette data so the Godot runtime can recolour hair and clothes.

The sprite sheets exported by ragnadot.sprite_export bake the SPR's default
palette into RGBA, which loses the indices needed for recolouring. This
module adds, for the *indexed* player parts only (head = hair, body =
clothes):

  sprites/<rel>.idx.png   an R8 sheet of raw palette indices, packed into
                          the exact same rects as the colour sheet so the
                          existing JSON frame rects/UVs still apply.
  palettes/hair/머리<style>_<g>_<color>.png   256x1 RGBA palette LUTs
  palettes/body/<name>_<g>_<color>.png
  data/palettes.json      available {style/name -> max colour} per gender

The runtime (ActorSprite recolour path) samples the index sheet, looks up
the chosen palette LUT, and outputs palette[index] — a classic palette
swap. Index 0 stays transparent.

Usage:
    python -m ragnadot.palette_export --out C:/Users/pedro/Documents/ragnarok
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from ragx.formats import spr as spr_format
from ragx.grf import GrfStack

HEAD_DIRS = ("인간족\\머리통", "도람족\\머리통")
BODY_DIRS = ("인간족\\몸통", "도람족\\몸통")
SPRITE_PREFIX = "data\\sprite\\"
HAIR_PAL = "data\\palette\\머리\\"
BODY_PAL = "data\\palette\\몸\\"
_HAIR_RE = re.compile(r"머리(\d+)_([남여])_(\d+)\.pal$")


def _palette_png(data: bytes) -> np.ndarray:
    """A 1024-byte .pal -> (1, 256, 4) RGBA, index 0 transparent."""
    pal = np.frombuffer(data[:1024], dtype=np.uint8).reshape(256, 4).copy()
    pal[:, 3] = 255
    pal[0] = (0, 0, 0, 0)
    return pal.reshape(1, 256, 4)


def export_palettes(grf: GrfStack, out: Path) -> dict:
    from PIL import Image

    manifest: dict[str, dict] = {"hair": {}, "body": {}}
    names = grf.namelist()

    hair_dir = out / "palettes" / "hair"
    hair_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        if not n.startswith(HAIR_PAL) or not n.endswith(".pal"):
            continue
        base = n.split("\\")[-1]
        Image.fromarray(_palette_png(grf.read(n)), "RGBA").save(
            hair_dir / (base[:-4] + ".png"))
        m = _HAIR_RE.search(base)
        if m:
            key = "%s_%s" % (m.group(1), m.group(2))
            slot = manifest["hair"].setdefault(key, 0)
            manifest["hair"][key] = max(slot, int(m.group(3)))

    body_dir = out / "palettes" / "body"
    body_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        if not n.startswith(BODY_PAL) or not n.endswith(".pal"):
            continue
        base = n.split("\\")[-1]
        Image.fromarray(_palette_png(grf.read(n)), "RGBA").save(
            body_dir / (base[:-4] + ".png"))
    print(f"  palettes: {len(list(hair_dir.iterdir()))} hair, "
          f"{len(list(body_dir.iterdir()))} body")
    return manifest


def export_index_sheets(grf: GrfStack, out: Path) -> int:
    """Write an R8 index sheet for every head/body sprite that already has a
    colour-sheet JSON (so the rects line up)."""
    from PIL import Image

    names = grf.namelist()
    written = 0
    for n in names:
        if not n.endswith(".spr"):
            continue
        rel_dir = n[len(SPRITE_PREFIX):]
        if not any(rel_dir.startswith(d) for d in HEAD_DIRS + BODY_DIRS):
            continue
        rel = n[len(SPRITE_PREFIX):-4].replace("\\", "/")
        json_path = out / "sprites" / (rel + ".json")
        if not json_path.exists():
            continue
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        spr = spr_format.parse(grf.read(n))
        sheet = np.zeros((int(meta["h"]), int(meta["w"])), dtype=np.uint8)
        rects = meta["frames"]
        for i, frame in enumerate(spr.indexed_frames):
            if i >= len(rects):
                break
            x, y, w, h = rects[i]
            if w == 0 or h == 0:
                continue
            idx = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(h, w)
            sheet[y: y + h, x: x + w] = idx
        idx_path = out / "sprites" / (rel + ".idx.png")
        Image.fromarray(sheet, "L").save(idx_path)
        written += 1
        if written % 100 == 0:
            print(f"  index sheets: {written}", flush=True)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--out", required=True, help="Godot project root")
    args = parser.parse_args()

    grf = GrfStack([str(Path(args.client) / "data.grf"),
                    str(Path(args.client) / "event.grf")])
    out = Path(args.out)

    manifest = export_palettes(grf, out)
    count = export_index_sheets(grf, out)
    (out / "data" / "palettes.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"done: {count} index sheets, palettes.json written")


if __name__ == "__main__":
    main()
