"""Export the loose effect textures that STR files never reference.

``effect_export`` only packs the bitmaps a ``.str`` names. But a large,
important family of RO effects has **no .str and no model at all** — the
client builds them procedurally in C++ from a handful of textures:

  Cold Bolt   N axis-aligned billboards of ``icearrow.tga`` raining onto the
              target, then an expanding ``ring_blue.tga`` circle on impact
  Fire Bolt   the same, with the fire bitmaps
  Lightning   likewise
  Frost Diver, Soul Strike, Fireball, hit sparks, ...

(Confirmed against a real client implementation: see RagnarokRebuild's
``Effects/EffectHandlers/Skills/IceArrow.cs`` — it launches
``PrimitiveType.DirectionalBillboard`` primitives with an ``icearrow``
sprite and a ``PrimitiveType.Circle`` ring. There is no ``.rsm`` for any of
this; searching ``data\\model\\`` for an effect tree comes up empty.)

Those textures live loose under ``data\\texture\\effect\\`` and were simply
never exported, so a renderer has nothing to draw the bolts with. This
module writes them out:

  effects/tex/<rel path>.png     RGBA, magenta-keyed like the client

By default only the **loose** textures (directly under
``data\\texture\\effect\\``, no subfolder) are exported — that is where the
procedural effects' art lives, ~2k files. ``--all`` walks the whole tree.

Usage:
    python -m ragnadot.effect_texture_export --out C:/Users/pedro/Documents/ragnarok
    python -m ragnadot.effect_texture_export icearrow ring_blue --out ...
    python -m ragnadot.effect_texture_export --all --out ...
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from ragx.client import client_grf_paths
from ragx.grf import GrfStack

EFFECT_PREFIX = "data\\texture\\effect\\"
IMAGE_EXT = (".bmp", ".tga", ".jpg", ".jpeg", ".png")


def _decode(data: bytes) -> bytes | None:
    """Decode an effect bitmap to a PNG, applying RO's magenta colour key.

    Same rules as ``effect_export._decode_texture``: effect BMPs are 24-bit
    and rely on magic pink for transparency; TGAs carry their own alpha.
    The top nibble is quantised before testing so *near*-pink keys out too,
    exactly as the client does — otherwise bolts get pink fringes.
    """
    from PIL import Image
    import numpy as np

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # noqa: BLE001
        return None
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    rgba = np.array(image, dtype=np.uint8)
    keyed = ((rgba[:, :, 0] & 0xF0) == 0xF0) & (rgba[:, :, 1] < 0x10) \
        & ((rgba[:, :, 2] & 0xF0) == 0xF0)
    rgba[keyed] = 0

    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, format="PNG", optimize=False)
    return out.getvalue()


def _targets(names: list[str], *, all_: bool, wanted: set[str]) -> list[str]:
    pool = [n for n in names
            if n.startswith(EFFECT_PREFIX) and n.lower().endswith(IMAGE_EXT)]
    if wanted:
        picked = []
        for n in pool:
            rel = n[len(EFFECT_PREFIX):].lower()
            stem = rel.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            if rel in wanted or stem in wanted:
                picked.append(n)
        return sorted(picked)
    if all_:
        return sorted(pool)
    # Loose = directly under the effect dir; that's where the procedural
    # effects' art sits (icearrow.tga, ring_blue.tga, firehit*.bmp, ...).
    return sorted(n for n in pool if n.count("\\") == 3)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("textures", nargs="*",
                        help="texture names relative to texture/effect/ (ext optional)")
    parser.add_argument("--all", action="store_true",
                        help="export the whole effect texture tree, not just loose ones")
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--out", required=True, help="Godot project root")
    args = parser.parse_args()

    grf = GrfStack(client_grf_paths(args.client))
    wanted = {t.replace("/", "\\").lower() for t in args.textures}
    targets = _targets(grf.namelist(), all_=args.all, wanted=wanted)
    if not targets:
        parser.error("no matching effect textures")

    out_root = Path(args.out) / "effects" / "tex"
    out_root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    written = skipped = 0
    for name in targets:
        rel = name[len(EFFECT_PREFIX):].lower()
        png = _decode(grf.read(name))
        if png is None:
            skipped += 1
            continue
        dest = out_root / (rel.replace("\\", "/").rsplit(".", 1)[0] + ".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(png)
        written += 1

    print(f"effect textures -> {out_root}")
    print(f"  wrote {written}, skipped {skipped} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
