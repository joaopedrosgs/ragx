"""``ragx effect-textures`` — export the loose effect textures.

``ragx effects`` packs the bitmaps each ``.str`` file references. But a whole
family of RO effects has **no .str and no model**: the client builds them
procedurally in C++ from single textures.

  Cold Bolt   axis-aligned billboards of ``icearrow.tga`` raining onto the
              target, then an expanding ``ring_blue.tga`` circle on impact
  Fire Bolt / Lightning Bolt / Frost Diver / Soul Strike / hit sparks / ...

There is no ``.rsm`` for any of it — ``data\\model\\`` has no effect tree at
all. The art lives loose under ``data\\texture\\effect\\`` and no exporter
ever emitted it, so a renderer has nothing to draw the bolts with.

Output:

  <out>/effects/tex/<rel path>.png     RGBA, magenta-keyed like the client

By default only the **loose** textures (directly under the effect dir, no
subfolder) are exported — that is where the procedural art lives, ~2k files.
``--all`` walks the whole tree.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np

from .. import client as client_mod

EFFECT_PREFIX = "data\\texture\\effect\\"
IMAGE_EXT = (".bmp", ".tga", ".jpg", ".jpeg", ".png")


def _decode(data: bytes) -> bytes | None:
    """Decode an effect bitmap to PNG, applying RO's magenta colour key.

    Effect BMPs are 24-bit and rely on magic pink for transparency; TGAs
    carry real alpha. The top nibble is quantised before testing so *near*-
    pink keys out too, exactly as the client does — otherwise the bolts get
    pink fringes where the art was antialiased against the key colour.
    """
    from PIL import Image

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
    return sorted(n for n in pool if n.count("\\") == 3)


def run(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    grf = client_mod.open_stack(args.client)
    wanted = {t.replace("/", "\\").lower() for t in (args.textures or [])}
    targets = _targets(grf.namelist(), all_=args.all, wanted=wanted)
    if not targets:
        grf.close()
        sys.exit("no matching effect textures (try --all or check the name)")

    out_root = Path(args.out) / "effects" / "tex"
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"exporting {len(targets)} effect texture(s) -> {out_root}", flush=True)

    t0 = time.time()
    written = skipped = 0
    for name in targets:
        png = _decode(grf.read(name))
        if png is None:
            skipped += 1
            continue
        rel = name[len(EFFECT_PREFIX):].lower()
        dest = out_root / (rel.replace("\\", "/").rsplit(".", 1)[0] + ".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(png)
        written += 1

    grf.close()
    print(f"wrote {written}, skipped {skipped} in {time.time() - t0:.1f}s")
    return 0
