"""``ragx effects`` — export STR skill/visual effects to atlas PNG + JSON.

For each ``data\\texture\\effect\\**\\<name>.str`` in the client:

  <out>/effects/<rel path>.png    every bitmap the effect references, packed
  <out>/effects/<rel path>.json   fps + per-layer keyframe tracks + atlas rects

We export the *keyframes*, not baked per-frame data: the client's morph model (a
type-0 "basis" keyframe plus type-1 keyframes holding per-frame increments) is a
cheap linear interpolation the renderer redoes at playback time. That keeps the
JSON tiny (~130 keyframes/effect) and the animation frame-rate independent.

JSON layout:

  {
    "fps":      frames per second,
    "max_key":  total frames in the animation,
    "w", "h":   atlas size in pixels,
    "textures": [[x, y, w, h], ...]  atlas rect per global texture,
    "layers": [
      { "tex":  [global_index, ...]  this layer's texture pool,
        "keys": [[frame, type, px, py, x0,x1,x2,x3, y0,y1,y2,y3,
                  aniframe, anitype, delay, angle, r, g, b, a, blend], ...] }
    ]
  }

  blend is a resolved family (0 mix, 1 add, 2 premul); inherited (0,0) factors
  are already carried forward here so the renderer needs no blend history.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import numpy as np

from .. import client as client_mod
from ..formats import str as strfmt

EFFECT_PREFIX = "data\\texture\\effect\\"
MAX_SHEET_WIDTH = 2048
PADDING = 1


def _decode_texture(data: bytes) -> np.ndarray | None:
    """Decode a BMP/TGA effect bitmap to a top-down RGBA array.

    Effect BMPs are 24-bit (no alpha): under the additive/premultiplied blend
    modes that dominate effects, black contributes nothing, so a solid 255 alpha
    is correct. TGAs keep their own alpha channel."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # noqa: BLE001
        return None
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    rgba = np.array(image, dtype=np.uint8)  # writable copy
    # Magic pink (255, 0, 255) is RO's transparency key; quantise the top nibble
    # like the client before testing so near-pink also keys out.
    keyed = ((rgba[:, :, 0] & 0xF0) == 0xF0) & (rgba[:, :, 1] < 0x10) \
        & ((rgba[:, :, 2] & 0xF0) == 0xF0)
    rgba[keyed] = 0
    return rgba


def _pack(images: list[np.ndarray]) -> tuple[np.ndarray, list[list[int]]]:
    """Shelf-pack RGBA images into one atlas; returns (atlas, rects)."""
    order = sorted(range(len(images)), key=lambda i: -images[i].shape[0])
    max_w = max((im.shape[1] for im in images), default=1)
    area = sum(im.shape[0] * im.shape[1] for im in images)
    width = min(MAX_SHEET_WIDTH,
                max(max_w + 2 * PADDING, int(np.sqrt(area) * 1.3) + 2 * PADDING))

    rects: list[list[int]] = [[0, 0, 0, 0] for _ in images]
    x = y = shelf_h = 0
    for index in order:
        h, w = images[index].shape[0], images[index].shape[1]
        if w == 0 or h == 0:
            continue
        if x + w + 2 * PADDING > width:
            x = 0
            y += shelf_h
            shelf_h = 0
        rects[index] = [x + PADDING, y + PADDING, w, h]
        x += w + 2 * PADDING
        shelf_h = max(shelf_h, h + 2 * PADDING)
    height = max(y + shelf_h, 1)

    atlas = np.zeros((height, width, 4), dtype=np.uint8)
    for index, im in enumerate(images):
        rx, ry, w, h = rects[index]
        if w and h:
            atlas[ry: ry + h, rx: rx + w] = im
    return atlas, rects


def _round(value: float, ndigits: int = 3) -> float:
    """JSON-friendly round that collapses -0.0 and integers."""
    r = round(float(value), ndigits)
    return 0.0 if r == 0 else r


