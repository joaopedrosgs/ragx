"""IMF (interface motion file) parser.

Per player job+gender the client ships data/imf/<name>.imf with one entry
per (layer, action, frame). Layer 0 = body, layer 1 = head. The only field
clients use is `priority`: when the head layer's priority is 1 for a frame,
the head is drawn *before* the body (e.g. bowing). The cx/cy fields are
character-select-screen offsets and are ignored (zrenderer does the same).
"""

from __future__ import annotations

from dataclasses import dataclass

from .binio import Reader


@dataclass(slots=True)
class Imf:
    version: float
    # priorities[layer][action][frame]
    priorities: list[list[list[int]]]


def parse(data: bytes) -> Imf:
    reader = Reader(data)
    version = reader.f32()
    reader.skip(4)  # checksum, unused
    max_layer = reader.i32()
    layers = []
    for _ in range(max_layer + 1):
        actions = []
        for _ in range(reader.i32()):
            frames = []
            for _ in range(reader.i32()):
                frames.append(reader.i32())
                reader.skip(8)  # cx, cy
            actions.append(frames)
        layers.append(actions)
    return Imf(version, layers)
