"""RSM / RSM2 (3D model) parser.

Versions in the LATAM client: 1.4, 1.5 (classic .rsm) and 2.2, 2.3
(.rsm2). Layout follows korangar's `ragnarok-formats` `model.rs`.

Version highlights:
- >= 1.4: alpha byte after shade type
- >= 1.5: scale keyframes per node
- >= 2.2: strings become length-prefixed, fps field appears, multiple
  root nodes, translation keyframes, faces gain a length field and
  extra smoothing groups; node static rotation/scale/translation1
  disappear (offset matrix + keyframes carry everything)
- >= 2.3: textures move from the model header into each node (by name),
  texture animation keyframes appear
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .binio import Reader

SIGNATURE = b"GRSM"


@dataclass(slots=True)
class Face:
    vertex_indices: tuple[int, int, int]
    uv_indices: tuple[int, int, int]
    texture_index: int
    two_sided: int
    smooth_group: tuple[int, ...]


@dataclass(slots=True)
class TextureAnimation:
    texture_index: int
    # operation -> list of (frame, value); operations: 0/1 translate UV,
    # 2/3 scale UV, 4 rotate
    channels: dict[int, list[tuple[int, float]]]


@dataclass(slots=True)
class Node:
    name: str
    parent_name: str
    texture_indices: list[int]  # < 2.3 (indices into model textures)
    texture_names: list[str]  # >= 2.3
    offset_matrix: tuple[float, ...]  # 3x3, column-major as stored
    translation1: tuple[float, float, float] | None  # < 2.2
    translation2: tuple[float, float, float]
    rotation_angle: float | None  # radians, < 2.2
    rotation_axis: tuple[float, float, float] | None
    scale: tuple[float, float, float] | None
    vertices: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]  # already includes the color word for >=1.2
    uv_colors: list[int]
    faces: list[Face]
    scale_keyframes: list[tuple[int, float, float, float]]
    rotation_keyframes: list[tuple[int, float, float, float, float]]  # frame, x, y, z, w
    translation_keyframes: list[tuple[int, float, float, float]]
    texture_animations: list[TextureAnimation] = field(default_factory=list)


@dataclass(slots=True)
class Rsm:
    version: tuple[int, int]
    animation_length: int
    shade_type: int  # 0 = none, 1 = flat, 2 = smooth
    alpha: int
    frames_per_second: float
    textures: list[str]  # model-level (< 2.3)
    root_node_names: list[str]
    nodes: list[Node]

    @property
    def is_v2(self) -> bool:
        return self.version >= (2, 2)


def parse(data: bytes) -> Rsm:
    reader = Reader(data)
    if reader.bytes(4) != SIGNATURE:
        raise ValueError("not an RSM file")
    version = (reader.u8(), reader.u8())

    def model_string(fixed_length: int = 40) -> str:
        if version >= (2, 2):
            length = reader.u32()
            raw = reader.bytes(length)
            end = raw.find(b"\0")
            if end >= 0:
                raw = raw[:end]
            return raw.decode("cp949", errors="replace")
        return reader.fixed_string(fixed_length)

    animation_length = reader.u32()
    shade_type = reader.u32()
    alpha = reader.u8() if version >= (1, 4) else 255

    frames_per_second = 60.0
    if version < (2, 2):
        reader.skip(16)  # reserved
    else:
        frames_per_second = reader.f32()

    textures: list[str] = []
    if version < (2, 3):
        texture_count = reader.u32()
        textures = [model_string() for _ in range(texture_count)]

    if version < (2, 2):
        root_node_names = [model_string()]
    else:
        root_count = reader.u32()
        root_node_names = [model_string() for _ in range(root_count)]

    node_count = reader.u32()
    nodes = [_read_node(reader, version, model_string) for _ in range(node_count)]

    # Old single-root files sometimes leave junk in the root name; korangar
    # trusts it, but guard against models whose root name matches no node.
    node_names = {node.name for node in nodes}
    root_node_names = [name for name in root_node_names if name in node_names]
    if not root_node_names and nodes:
        root_node_names = [nodes[0].name]

    return Rsm(version, animation_length, shade_type, alpha, frames_per_second,
               textures, root_node_names, nodes)


def _read_node(reader: Reader, version: tuple[int, int], model_string) -> Node:
    name = model_string()
    parent_name = model_string()

    texture_indices: list[int] = []
    texture_names: list[str] = []
    if version < (2, 3):
        texture_count = reader.u32()
        texture_indices = [reader.u32() for _ in range(texture_count)]
    else:
        texture_name_count = reader.u32()
        texture_names = [model_string() for _ in range(texture_name_count)]

    offset_matrix = reader.f32s(9)

    translation1 = None
    rotation_angle = None
    rotation_axis = None
    scale = None
    if version < (2, 2):
        translation1 = reader.vec3()
    translation2 = reader.vec3()
    if version < (2, 2):
        rotation_angle = reader.f32()
        rotation_axis = reader.vec3()
        scale = reader.vec3()

    vertex_count = reader.u32()
    vertices = [reader.vec3() for _ in range(vertex_count)]

    uv_count = reader.u32()
    uvs: list[tuple[float, float]] = []
    uv_colors: list[int] = []
    for _ in range(uv_count):
        color = reader.u32() if version >= (1, 2) else 0xFFFFFFFF
        uv_colors.append(color)
        uvs.append(reader.vec2())

    face_count = reader.u32()
    faces: list[Face] = []
    for _ in range(face_count):
        length = reader.u32() if version >= (2, 2) else 24
        v0, v1, v2 = reader.u16(), reader.u16(), reader.u16()
        t0, t1, t2 = reader.u16(), reader.u16(), reader.u16()
        texture_index = reader.u16()
        reader.u16()  # padding
        two_sided = reader.i32()
        smooth_group = [reader.i32()]
        extra = (length - 24) // 4
        for _ in range(extra):
            smooth_group.append(reader.i32())
        faces.append(Face((v0, v1, v2), (t0, t1, t2), texture_index, two_sided, tuple(smooth_group)))

    scale_keyframes: list[tuple[int, float, float, float]] = []
    if version >= (1, 6):
        count = reader.u32()
        for _ in range(count):
            frame = reader.i32()
            sx, sy, sz = reader.vec3()
            reader.f32()  # reserved
            scale_keyframes.append((frame, sx, sy, sz))

    rotation_keyframes: list[tuple[int, float, float, float, float]] = []
    count = reader.u32()
    for _ in range(count):
        frame = reader.i32()
        qx, qy, qz, qw = reader.vec4()
        rotation_keyframes.append((frame, qx, qy, qz, qw))

    translation_keyframes: list[tuple[int, float, float, float]] = []
    if version >= (2, 2):
        count = reader.u32()
        for _ in range(count):
            frame = reader.i32()
            tx, ty, tz = reader.vec3()
            reader.f32()  # reserved
            translation_keyframes.append((frame, tx, ty, tz))

    texture_animations: list[TextureAnimation] = []
    if version >= (2, 3):
        textures_keyframe_count = reader.u32()
        for _ in range(textures_keyframe_count):
            texture_index = reader.u32()
            channel_count = reader.u32()
            channels: dict[int, list[tuple[int, float]]] = {}
            for _ in range(channel_count):
                operation = reader.u32()
                frame_count = reader.u32()
                channels[operation] = [(reader.i32(), reader.f32()) for _ in range(frame_count)]
            texture_animations.append(TextureAnimation(texture_index, channels))

    return Node(
        name=name,
        parent_name=parent_name,
        texture_indices=texture_indices,
        texture_names=texture_names,
        offset_matrix=offset_matrix,
        translation1=translation1,
        translation2=translation2,
        rotation_angle=rotation_angle,
        rotation_axis=rotation_axis,
        scale=scale,
        vertices=vertices,
        uvs=uvs,
        uv_colors=uv_colors,
        faces=faces,
        scale_keyframes=scale_keyframes,
        rotation_keyframes=rotation_keyframes,
        translation_keyframes=translation_keyframes,
        texture_animations=texture_animations,
    )
