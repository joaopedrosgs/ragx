"""Texture loading and conversion.

RO textures live under ``data\\texture\\`` and are mostly BMP (with
magenta #FF00FF as the transparency key), plus some TGA (real alpha)
and JPG. Everything is converted to PNG for embedding into the GLB.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

TEXTURE_PREFIX = "data\\texture\\"


@dataclass(slots=True)
class LoadedTexture:
    png: bytes
    width: int
    height: int
    has_alpha: bool


def convert_texture(raw: bytes, name: str) -> LoadedTexture:
    image = Image.open(io.BytesIO(raw))
    image.load()

    lower = name.lower()
    image = image.convert("RGBA")
    has_alpha = False
    # The client keys magenta on every model/map texture regardless of
    # container format (TGAs included — some carry opaque magenta).
    # JPGs are exempt: they have no key color and some water/lava art is
    # legitimately purple.
    if not lower.endswith((".jpg", ".jpeg")):
        has_alpha = _apply_magenta_key(image)
    has_alpha = has_alpha or _image_has_alpha(image)

    if not has_alpha:
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return LoadedTexture(buffer.getvalue(), image.width, image.height, has_alpha)


def _apply_magenta_key(image: Image.Image) -> bool:
    """Replace (near-)magenta pixels with true transparency. Returns True
    if any pixel was keyed.

    Two extra steps beyond a plain color test keep the cutout edges clean:
    - the tolerance is wide enough to catch antialiased / off-by-a-few
      magenta (e.g. 248,8,248) that would otherwise leave pink fringes;
    - the RGB of keyed pixels is in-painted with the average color of
      their nearest opaque neighbors, because texture filtering samples
      the RGB of transparent texels at the cutout border (leaving the
      original magenta there causes pink halos; plain black causes dark
      ones)."""
    pixels = np.asarray(image).copy()
    red = pixels[:, :, 0].astype(np.int16)
    green = pixels[:, :, 1].astype(np.int16)
    blue = pixels[:, :, 2].astype(np.int16)

    mask = (red > 0xE0) & (green < 0x30) & (blue > 0xE0) & \
           ((red - green) > 0xB0) & ((blue - green) > 0xB0)
    if not mask.any():
        return False

    pixels[:, :, 3][mask] = 0

    # In-paint keyed RGB from opaque neighbors (a few dilation passes).
    rgb = pixels[:, :, :3].astype(np.float32)
    known = ~mask
    for _ in range(4):
        unknown = ~known
        if not unknown.any():
            break
        neighbor_sum = np.zeros_like(rgb)
        neighbor_count = np.zeros(rgb.shape[:2], dtype=np.float32)
        for shift_y, shift_x in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted_known = np.roll(known, (shift_y, shift_x), axis=(0, 1))
            shifted_rgb = np.roll(rgb, (shift_y, shift_x), axis=(0, 1))
            take = unknown & shifted_known
            neighbor_sum[take] += shifted_rgb[take]
            neighbor_count[take] += 1.0
        filled = unknown & (neighbor_count > 0)
        if not filled.any():
            break
        rgb[filled] = neighbor_sum[filled] / neighbor_count[filled, None]
        known |= filled

    pixels[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    # Anything still unknown (fully magenta regions) becomes neutral gray.
    remaining = ~known
    if remaining.any():
        pixels[:, :, :3][remaining] = 128

    image.frombytes(pixels.tobytes())
    return True


def _image_has_alpha(image: Image.Image) -> bool:
    extrema = image.getextrema()
    if len(extrema) >= 4:
        return extrema[3][0] < 255
    return False
