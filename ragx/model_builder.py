"""RSM -> glTF-ready template conversion.

A *template* is built once per unique model file and shared by all map
instances of that model: mesh data (positions/normals/UVs grouped by
texture), a node hierarchy with local TRS transforms, and animation
channels.

Transform math follows korangar's `ModelLoader` faithfully:

RSM1 (< 2.2):
- vertices are baked with `main = T(translation1) * offset_matrix`
- a node's local transform is `T(translation2) * R(axis, angle) * S`,
  where R is dropped when rotation keyframes exist (animation replaces it)
- the root node is shifted by (-bbox.center.x, -bbox.max.y, -bbox.center.z)
  so models stand on the ground at their anchor point
- keyframe times are milliseconds

RSM2 (>= 2.2):
- vertices are raw; `translation2` / `offset_matrix` are *absolute*
  (model-space) per node, so the glTF-local transform of a node is
  computed relative to its parent:
      T_local = parent_rot^T  @ (translation2 - parent_translation2)
      R_local = parent_rot^T  @ offset_matrix
- keyframe times are frames at `frames_per_second`

Coordinate system: everything is emitted in glTF space (right-handed,
+Y up, -Z = map north). RO render space is mirrored in Z, so positions
and matrices are conjugated by diag(1,1,-1) on the way out; quaternions
map as (x,y,z,w) -> (-x,-y,z,w).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import mathutil as mu
from .formats import rsm as rsm_format


@dataclass(slots=True)
class Primitive:
    texture: str  # texture file name as stored in the model (CP949 decoded)
    positions: np.ndarray  # (n,3) float32, glTF space
    normals: np.ndarray  # (n,3) float32
    uvs: np.ndarray  # (n,2) float32
    indices: np.ndarray  # (m,) uint32
    double_sided: bool = False


@dataclass(slots=True)
class AnimChannel:
    path: str  # "translation" | "rotation" | "scale"
    times: np.ndarray  # float32 seconds
    values: np.ndarray  # (n,3) or (n,4) float32, glTF space


@dataclass(slots=True)
class NodeTemplate:
    name: str
    parent: int  # index into ModelTemplate.nodes, -1 for root
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]
    primitives: list[Primitive] = field(default_factory=list)
    channels: list[AnimChannel] = field(default_factory=list)


@dataclass(slots=True)
class ModelTemplate:
    name: str
    nodes: list[NodeTemplate]
    is_static: bool
    is_v2: bool


def build_template(model: rsm_format.Rsm, model_name: str,
                   world_scale: float = 1.0) -> ModelTemplate:
    if model.is_v2:
        return _build_v2(model, model_name, world_scale)
    return _build_v1(model, model_name, world_scale)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _node_children_order(model: rsm_format.Rsm) -> tuple[list[int], list[int]]:
    """Return (order, parent_index) replicating korangar's traversal:
    roots first, children matched by parent name, each node used once."""
    nodes = model.nodes
    processed = [False] * len(nodes)
    order: list[int] = []
    parents: list[int] = []

    name_to_indices: dict[str, list[int]] = {}
    for index, node in enumerate(nodes):
        name_to_indices.setdefault(node.name, []).append(index)

    def visit(index: int, parent_position: int) -> None:
        order.append(index)
        parents.append(parent_position)
        my_position = len(order) - 1
        for child_index, child in enumerate(nodes):
            if processed[child_index]:
                continue
            if child.parent_name == nodes[index].name and child_index != index:
                processed[child_index] = True
                visit(child_index, my_position)

    for root_name in model.root_node_names:
        for index in name_to_indices.get(root_name, []):
            if not processed[index]:
                processed[index] = True
                visit(index, -1)

    # Orphan nodes (bad parent references) become extra roots.
    for index in range(len(nodes)):
        if not processed[index]:
            processed[index] = True
            visit(index, -1)

    return order, parents


def _node_texture_list(model: rsm_format.Rsm, node: rsm_format.Node) -> list[str]:
    if model.version >= (2, 3):
        return [name for name in node.texture_names]
    return [model.textures[i] if i < len(model.textures) else "" for i in node.texture_indices]


def _smooth_model_normals(faces: list[rsm_format.Face],
                          face_normals: list[tuple[float, float, float]],
                          positions: list[tuple[float, float, float]]) \
        -> list[list[tuple[float, float, float]]]:
    """Korangar-compatible RSM smoothing in transformed position space.

    An RSM face may belong to several smoothing groups. Korangar first sums
    every face normal at a position inside each group, then a face vertex sums
    all group totals it belongs to and normalizes once. Grouping by source
    vertex index or consulting only the first group leaves seams whenever an
    artist duplicated a vertex or supplied the extra RSM 2.2 groups.
    """
    grouped: dict[tuple[int, tuple[int, int, int]], list[float]] = {}

    def position_key(position: tuple[float, float, float]) -> tuple[int, int, int]:
        return tuple(round(float(component) / 1e-6) for component in position)

    def groups_for(face: rsm_format.Face) -> list[int]:
        groups: list[int] = []
        for group in face.smooth_group:
            if group < 0:
                break
            normalized = group % 128
            if normalized not in groups:
                groups.append(normalized)
        return groups

    for face, normal in zip(faces, face_normals):
        for group in groups_for(face):
            for vertex_index in face.vertex_indices:
                key = (group, position_key(positions[vertex_index]))
                total = grouped.setdefault(key, [0.0, 0.0, 0.0])
                for axis in range(3):
                    total[axis] += normal[axis]

    smoothed: list[list[tuple[float, float, float]]] = []
    for face, fallback in zip(faces, face_normals):
        vertex_normals: list[tuple[float, float, float]] = []
        groups = groups_for(face)
        for vertex_index in face.vertex_indices:
            key = position_key(positions[vertex_index])
            total = [0.0, 0.0, 0.0]
            for group in groups:
                contribution = grouped.get((group, key))
                if contribution is not None:
                    for axis in range(3):
                        total[axis] += contribution[axis]
            length = math.sqrt(sum(component * component for component in total))
            vertex_normals.append(tuple(component / length for component in total)
                                  if length >= 1e-9 else fallback)
        smoothed.append(vertex_normals)
    return smoothed


def _bake_mesh(node: rsm_format.Node, main_matrix: mu.Matrix, textures: list[str],
               smooth: bool, flip_y: bool = False,
               world_scale: float = 1.0) -> list[Primitive]:
    """Transform vertices by main_matrix, build per-texture primitives in
    glTF space, with face or smoothing-group normals.

    `flip_y` (RSM1): the model is authored Y-down; bake the flip into the
    geometry. Combined with the RO->glTF Z-mirror this is a 180-degree
    rotation about X, so handedness is preserved.

    Triangle winding is chosen so that front faces — and the per-face normals
    derived from them below — point OUTWARD in glTF space (so the output lights
    correctly in any viewer, not only ones that flip normals on back faces).
    The non-flip path applies a Z-reflection, which reverses the source
    winding, so it reverses the triangle order to compensate; the flip_y path's
    180-degree rotation preserves the source winding, so it keeps it as-is."""
    if not node.faces:
        return []

    baked = [mu.transform_point(main_matrix, v) for v in node.vertices]
    if flip_y:
        baked = [(x, -y, -z) for (x, y, z) in baked]
    else:
        baked = [(x, y, -z) for (x, y, z) in baked]  # RO -> glTF mirror
    if world_scale != 1.0:
        baked = [tuple(component * world_scale for component in vertex)
                 for vertex in baked]

    faces = node.faces
    if not flip_y:
        faces = [
            rsm_format.Face(
                (face.vertex_indices[0], face.vertex_indices[2], face.vertex_indices[1]),
                (face.uv_indices[0], face.uv_indices[2], face.uv_indices[1]),
                face.texture_index, face.two_sided, face.smooth_group)
            for face in node.faces
        ]

    face_normals: list[tuple[float, float, float]] = []
    for face in faces:
        i0, i1, i2 = face.vertex_indices
        v0, v1, v2 = baked[i0], baked[i1], baked[i2]
        n = mu.cross(mu.sub(v1, v0), mu.sub(v2, v0))
        length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if length < 1e-9:
            n = (0.0, 1.0, 0.0)
        else:
            n = (n[0] / length, n[1] / length, n[2] / length)
        face_normals.append(n)

    smooth_normals = _smooth_model_normals(faces, face_normals, baked) \
        if smooth else []

    # Group faces by texture, deduplicating (vertex, uv, normal) tuples.
    by_texture: dict[int, dict] = {}
    for face_index, face in enumerate(faces):
        bucket = by_texture.setdefault(face.texture_index, {
            "positions": [], "normals": [], "uvs": [], "indices": [],
            "lookup": {}, "two_sided": False,
        })
        if face.two_sided:
            bucket["two_sided"] = True
        normal = face_normals[face_index]
        for corner, (vertex_index, uv_index) in enumerate(
                zip(face.vertex_indices, face.uv_indices)):
            vertex_normal = smooth_normals[face_index][corner] if smooth else normal
            uv = node.uvs[uv_index] if uv_index < len(node.uvs) else (0.0, 0.0)
            key = (vertex_index, uv_index, vertex_normal)
            existing = bucket["lookup"].get(key)
            if existing is None:
                existing = len(bucket["positions"])
                bucket["lookup"][key] = existing
                bucket["positions"].append(baked[vertex_index])
                bucket["normals"].append(vertex_normal)
                bucket["uvs"].append(uv)
            bucket["indices"].append(existing)

    primitives = []
    for texture_index, bucket in by_texture.items():
        texture = textures[texture_index] if texture_index < len(textures) else ""
        primitives.append(Primitive(
            texture=texture,
            positions=np.asarray(bucket["positions"], dtype=np.float32),
            normals=np.asarray(bucket["normals"], dtype=np.float32),
            uvs=np.asarray(bucket["uvs"], dtype=np.float32),
            indices=np.asarray(bucket["indices"], dtype=np.uint32),
            double_sided=True,  # RO never culls reliably; keep everything visible
        ))
    return primitives


# --------------------------------------------------------------------------
# RSM1
# --------------------------------------------------------------------------

def _build_v1(model: rsm_format.Rsm, model_name: str,
              world_scale: float) -> ModelTemplate:
    order, parents = _node_children_order(model)
    nodes = model.nodes
    smooth = model.shade_type == 2

    # --- bounding box in model space (korangar box_transform chain) ----
    box_min = [float("inf")] * 3
    box_max = [float("-inf")] * 3
    box_transforms: list[mu.Matrix] = [mu.IDENTITY] * len(order)
    for position, node_index in enumerate(order):
        node = nodes[node_index]
        parent_box = mu.IDENTITY if parents[position] < 0 else box_transforms[parents[position]]
        local = mu.mat_mul(
            mu.translation(node.translation2),
            mu.mat_mul(
                mu.axis_angle_matrix(node.rotation_axis or (0.0, 0.0, 0.0), node.rotation_angle or 0.0),
                mu.scaling(node.scale or (1.0, 1.0, 1.0)),
            ),
        )
        box_transform = mu.mat_mul(parent_box, local)
        box_transforms[position] = box_transform
        main = mu.mat_mul(mu.translation(node.translation1 or (0.0, 0.0, 0.0)),
                          mu.mat3_to_mat4(node.offset_matrix))
        box_matrix = mu.mat_mul(box_transform, main)
        for vertex in node.vertices:
            x, y, z = mu.transform_point(box_matrix, vertex)
            box_min[0] = min(box_min[0], x); box_max[0] = max(box_max[0], x)
            box_min[1] = min(box_min[1], y); box_max[1] = max(box_max[1], y)
            box_min[2] = min(box_min[2], z); box_max[2] = max(box_max[2], z)

    if not math.isfinite(box_min[0]):
        box_min = box_max = [0.0, 0.0, 0.0]

    center = [(box_min[i] + box_max[i]) / 2.0 for i in range(3)]
    root_shift = (-center[0], -box_max[1], -center[2])  # RO space

    templates: list[NodeTemplate] = []
    is_static = True

    for position, node_index in enumerate(order):
        node = nodes[node_index]
        textures = _node_texture_list(model, node)
        main = mu.mat_mul(mu.translation(node.translation1 or (0.0, 0.0, 0.0)),
                          mu.mat3_to_mat4(node.offset_matrix))
        # RSM1 geometry is authored Y-down; bake the flip so standalone
        # model files stand upright and instances need no negative scale.
        primitives = _bake_mesh(node, main, textures, smooth, flip_y=True,
                                world_scale=world_scale)

        has_rotation_anim = bool(node.rotation_keyframes)
        has_scale_anim = bool(node.scale_keyframes)
        if has_rotation_anim or has_scale_anim:
            is_static = False

        translation = list(node.translation2)
        if parents[position] < 0:
            translation[0] += root_shift[0]
            translation[1] += root_shift[1]
            translation[2] += root_shift[2]

        if has_rotation_anim:
            rotation = (0.0, 0.0, 0.0, 1.0)  # animation channel will drive it
        else:
            rotation = mu.axis_angle_quat(node.rotation_axis or (0.0, 0.0, 0.0),
                                          node.rotation_angle or 0.0)
        scale = node.scale or (1.0, 1.0, 1.0)

        channels: list[AnimChannel] = []
        if has_rotation_anim:
            times = np.asarray([k[0] / 1000.0 for k in node.rotation_keyframes], dtype=np.float32)
            quats = np.asarray(
                [mu.rot_x180_quat(mu.quat_normalize((k[1], k[2], k[3], k[4])))
                 for k in node.rotation_keyframes], dtype=np.float32)
            channels.append(AnimChannel("rotation", times, quats))
        if has_scale_anim:
            times = np.asarray([k[0] / 1000.0 for k in node.scale_keyframes], dtype=np.float32)
            scales = np.asarray([k[1:4] for k in node.scale_keyframes], dtype=np.float32)
            channels.append(AnimChannel("scale", times, scales))

        templates.append(NodeTemplate(
            name=node.name or f"node{node_index}",
            parent=parents[position],
            translation=tuple(component * world_scale for component in
                              mu.rot_x180_point(tuple(translation))),
            rotation=mu.rot_x180_quat(rotation),
            scale=tuple(scale),
            primitives=primitives,
            channels=channels,
        ))

    return ModelTemplate(model_name, templates, is_static, is_v2=False)


# --------------------------------------------------------------------------
# RSM2
# --------------------------------------------------------------------------

def _build_v2(model: rsm_format.Rsm, model_name: str,
              world_scale: float) -> ModelTemplate:
    order, parents = _node_children_order(model)
    nodes = model.nodes
    smooth = model.shade_type == 2
    fps = model.frames_per_second or 60.0

    templates: list[NodeTemplate] = []
    is_static = True

    for position, node_index in enumerate(order):
        node = nodes[node_index]
        textures = _node_texture_list(model, node)
        primitives = _bake_mesh(node, mu.IDENTITY, textures, smooth,
                                world_scale=world_scale)

        if parents[position] < 0:
            parent_rotation = mu.IDENTITY
            parent_translation = (0.0, 0.0, 0.0)
        else:
            parent_node = nodes[order[parents[position]]]
            parent_rotation = mu.mat3_to_mat4(parent_node.offset_matrix)
            parent_translation = parent_node.translation2

        parent_rotation_inv = mu.mat3_transpose4(parent_rotation)

        # Static local TRS (relative to parent's absolute transform).
        delta = mu.sub(node.translation2, parent_translation)
        local_translation = mu.transform_direction(parent_rotation_inv, delta)
        local_rotation_matrix = mu.mat_mul(parent_rotation_inv, mu.mat3_to_mat4(node.offset_matrix))
        local_rotation = mu.matrix_to_quat(local_rotation_matrix)

        channels: list[AnimChannel] = []
        if node.rotation_keyframes:
            is_static = False
            times = np.asarray([k[0] / fps for k in node.rotation_keyframes], dtype=np.float32)
            quats = np.asarray(
                [mu.mirror_z_quat(mu.quat_normalize((k[1], k[2], k[3], k[4])))
                 for k in node.rotation_keyframes], dtype=np.float32)
            channels.append(AnimChannel("rotation", times, quats))
        if node.translation_keyframes:
            is_static = False
            times = np.asarray([k[0] / fps for k in node.translation_keyframes], dtype=np.float32)
            values = np.asarray([
                tuple(component * world_scale for component in
                      mu.mirror_z_point(tuple(k[1:4])))
                for k in node.translation_keyframes], dtype=np.float32)
            channels.append(AnimChannel("translation", times, values))
        if node.scale_keyframes:
            is_static = False
            times = np.asarray([k[0] / fps for k in node.scale_keyframes], dtype=np.float32)
            values = np.asarray([k[1:4] for k in node.scale_keyframes], dtype=np.float32)
            channels.append(AnimChannel("scale", times, values))

        templates.append(NodeTemplate(
            name=node.name or f"node{node_index}",
            parent=parents[position],
            translation=tuple(component * world_scale for component in
                              mu.mirror_z_point(tuple(local_translation))),
            rotation=mu.mirror_z_quat(mu.quat_normalize(local_rotation)),
            scale=(1.0, 1.0, 1.0),
            primitives=primitives,
            channels=channels,
        ))

    return ModelTemplate(model_name, templates, is_static, is_v2=True)
