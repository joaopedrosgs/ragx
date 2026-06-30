"""RSW (world/scene) parser.

Versions seen in the LATAM client: 1.9, 2.1, 2.2, 2.4, 2.5, 2.6, 2.7.

Layout reconstructed from korangar's `ragnarok-formats` crate and
RagLite's `RagnarokRSW.lua`, then validated by parsing every RSW in the
client and requiring the quadtree to consume the file exactly.

Version differences that matter:
- >= 2.2 and < 2.5: one extra u8 (build number, low precision)
- >= 2.5: u32 build number plus one unknown u8 ("render flag")
- < 2.6: water settings live here (later they move into the GND)
- >= 2.6 with build > 161: props gain one unknown byte after block type
- >= 2.7 (build 248): an i32 array (`[count][count x i32]`, purpose
  unknown) appears after the map bounds, and props gain one more
  unknown i32 (usually -1) between the unknown byte and the model name.
  Layout verified empirically: all 18 v2.7 maps in the LATAM client
  parse to exactly zero leftover bytes.
- >= 2.1: file ends with a serialized quadtree (1365 nodes x 48 bytes)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .binio import Reader

SIGNATURE = b"GRSW"

QUAD_TREE_NODES = sum(4**depth for depth in range(6))  # depth 5 => 1365


@dataclass(slots=True)
class WaterSettings:
    level: float = 0.0
    water_type: int = 0
    wave_height: float = 0.0
    wave_speed: float = 0.0
    wave_pitch: float = 0.0
    texture_cycling_interval: int = 3


@dataclass(slots=True)
class LightSettings:
    longitude: int = 45
    latitude: int = 45
    diffuse: tuple[float, float, float] = (1.0, 1.0, 1.0)
    ambient: tuple[float, float, float] = (0.3, 0.3, 0.3)
    shadow_map_alpha: float = 1.0


@dataclass(slots=True)
class ModelInstance:
    name: str
    animation_type: int
    animation_speed: float
    block_type: int
    model_name: str  # path under data\model\, CP949-decoded
    node_name: str
    position: tuple[float, float, float]  # raw file values (Y down)
    rotation: tuple[float, float, float]  # degrees
    scale: tuple[float, float, float]


@dataclass(slots=True)
class LightSource:
    name: str
    position: tuple[float, float, float]
    color: tuple[float, float, float]
    range: float


@dataclass(slots=True)
class SoundSource:
    name: str
    sound_file: str
    position: tuple[float, float, float]
    volume: float
    width: int
    height: int
    range: float
    cycle: float


@dataclass(slots=True)
class EffectSource:
    name: str
    position: tuple[float, float, float]
    effect_type: int
    emit_speed: float
    params: tuple[float, float, float, float]


@dataclass(slots=True)
class Rsw:
    version: tuple[int, int]
    build_number: int
    ini_file: str
    gnd_file: str
    gat_file: str
    source_file: str
    water: WaterSettings | None
    light: LightSettings
    bounds: tuple[int, int, int, int]  # top, bottom, left, right
    models: list[ModelInstance] = field(default_factory=list)
    lights: list[LightSource] = field(default_factory=list)
    sounds: list[SoundSource] = field(default_factory=list)
    effects: list[EffectSource] = field(default_factory=list)


def parse(data: bytes) -> Rsw:
    reader = Reader(data)
    if reader.bytes(4) != SIGNATURE:
        raise ValueError("not an RSW file")
    version = (reader.u8(), reader.u8())

    build_number = 0
    if (2, 2) <= version < (2, 5):
        build_number = reader.u8()
    elif version >= (2, 5):
        build_number = reader.u32()
        reader.u8()  # unknown render flag

    ini_file = reader.fixed_string(40)
    gnd_file = reader.fixed_string(40)
    gat_file = reader.fixed_string(40)
    source_file = reader.fixed_string(40) if version >= (1, 4) else ""

    water: WaterSettings | None = None
    if version < (2, 6):
        water = WaterSettings()
        if version >= (1, 3):
            water.level = reader.f32()
        if version >= (1, 8):
            water.water_type = reader.i32()
            water.wave_height = reader.f32()
            water.wave_speed = reader.f32()
            water.wave_pitch = reader.f32()
        if version >= (1, 9):
            water.texture_cycling_interval = reader.i32()

    light = LightSettings()
    if version >= (1, 5):
        light.longitude = reader.i32()
        light.latitude = reader.i32()
        light.diffuse = reader.vec3()
        light.ambient = reader.vec3()
    if version >= (1, 7):
        light.shadow_map_alpha = reader.f32()

    bounds = (0, 0, 0, 0)
    if version >= (1, 6):
        bounds = (reader.i32(), reader.i32(), reader.i32(), reader.i32())

    if version >= (2, 7):
        unknown_count = reader.i32()
        reader.skip(4 * unknown_count)

    rsw = Rsw(version, build_number, ini_file, gnd_file, gat_file, source_file,
              water, light, bounds)

    prop_mystery_byte = version >= (2, 6) and build_number > 161
    prop_mystery_int = version >= (2, 7)

    object_count = reader.i32()
    for _ in range(object_count):
        object_type = reader.i32()
        if object_type == 1:
            rsw.models.append(_read_model(reader, version, prop_mystery_byte, prop_mystery_int))
        elif object_type == 2:
            rsw.lights.append(LightSource(
                name=reader.fixed_string(80),
                position=reader.vec3(),
                color=reader.vec3(),
                range=reader.f32(),
            ))
        elif object_type == 3:
            rsw.sounds.append(SoundSource(
                name=reader.fixed_string(80),
                sound_file=reader.fixed_string(80),
                position=reader.vec3(),
                volume=reader.f32(),
                width=reader.i32(),
                height=reader.i32(),
                range=reader.f32(),
                cycle=reader.f32() if version >= (2, 0) else 4.0,
            ))
        elif object_type == 4:
            rsw.effects.append(EffectSource(
                name=reader.fixed_string(80),
                position=reader.vec3(),
                effect_type=reader.i32(),
                emit_speed=reader.f32(),
                params=reader.vec4(),
            ))
        else:
            raise ValueError(f"unknown scene object type {object_type} at offset {reader.pos}")

    if version >= (2, 1):
        reader.skip(QUAD_TREE_NODES * 48)

    if reader.remaining() != 0:
        raise ValueError(f"RSW v{version[0]}.{version[1]} build {build_number}: "
                         f"{reader.remaining()} bytes left unparsed")

    return rsw


def _read_model(reader: Reader, version: tuple[int, int], mystery_byte: bool,
                mystery_int: bool) -> ModelInstance:
    if version >= (1, 3):
        name = reader.fixed_string(40)
        animation_type = reader.i32()
        animation_speed = reader.f32()
        block_type = reader.i32()
    else:
        name = ""
        animation_type = 0
        animation_speed = 1.0
        block_type = 0
    if mystery_byte:
        reader.u8()
    if mystery_int:
        reader.i32()
    model_name = reader.fixed_string(80)
    node_name = reader.fixed_string(80)
    position = reader.vec3()
    rotation = reader.vec3()
    scale = reader.vec3()
    return ModelInstance(name, animation_type, animation_speed, block_type,
                         model_name, node_name, position, rotation, scale)
