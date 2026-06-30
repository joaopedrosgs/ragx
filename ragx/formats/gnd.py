"""GND (ground/terrain) parser.

Versions seen in the wild: 1.7 (vast majority), 1.8 and 1.9 (Renewal maps
with water plane configuration moved out of the RSW).

Geometry model (see references/RagnarokFileFormats/GND.MD):
- The map is a grid of `width * height` cubes, each 2x2 GAT tiles
  (10 world units across at the standard zoom of 10).
- A cube stores the height of its four corners and up to three surface
  references: TOP, NORTH (wall to the cube above) and EAST (wall to the
  cube to the right). -1 means "no surface".
- A surface carries UVs for its four corners, a texture index, a
  lightmap slice index and one BGRA color applied to the bottom-left
  vertex of the tile.
- Heights are stored with +Y pointing *down*; the converter negates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .binio import Reader

SIGNATURE = b"GRGN"


@dataclass(slots=True)
class Surface:
    u: tuple[float, float, float, float]
    v: tuple[float, float, float, float]
    texture_index: int
    light_map_index: int
    color_rgba: tuple[int, int, int, int]  # converted from stored BGRA


@dataclass(slots=True)
class Cube:
    h_sw: float
    h_se: float
    h_nw: float
    h_ne: float
    top_surface: int
    north_surface: int
    east_surface: int


@dataclass(slots=True)
class WaterPlane:
    level: float
    water_type: int
    wave_height: float
    wave_speed: float
    wave_pitch: float
    texture_cycling_interval: int


@dataclass(slots=True)
class Gnd:
    version: tuple[int, int]
    width: int
    height: int
    zoom: float
    textures: list[str]
    surfaces: list[Surface]
    cubes: list[Cube]
    # Lightmap data, kept raw: per slice 64 bytes of shadow (alpha) then
    # 192 bytes of RGB lightmap color (8x8 texels each).
    lightmap_count: int = 0
    lightmap_data: bytes = b""
    lightmap_size: tuple[int, int] = (8, 8)
    water_planes: list[WaterPlane] = field(default_factory=list)
    water_grid: tuple[int, int] = (1, 1)


def parse(data: bytes) -> Gnd:
    reader = Reader(data)
    if reader.bytes(4) != SIGNATURE:
        raise ValueError("not a GND file")
    major, minor = reader.u8(), reader.u8()
    version = (major, minor)

    width = reader.i32()
    height = reader.i32()
    zoom = reader.f32()
    texture_count = reader.i32()
    texture_name_length = reader.i32()
    textures = [reader.fixed_string(texture_name_length) for _ in range(texture_count)]

    lightmap_count = reader.i32()
    lightmap_width = reader.i32()
    lightmap_height = reader.i32()
    _cells_per_grid = reader.i32()
    per_slice = lightmap_width * lightmap_height * 4
    if version >= (1, 7):
        lightmap_data = reader.bytes(lightmap_count * per_slice)
    else:
        lightmap_data = reader.bytes(lightmap_count * 16)

    surface_count = reader.i32()
    surfaces: list[Surface] = []
    for _ in range(surface_count):
        u = reader.f32s(4)
        v = reader.f32s(4)
        texture_index = reader.i16()
        light_map_index = reader.i16()
        b, g, r, a = reader.bytes(4)
        surfaces.append(Surface(u, v, texture_index, light_map_index, (r, g, b, a)))

    cubes: list[Cube] = []
    wide_indices = version >= (1, 7)
    for _ in range(width * height):
        h_sw = reader.f32()
        h_se = reader.f32()
        h_nw = reader.f32()
        h_ne = reader.f32()
        if wide_indices:
            top, north, east = reader.i32(), reader.i32(), reader.i32()
        else:
            top, north, east = reader.i16(), reader.i16(), reader.i16()
        cubes.append(Cube(h_sw, h_se, h_nw, h_ne, top, north, east))

    gnd = Gnd(
        version=version,
        width=width,
        height=height,
        zoom=zoom,
        textures=textures,
        surfaces=surfaces,
        cubes=cubes,
        lightmap_count=lightmap_count,
        lightmap_data=lightmap_data,
        lightmap_size=(lightmap_width, lightmap_height),
    )

    if version >= (1, 8):
        base = _read_water_plane(reader)
        num_u = reader.i32()
        num_v = reader.i32()
        gnd.water_grid = (num_u, num_v)
        if version >= (1, 9):
            gnd.water_planes = [_read_water_plane(reader) for _ in range(num_u * num_v)]
            if not gnd.water_planes:
                gnd.water_planes = [base]
                gnd.water_grid = (1, 1)
        else:
            # 1.8 stores only the water level for each extra plane (and in
            # practice there is always exactly one).
            levels = [reader.f32() for _ in range(num_u * num_v)]
            gnd.water_planes = [
                WaterPlane(level, base.water_type, base.wave_height, base.wave_speed,
                           base.wave_pitch, base.texture_cycling_interval)
                for level in levels
            ] or [base]

    return gnd


def _read_water_plane(reader: Reader) -> WaterPlane:
    return WaterPlane(
        level=reader.f32(),
        water_type=reader.i32(),
        wave_height=reader.f32(),
        wave_speed=reader.f32(),
        wave_pitch=reader.f32(),
        texture_cycling_interval=reader.i32(),
    )
