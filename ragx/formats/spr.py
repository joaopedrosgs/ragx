"""SPR (sprite atlas) parser.

A SPR file is a list of images ("frames") in two pools that ACT layers
reference by (sprite_type, index): pool 0 holds 8-bit palette-indexed
frames, pool 1 holds 32-bit RGBA frames.

Version differences (header version is stored minor-byte-first):
  1.0  indexed frames only, no embedded palette
  1.1  256-color RGBA palette appended at EOF
  2.0  adds the RGBA frame pool
  2.1  indexed frame pixels are RLE-compressed (zero runs only)

Layout knowledge from GRF Editor's SprLoader.cs and korangar sprite.rs:
RGBA frame pixels are stored bottom-up with byte order A,B,G,R; palette
index 0 is the transparency key regardless of its stored alpha.
"""

from __future__ import annotations

from dataclasses import dataclass

from .binio import Reader

SIGNATURE = b"SP"


@dataclass(slots=True)
class IndexedFrame:
    width: int
    height: int
    pixels: bytes  # width*height palette indices (RLE already decoded)


@dataclass(slots=True)
class RgbaFrame:
    width: int
    height: int
    pixels: bytes  # width*height*4 raw file bytes (A,B,G,R / bottom-up)


@dataclass(slots=True)
class Spr:
    version: tuple[int, int]
    indexed_frames: list[IndexedFrame]
    rgba_frames: list[RgbaFrame]
    palette: bytes | None  # 1024 bytes RGBX, or None for v1.0
    leftover: int = 0  # junk bytes between frames and palette (a few official files)

    def frame_rgba(self, sprite_type: int, index: int) -> tuple[int, int, bytes]:
        """Decode one frame to (width, height, top-down RGBA8 bytes).

        sprite_type is the ACT layer convention: 0 = indexed, 1 = RGBA.
        """
        if sprite_type == 0:
            frame = self.indexed_frames[index]
            if self.palette is None:
                raise ValueError("v1.0 SPR uses the system palette, which is not supported")
            out = bytearray(frame.width * frame.height * 4)
            palette = self.palette
            for i, pix in enumerate(frame.pixels):
                if pix == 0:  # index 0 is the transparency key
                    continue
                out[4 * i] = palette[4 * pix]
                out[4 * i + 1] = palette[4 * pix + 1]
                out[4 * i + 2] = palette[4 * pix + 2]
                out[4 * i + 3] = 255
            return frame.width, frame.height, bytes(out)
        frame = self.rgba_frames[index]
        width, height = frame.width, frame.height
        src = frame.pixels
        out = bytearray(len(src))
        row_bytes = width * 4
        for y in range(height):
            src_off = (height - 1 - y) * row_bytes
            dst_off = y * row_bytes
            for x in range(width):
                s = src_off + 4 * x
                d = dst_off + 4 * x
                out[d] = src[s + 3]      # R
                out[d + 1] = src[s + 2]  # G
                out[d + 2] = src[s + 1]  # B
                out[d + 3] = src[s]      # A
        return width, height, bytes(out)


def _decode_rle(reader: Reader, pixel_count: int) -> bytes:
    """Zero-run RLE used by v2.1+: 0x00 is followed by the run length."""
    encoded = reader.u16()
    data = reader.bytes(encoded)
    out = bytearray(pixel_count)
    next_pixel = 0
    pos = 0
    while pos < encoded:
        byte = data[pos]
        pos += 1
        if byte == 0:
            length = max(data[pos], 1)
            pos += 1
            next_pixel += length  # already zero-initialized
        else:
            out[next_pixel] = byte
            next_pixel += 1
        if next_pixel > pixel_count:
            raise ValueError("RLE overruns the frame")
    if next_pixel != pixel_count:
        raise ValueError(f"RLE produced {next_pixel} of {pixel_count} pixels")
    return bytes(out)


def parse(data: bytes) -> Spr:
    reader = Reader(data)
    if reader.bytes(2) != SIGNATURE:
        raise ValueError("not a SPR file")
    minor = reader.u8()
    major = reader.u8()
    version = (major, minor)
    if not ((1, 0) <= version <= (2, 1)):
        raise ValueError(f"unsupported SPR version {major}.{minor}")

    indexed_count = reader.u16()
    rgba_count = reader.u16() if version >= (2, 0) else 0

    has_palette = version >= (1, 1)

    indexed_frames = []
    for _ in range(indexed_count):
        width = reader.u16()
        height = reader.u16()
        if version >= (2, 1):
            pixels = _decode_rle(reader, width * height)
        else:
            pixels = reader.bytes(width * height)
        indexed_frames.append(IndexedFrame(width, height, pixels))

    rgba_frames = []
    for _ in range(rgba_count):
        width = reader.u16()
        height = reader.u16()
        rgba_frames.append(RgbaFrame(width, height, reader.bytes(width * height * 4)))

    # The palette is the last 1024 bytes of the file (GRF Editor reads it
    # the same way); a handful of official files pad junk before it, and
    # two ship a truncated palette that no indexed frame references.
    palette = None
    leftover = len(data) - reader.pos
    if has_palette:
        if leftover >= 1024:
            palette = data[-1024:]
            leftover -= 1024
        elif indexed_frames:
            raise ValueError("indexed frames but no complete palette")
    if leftover < 0:
        raise ValueError("frames overrun the palette")
    return Spr(version, indexed_frames, rgba_frames, palette, leftover)
