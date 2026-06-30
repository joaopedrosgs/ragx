"""GAT (altitude/terrain-type) parser.

Not strictly required to render a map, but cheap to parse and useful for
sanity checks (the GAT grid is exactly twice the GND cube grid).
"""

from __future__ import annotations

from dataclasses import dataclass

from .binio import Reader

SIGNATURE = b"GRAT"


@dataclass(slots=True)
class Gat:
    version: tuple[int, int]
    width: int
    height: int
    # Per tile: (h_sw, h_se, h_nw, h_ne, type)
    tiles: list[tuple[float, float, float, float, int]]


def parse(data: bytes) -> Gat:
    reader = Reader(data)
    if reader.bytes(4) != SIGNATURE:
        raise ValueError("not a GAT file")
    version = (reader.u8(), reader.u8())
    width = reader.i32()
    height = reader.i32()
    tiles = []
    for _ in range(width * height):
        h_sw = reader.f32()
        h_se = reader.f32()
        h_nw = reader.f32()
        h_ne = reader.f32()
        tile_type = reader.u32()
        tiles.append((h_sw, h_se, h_nw, h_ne, tile_type))
    return Gat(version, width, height, tiles)