def export_one(effect, folder, grf, out_root: Path) -> str:
    from PIL import Image

    rel = effect[len(EFFECT_PREFIX):-4].replace("\\", "/")

    # Gather the unique textures the whole effect references, decode + pack.
    global_names: list[str] = []
    name_to_global: dict[str, int] = {}
    for layer in folder.layers:
        for name in layer.texture_names:
            if name not in name_to_global:
                name_to_global[name] = len(global_names)
                global_names.append(name)

    images: list[np.ndarray] = []
    for name in global_names:
        tex_path = effect.rsplit("\\", 1)[0] + "\\" + name
        image = None
        if tex_path in grf:
            image = _decode_texture(grf.read(tex_path))
        images.append(image if image is not None
                      else np.zeros((1, 1, 4), dtype=np.uint8))

    atlas, rects = _pack(images)

    # A texture "has alpha" if it carries real transparency (TGA channel or a
    # magic-pink key); opaque BMPs do not. Effect BMPs are meant to blend
    # additively (black adds nothing), so a layer whose textures are all opaque
    # must never use alpha compositing or it shows as a solid block.
    has_alpha = [bool(im.shape[2] == 4 and int(im[:, :, 3].min()) < 255)
                 for im in images]

    # Resolve inherited blend factors once, classify per keyframe.
    layers_json = []
    for layer in folder.layers:
        layer_has_alpha = any(
            has_alpha[name_to_global[n]] for n in layer.texture_names)
        last_src, last_dst = 5, 6
        resolved: list[int] = []
        for kf in layer.keyframes:
            src = kf.src_blend if kf.src_blend != 0 else last_src
            dst = kf.dst_blend if kf.dst_blend != 0 else last_dst
            last_src, last_dst = src, dst
            blend = strfmt.classify_blend(src, dst)
            if blend == strfmt.BLEND_MIX and not layer_has_alpha:
                blend = strfmt.BLEND_ADD
            resolved.append(blend)

        keys = []
        for i, kf in enumerate(layer.keyframes):
            keys.append([
                kf.frame, kf.type,
                _round(kf.position[0]), _round(kf.position[1]),
                _round(kf.xy[0]), _round(kf.xy[1]), _round(kf.xy[2]), _round(kf.xy[3]),
                _round(kf.xy[4]), _round(kf.xy[5]), _round(kf.xy[6]), _round(kf.xy[7]),
                _round(kf.texture_index, 4), kf.animation_type, _round(kf.delay, 4),
                _round(kf.angle, 3),
                _round(kf.color[0], 1), _round(kf.color[1], 1),
                _round(kf.color[2], 1), _round(kf.color[3], 1),
                resolved[i],
            ])
        layers_json.append({
            "tex": [name_to_global[n] for n in layer.texture_names],
            "keys": keys,
        })

    meta = {
        "fps": folder.fps,
        "max_key": strfmt.frame_count(folder),
        "w": int(atlas.shape[1]),
        "h": int(atlas.shape[0]),
        "textures": rects,
        "layers": layers_json,
    }

    png_path = out_root / (rel + ".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(atlas, "RGBA").save(png_path, format="PNG")
    json_path = out_root / (rel + ".json")
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return "ok"


def run(args) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    grf = client_mod.open_stack(args.client)
    names = grf.namelist()
    if args.all:
        targets = sorted(n for n in names
                         if n.startswith(EFFECT_PREFIX) and n.endswith(".str"))
    elif args.effects:
        targets = []
        wanted = {s.replace("/", "\\").lower() for s in args.effects}
        for n in names:
            if not (n.startswith(EFFECT_PREFIX) and n.endswith(".str")):
                continue
            rel = n[len(EFFECT_PREFIX):-4].lower()
            if rel in wanted or rel.rsplit("\\", 1)[-1] in wanted:
                targets.append(n)
        if not targets:
            grf.close()
            sys.exit("no matching effects (try --all or check the name)")
    else:
        grf.close()
        sys.exit("no effects given (pass effect names or --all)")

    out_root = Path(args.out) / "effects"
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"exporting {len(targets)} effect(s) -> {out_root}", flush=True)

    t0 = time.time()
    counts: dict[str, int] = {}
    for done, name in enumerate(targets, 1):
        try:
            parsed = strfmt.parse(grf.read(name))
            status = export_one(name, parsed, grf, out_root)
        except Exception as exc:  # noqa: BLE001
            status = "FAILED"
            print(f"FAIL {name}: {exc}")
        counts[status] = counts.get(status, 0) + 1
        if done % 100 == 0 or done == len(targets):
            print(f"[{done}/{len(targets)}] {counts} {time.time()-t0:.0f}s", flush=True)

    grf.close()
    print(f"done in {time.time()-t0:.0f}s: {counts}")
    return 1 if counts.get("FAILED") else 0
