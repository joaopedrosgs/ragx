"""ACT (sprite animation) parser.

An ACT file holds the animation metadata for a SPR atlas: a list of
*actions* (idle, walk, attack, ... usually x8 directions), each a list
of *frames* (animation steps), each a stack of *layers* placing one SPR
image with offset/mirror/tint/scale/rotation.

Version gates follow korangar's action.rs (validated against every ACT
in the LATAM client):
  2.0  layer color/zoom/angle/sprite_type, per-frame event_id
  2.1  named event list
  2.2  per-action frame delay table (units of 25 ms)
  2.3  per-frame attach points
  2.4  split x/y zoom
  2.5  layer width/height fields
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .binio import Reader

SIGNATURE = b"AC"
# The client advances animations in 25 ms ticks; a frame delay of 4 = 100 ms.
DELAY_UNIT_MS = 25.0
DEFAULT_DELAY = 4.0


@dataclass(slots=True)
class Layer:
    x: int
    y: int
    sprite_index: int  # index into the SPR pool; -1 = unused layer
    mirror: bool
    color: tuple[int, int, int, int] = (255, 255, 255, 255)  # RGBA tint
    scale_x: float = 1.0
    scale_y: float = 1.0
    angle: float = 0.0  # degrees, clockwise
    sprite_type: int = 0  # SPR pool: 0 = indexed, 1 = RGBA
    width: int = 0  # v2.5+; usually the SPR frame size, informational
    height: int = 0


@dataclass(slots=True)
class AttachPoint:
    x: int
    y: int
    attr: int


@dataclass(slots=True)
class Frame:
    layers: list[Layer]
    event_id: int = -1
    attach_points: list[AttachPoint] = field(default_factory=list)


@dataclass(slots=True)
class Action:
    frames: list[Frame]
    delay: float = DEFAULT_DELAY  # per-frame display time in 25 ms units


@dataclass(slots=True)
class Act:
    version: tuple[int, int]
    actions: list[Action]
    events: list[str]  # sound file names / "atk" markers, see Frame.event_id
    leftover: int = 0  # trailing junk bytes (a few official files have some)


def _parse_layer(reader: Reader, version: tuple[int, int]) -> Layer:
    x = reader.i32()
    y = reader.i32()
    sprite_index = reader.i32()
    mirror = reader.u32() != 0
    layer = Layer(x, y, sprite_index, mirror)
    if version >= (2, 0):
        color = reader.bytes(4)
        layer.color = (color[0], color[1], color[2], color[3])
        if version >= (2, 4):
            layer.scale_x = reader.f32()
            layer.scale_y = reader.f32()
        else:
            layer.scale_x = layer.scale_y = reader.f32()
        layer.angle = float(reader.i32())
        layer.sprite_type = reader.i32()
        if version >= (2, 5):
            layer.width = reader.i32()
            layer.height = reader.i32()
    return layer


def _parse_frame(reader: Reader, version: tuple[int, int]) -> Frame:
    reader.skip(32)  # range1/range2 bounding boxes, unused by clients
    layers = [_parse_layer(reader, version) for _ in range(reader.u32())]
    frame = Frame(layers)
    if version >= (2, 0):
        frame.event_id = reader.i32()
    if version >= (2, 3):
        for _ in range(reader.u32()):
            reader.skip(4)  # unknown, always seems to be 0
            x = reader.i32()
            y = reader.i32()
            frame.attach_points.append(AttachPoint(x, y, reader.i32()))
    return frame


def parse(data: bytes) -> Act:
    reader = Reader(data)
    if reader.bytes(2) != SIGNATURE:
        raise ValueError("not an ACT file")
    minor = reader.u8()
    major = reader.u8()
    version = (major, minor)
    if not ((1, 0) <= version <= (2, 5)):
        raise ValueError(f"unsupported ACT version {major}.{minor}")

    action_count = reader.u16()
    reader.skip(10)  # reserved
    actions = [
        Action([_parse_frame(reader, version) for _ in range(reader.u32())])
        for _ in range(action_count)
    ]

    events = []
    if version >= (2, 1):
        events = [reader.fixed_string(40) for _ in range(reader.u32())]
    if version >= (2, 2):
        for action in actions:
            action.delay = reader.f32()

    return Act(version, actions, events, leftover=reader.remaining())
