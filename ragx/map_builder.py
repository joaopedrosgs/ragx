"""Assemble one map (RSW + GND + RSM models) into a single GLB.

Coordinate system of the output: glTF standard (right-handed, +Y up),
with -Z pointing to the map's north and the terrain centered on the
origin. One world unit = 1/10 of a GND cube = half a GAT tile... in
other words the original client world units are kept unchanged.

Scene structure:
    terrain            (one mesh, one primitive per terrain texture)
    water              (optional, semi-transparent)
    sun                (KHR_lights_punctual directional light)
    <instance nodes>   (one subtree per RSW model, meshes shared)

Animations: all RSM keyframe animations are merged into one glTF
animation called "scene" so viewers autoplay everything at once.
"""

from __future__ import annotations

import math
import os
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import mathutil as mu
from .formats import gnd as gnd_format
from .formats import rsm as rsm_format
from .formats import rsw as rsw_format
from .gltf import ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, FLOAT, UNSIGNED_BYTE, UNSIGNED_INT, GltfBuilder
from .grf import normalize_path
from .model_builder import ModelTemplate, build_template
from .textures import LoadedTexture, convert_texture

WATER_OPACITY = 144.0 / 255.0


class MapHasNoTerrain(Exception):
    """The client does not ship terrain data for this map."""


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file + rename so parallel workers writing the same
    shared file (model glTFs) can never interleave."""
    temporary = path.parent / (path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    try:
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


class AssetSource:
    """File access abstraction (GRF stack or extracted folder)."""

    def __init__(self, reader):
        self._reader = reader

    def read(self, path: str) -> bytes:
        return self._reader.read(path)

    def try_read(self, path: str) -> bytes | None:
        try:
            return self._reader.read(path)
        except (FileNotFoundError, KeyError):
            return None

    def namelist(self) -> list[str]:
        return self._reader.namelist()


@dataclass
class BuildStats:
    missing_models: list[str] = field(default_factory=list)
    missing_textures: list[str] = field(default_factory=list)
    model_errors: list[str] = field(default_factory=list)
    instances: int = 0
    animated_instances: int = 0


class MapBuilder:
    def __init__(self, source: AssetSource, texture_dir: str | os.PathLike | None = None):
        """`texture_dir`: where shared PNG files go for .gltf output (one
        file per unique texture, reused by every map that references it)."""
        self.source = source
        self.texture_dir = Path(texture_dir) if texture_dir is not None else None
        self.texture_cache: dict[str, LoadedTexture | None] = {}
        self.template_cache: dict[str, ModelTemplate | None] = {}
        self._written_textures: set[str] = set()
        self._collision_stems: set[str] | None = None

    # ---- texture/material helpers ------------------------------------

    _TEXTURE_CACHE_LIMIT = 2048  # bounds memory during long batch runs

    def _load_texture(self, name: str) -> LoadedTexture | None:
        key = normalize_path("data\\texture\\" + name)
        if key in self.texture_cache:
            return self.texture_cache[key]
        if len(self.texture_cache) >= self._TEXTURE_CACHE_LIMIT:
            self.texture_cache.clear()
        raw = self.source.try_read(key)
        result: LoadedTexture | None = None
        if raw is not None:
            try:
                result = convert_texture(raw, key)
            except Exception:
                result = None
        self.texture_cache[key] = result
        return result

    def _texture_relpath(self, texture_name: str) -> str:
        """Shared on-disk path (relative, forward slashes) for a texture,
        with the extension replaced by .png. A handful of textures exist
        as both `x.bmp` and `x.tga` (201 in the LATAM client); only those
        keep the original extension in the name to stay unambiguous."""
        key = normalize_path(texture_name)
        stem, dot, _ext = key.rpartition(".")
        if dot and stem not in self._texture_collisions():
            return stem.replace("\\", "/") + ".png"
        return key.replace("\\", "/") + ".png"

    def _texture_collisions(self) -> set[str]:
        if self._collision_stems is None:
            stems: dict[str, str] = {}
            collisions: set[str] = set()
            try:
                names = self.source.namelist()
            except AttributeError:
                names = []
            prefix = "data\\texture\\"
            for name in names:
                if not name.startswith(prefix):
                    continue
                stem, dot, ext = name[len(prefix):].rpartition(".")
                if not dot:
                    continue
                previous = stems.get(stem)
                if previous is not None and previous != ext:
                    collisions.add(stem)
                stems[stem] = ext
            self._collision_stems = collisions
        return self._collision_stems

    def _write_shared_texture(self, texture_name: str, texture: LoadedTexture,
                              uri_base: str = "") -> str:
        """Write the PNG once into texture_dir; return a URI relative to
        the referencing glTF file. `uri_base` climbs out of subfolders
        (e.g. "../../" for a model two levels deep)."""
        relpath = self._texture_relpath(texture_name)
        if relpath not in self._written_textures:
            target = self.texture_dir / relpath
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Atomic write: parallel workers may race on the same file.
                temporary = target.parent / (target.name + f".{os.getpid()}.tmp")
                temporary.write_bytes(texture.png)
                try:
                    os.replace(temporary, target)
                except OSError:
                    temporary.unlink(missing_ok=True)
            self._written_textures.add(relpath)
        uri_path = "/".join(urllib.parse.quote(part) for part in relpath.split("/"))
        return f"{uri_base}{self.texture_dir.name}/{uri_path}"

    def build(self, map_name: str, out_path: str) -> BuildStats:
        stats = BuildStats()
        source = self.source

        rsw = rsw_format.parse(source.read(f"data\\{map_name}.rsw"))
        gnd = gnd_format.parse(self._read_gnd(rsw, map_name))

        external_textures = str(out_path).lower().endswith(".gltf")
        if external_textures and self.texture_dir is None:
            raise ValueError("texture_dir is required for .gltf output")

        builder = GltfBuilder()
        sampler = builder.add_sampler(wrap=True)
        material_for, add_image = self._material_factory(
            builder, sampler, stats, external_textures, uri_base="")

        # ---- terrain ----------------------------------------------------
        terrain_node = self._build_terrain(builder, gnd, material_for)
        builder.add_scene_node(terrain_node)

        # ---- water -------------------------------------------------------
        water_node = self._build_water(builder, gnd, rsw, sampler, stats, add_image)
        if water_node is not None:
            builder.add_scene_node(water_node)

        # ---- sun light ----------------------------------------------------
        light_node = self._build_sun(builder, rsw)
        builder.add_scene_node(light_node)

        # ---- models --------------------------------------------------------
        mesh_cache: dict[tuple[str, int], int | None] = {}
        accessor_cache: dict[tuple[int, float], tuple[int, int]] = {}

        for instance in rsw.models:
            template = self._load_template(instance.model_name, stats)
            if template is None:
                continue
            stats.instances += 1
            animated = self._instantiate(builder, template, instance,
                                         material_for, mesh_cache, accessor_cache)
            if animated:
                stats.animated_instances += 1

        # Scene metadata for downstream tools.
        builder.json["scenes"][0]["extras"] = {
            "map": map_name,
            "ambientColor": list(rsw.light.ambient),
            "diffuseColor": list(rsw.light.diffuse),
        }

        self._write_output(builder, out_path, external_textures)
        return stats

    def _material_factory(self, builder: GltfBuilder, sampler: int, stats: BuildStats,
                          external_textures: bool, uri_base: str):
        """Return (material_for, add_image) closures bound to a builder."""
        material_indices: dict[str, int] = {}
        fallback_material: int | None = None

        def add_image(texture_name: str, texture: LoadedTexture) -> int:
            if external_textures:
                uri = self._write_shared_texture(texture_name, texture, uri_base)
                return builder.add_image_uri(uri, texture_name)
            return builder.add_image_png(texture.png, texture_name)

        def material_for(texture_name: str) -> int:
            nonlocal fallback_material
            key = normalize_path(texture_name) if texture_name else ""
            if key in material_indices:
                return material_indices[key]
            if key.endswith(".bik"):
                # Bink video texture (animated billboards); not embeddable.
                index = builder.add_material({
                    "name": texture_name,
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [0.35, 0.35, 0.35, 1.0],
                        "metallicFactor": 0.0,
                        "roughnessFactor": 1.0,
                    },
                    "doubleSided": True,
                })
                material_indices[key] = index
                return index
            texture = self._load_texture(texture_name) if texture_name else None
            if texture is None:
                if texture_name:
                    stats.missing_textures.append(texture_name)
                if fallback_material is None:
                    fallback_material = builder.add_material({
                        "name": "missing",
                        "pbrMetallicRoughness": {
                            "baseColorFactor": [1.0, 0.0, 1.0, 1.0],
                            "metallicFactor": 0.0,
                            "roughnessFactor": 1.0,
                        },
                        "doubleSided": True,
                    })
                material_indices[key] = fallback_material
                return fallback_material
            image = add_image(texture_name, texture)
            texture_index = builder.add_texture(image, sampler)
            material = {
                "name": texture_name,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": texture_index},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
                "doubleSided": True,
            }
            if texture.has_alpha:
                material["alphaMode"] = "MASK"
                material["alphaCutoff"] = 0.5
            index = builder.add_material(material)
            material_indices[key] = index
            return index

        return material_for, add_image

    def _write_output(self, builder: GltfBuilder, out_path: str | os.PathLike,
                      external_textures: bool) -> None:
        out_path = Path(out_path)
        if external_textures:
            bin_name = out_path.stem + ".bin"
            json_bytes, bin_bytes = builder.to_gltf(urllib.parse.quote(bin_name))
            _atomic_write(out_path, json_bytes)
            _atomic_write(out_path.with_name(bin_name), bin_bytes)
        else:
            _atomic_write(out_path, builder.to_glb())

    # ---- decomposed exports (Godot pipeline) --------------------------

    def _water_planes(self, gnd: gnd_format.Gnd, rsw: rsw_format.Rsw):
        """Resolve water configuration (RSW < 2.6 or GND >= 1.8)."""
        if gnd.water_planes:
            return gnd.water_planes, gnd.water_grid
        if rsw.water is not None:
            water = rsw.water
            return [gnd_format.WaterPlane(water.level, water.water_type, water.wave_height,
                                          water.wave_speed, water.wave_pitch,
                                          water.texture_cycling_interval)], (1, 1)
        return [], (1, 1)

    def build_terrain_gltf(self, map_name: str, out_path: str | os.PathLike,
                           uri_base: str = "../", include_water: bool = True) -> BuildStats:
        """Terrain (+ optionally water) as one .gltf, no models/lights."""
        stats = BuildStats()
        rsw = rsw_format.parse(self.source.read(f"data\\{map_name}.rsw"))
        gnd = gnd_format.parse(self._read_gnd(rsw, map_name))

        builder = GltfBuilder()
        sampler = builder.add_sampler(wrap=True)
        material_for, add_image = self._material_factory(builder, sampler, stats, True, uri_base)

        builder.add_scene_node(self._build_terrain(builder, gnd, material_for))
        if include_water:
            water_node = self._build_water(builder, gnd, rsw, sampler, stats, add_image)
            if water_node is not None:
                builder.add_scene_node(water_node)
        builder.json["scenes"][0]["extras"] = {
            "map": map_name,
            "ambientColor": list(rsw.light.ambient),
            "diffuseColor": list(rsw.light.diffuse),
        }
        self._write_output(builder, out_path, True)
        return stats

    def build_water_gltf(self, map_name: str, out_path: str | os.PathLike,
                         uri_base: str = "../") -> dict | None:
        """Water plane as its own .gltf, plus all 32 animation frames in
        the shared texture folder. Returns the water parameters (for an
        engine-side shader) or None when the map has no water."""
        stats = BuildStats()
        rsw = rsw_format.parse(self.source.read(f"data\\{map_name}.rsw"))
        gnd = gnd_format.parse(self._read_gnd(rsw, map_name))
        planes, _grid = self._water_planes(gnd, rsw)
        if not planes:
            return None

        builder = GltfBuilder()
        sampler = builder.add_sampler(wrap=True)
        _, add_image = self._material_factory(builder, sampler, stats, True, uri_base)
        water_node = self._build_water(builder, gnd, rsw, sampler, stats, add_image)
        if water_node is None:
            return None
        builder.add_scene_node(water_node)
        self._write_output(builder, out_path, True)

        plane = planes[0]
        # Export every animation frame of the used water types.
        frame_uris: list[str] = []
        for frame in range(32):
            name = f"워터\\water{plane.water_type}{frame:02d}.jpg"
            texture = self._load_texture(name)
            if texture is None:
                break
            relpath = self._texture_relpath(name)
            self._write_shared_texture(name, texture)
            frame_uris.append(relpath)

        return {
            "type": plane.water_type,
            "level": -plane.level,
            "waveHeight": plane.wave_height,
            "waveSpeed": plane.wave_speed,
            "wavePitch": plane.wave_pitch,
            "cycleInterval": max(plane.texture_cycling_interval, 1),
            "opacity": 1.0 if plane.water_type in (4, 6) else WATER_OPACITY,
            "frames": frame_uris,
        }

    # Walkable GAT cell types (see korangar TileFlags): 0 = walkable,
    # 3 = walkable water. Everything else blocks ground movement.
    _WALKABLE_TYPES = (0, 3)

    def build_nav_gltf(self, map_name: str, out_path: str | os.PathLike) -> bool:
        """Walkable-surface mesh from the GAT (one quad per walkable
        tile, vertices welded) for navigation-mesh baking. Returns False
        when the map has no GAT."""
        from .formats import gat as gat_format

        rsw = rsw_format.parse(self.source.read(f"data\\{map_name}.rsw"))
        raw = self.source.try_read("data\\" + (rsw.gat_file or f"{map_name}.gat"))
        if raw is None:
            raw = self.source.try_read(f"data\\{map_name}.gat")
        if raw is None:
            return False
        gat = gat_format.parse(raw)

        offset_x = gat.width * 2.5  # GAT tiles are 5 world units
        offset_z = gat.height * 2.5
        vertices: list[tuple[float, float, float]] = []
        lookup: dict[tuple[int, int, int], int] = {}
        indices: list[int] = []

        def vertex(x_tile: float, height: float, z_tile: float) -> int:
            key = (int(x_tile * 2), int(round(height * 100)), int(z_tile * 2))
            existing = lookup.get(key)
            if existing is None:
                existing = len(vertices)
                lookup[key] = existing
                vertices.append((x_tile * 5.0 - offset_x, -height, -(z_tile * 5.0 - offset_z)))
            return existing

        for index, (h_sw, h_se, h_nw, h_ne, tile_type) in enumerate(gat.tiles):
            if tile_type not in self._WALKABLE_TYPES:
                continue
            x = index % gat.width
            z = index // gat.width
            sw = vertex(x, h_sw, z)
            se = vertex(x + 1, h_se, z)
            nw = vertex(x, h_nw, z + 1)
            ne = vertex(x + 1, h_ne, z + 1)
            # CCW in glTF space (normal up): (SW, SE, NW), (NW, SE, NE)
            indices.extend((sw, se, nw, nw, se, ne))

        if not indices:
            return False

        builder = GltfBuilder()
        positions = np.asarray(vertices, dtype=np.float32)
        primitive = {
            "attributes": {
                "POSITION": builder.add_accessor(positions, "VEC3", FLOAT, ARRAY_BUFFER, minmax=True),
            },
            "indices": builder.add_accessor(np.asarray(indices, dtype=np.uint32), "SCALAR",
                                            UNSIGNED_INT, ELEMENT_ARRAY_BUFFER),
        }
        mesh = builder.add_mesh([primitive], name="navsource")
        builder.add_scene_node(builder.add_node(name="navsource", mesh=mesh))
        self._write_output(builder, out_path, True)
        return True

    def build_model_gltf(self, model_name: str, out_path: str | os.PathLike,
                         uri_base: str, effective_speed: float = 1.0,
                         flip_winding: bool = False) -> BuildStats | None:
        """One RSM model as a standalone .gltf with its animation (named
        with a -loop suffix so Godot's importer marks it looping).
        Returns None if the model cannot be loaded."""
        stats = BuildStats()
        template = self._load_template(model_name, stats)
        if template is None:
            return None

        builder = GltfBuilder()
        sampler = builder.add_sampler(wrap=True)
        material_for, _ = self._material_factory(builder, sampler, stats, True, uri_base)

        mesh_cache: dict = {}
        node_ids: list[int] = []
        children_of: dict[int, list[int]] = defaultdict(list)
        animation = None

        for index, node in enumerate(template.nodes):
            mesh_index = self._mesh_for(builder, template, index, material_for, mesh_cache,
                                        flip_winding=flip_winding)
            node_id = builder.add_node(
                name=node.name,
                mesh=mesh_index,
                translation=node.translation,
                rotation=node.rotation,
                scale=node.scale,
            )
            node_ids.append(node_id)
            if node.parent >= 0:
                children_of[node.parent].append(node_id)
            for channel in node.channels:
                if animation is None:
                    stem = Path(normalize_path(model_name).replace("\\", "/")).stem
                    animation = builder.add_animation(f"{stem}-loop")
                times = channel.times / effective_speed if effective_speed != 1.0 else channel.times
                builder.add_animation_channel(
                    animation, node_id, channel.path,
                    times.astype(np.float32), channel.values.astype(np.float32))

        for parent_index, child_ids in children_of.items():
            builder.json["nodes"][node_ids[parent_index]].setdefault("children", []).extend(child_ids)
        for index, node in enumerate(template.nodes):
            if node.parent < 0:
                builder.add_scene_node(node_ids[index])

        self._write_output(builder, out_path, True)
        return stats

    def _read_gnd(self, rsw: rsw_format.Rsw, map_name: str) -> bytes:
        """Event-variant maps (pay_dun00_a, moc_para0a, ...) declare a GND
        that does not exist and reuse the base map's terrain. A few maps
        (unreleased content) genuinely have no GND in this client."""
        candidates = [rsw.gnd_file, f"{map_name}.gnd"]
        if "_" in map_name:
            candidates.append(map_name.rsplit("_", 1)[0] + ".gnd")  # pay_dun00_a -> pay_dun00
        if map_name[-1].isalpha():
            candidates.append(map_name[:-1] + "1.gnd")  # moc_para0a -> moc_para01
        for candidate in candidates:
            if not candidate:
                continue
            data = self.source.try_read("data\\" + candidate)
            if data is not None:
                return data
        raise MapHasNoTerrain(f"{map_name}: no GND in client (tried {candidates})")

    # ---- model templates ------------------------------------------------

    def _load_template(self, model_name: str, stats: BuildStats) -> ModelTemplate | None:
        key = normalize_path(model_name)
        if key in self.template_cache:
            return self.template_cache[key]
        raw = self.source.try_read("data\\model\\" + key)
        template: ModelTemplate | None = None
        if raw is None:
            stats.missing_models.append(model_name)
        else:
            try:
                model = rsm_format.parse(raw)
                template = build_template(model, model_name)
            except Exception as error:  # noqa: BLE001
                stats.model_errors.append(f"{model_name}: {error!r}")
        self.template_cache[key] = template
        return template

    def _instantiate(self, builder: GltfBuilder,
                     template: ModelTemplate, instance: rsw_format.ModelInstance,
                     material_for, mesh_cache: dict, accessor_cache: dict) -> bool:
        """Add one RSW model instance to the scene. Returns True if it
        received animation channels."""
        # Instance transform (korangar Model::get_model_matrix, mirrored).
        px, py, pz = instance.position
        translation = (px, -py, -pz)
        rx, ry, rz = (math.radians(a) for a in instance.rotation)
        rotation_matrix = mu.euler_rotation_matrix_zxy(rx, ry, rz)
        rotation = mu.matrix_to_quat(mu.mirror_z_matrix(rotation_matrix))
        # The RSM1 Y-flip is baked into the template geometry, so the
        # instance scale is used as stored in the RSW for all versions.
        scale = instance.scale

        # Original client (see open-midgard 3dActor::AdvanceFrame): the
        # animation timer advances int(speed * 100/3) ms (minimum 1) per
        # ~33ms tick, and animation_type 0 disables animation entirely.
        animate = instance.animation_type != 0 and not template.is_static
        speed = instance.animation_speed if instance.animation_speed and instance.animation_speed > 0 else 1.0
        effective_speed = max(speed, 0.03)

        animation: dict | None = None
        if animate:
            animation = builder.add_animation(instance.name or instance.model_name)

        node_ids: list[int] = []
        children_of: dict[int, list[int]] = defaultdict(list)

        for index, node in enumerate(template.nodes):
            mesh_index = self._mesh_for(builder, template, index, material_for, mesh_cache)
            node_id = builder.add_node(
                name=f"{instance.name or template.name}#{node.name}",
                mesh=mesh_index,
                translation=node.translation,
                rotation=node.rotation,
                scale=node.scale,
            )
            node_ids.append(node_id)
            if node.parent >= 0:
                children_of[node.parent].append(node_id)

            if animation is None:
                continue
            for channel in node.channels:
                cache_key = (id(channel), effective_speed)
                cached = accessor_cache.get(cache_key)
                if cached is None:
                    times = channel.times / effective_speed if effective_speed != 1.0 else channel.times
                    input_accessor = builder.add_accessor(
                        times.astype(np.float32), "SCALAR", FLOAT, minmax=True)
                    value_type = "VEC3" if channel.path in ("translation", "scale") else "VEC4"
                    output_accessor = builder.add_accessor(
                        channel.values.astype(np.float32), value_type, FLOAT)
                    cached = (input_accessor, output_accessor)
                    accessor_cache[cache_key] = cached
                sampler_index = len(animation["samplers"])
                animation["samplers"].append({
                    "input": cached[0], "output": cached[1], "interpolation": "LINEAR",
                })
                animation["channels"].append({
                    "sampler": sampler_index,
                    "target": {"node": node_id, "path": channel.path},
                })

        if animation is not None and not animation["channels"]:
            builder.json["animations"].remove(animation)
            animation = None

        # Wire up hierarchy (children lists must be set after creation).
        for parent_index, child_ids in children_of.items():
            node = builder.json["nodes"][node_ids[parent_index]]
            node.setdefault("children", []).extend(child_ids)

        roots = [node_ids[i] for i, node in enumerate(template.nodes) if node.parent < 0]
        instance_node = builder.add_node(
            name=instance.model_name,
            translation=translation,
            rotation=rotation,
            scale=scale,
            children=roots,
        )
        builder.add_scene_node(instance_node)
        return animation is not None

    def _mesh_for(self, builder: GltfBuilder, template: ModelTemplate, node_index: int,
                  material_for, mesh_cache: dict, flip_winding: bool = False) -> int | None:
        key = (template.name, node_index, flip_winding)
        if key in mesh_cache:
            return mesh_cache[key]
        node = template.nodes[node_index]
        primitives = []
        for primitive in node.primitives:
            if primitive.indices.size == 0:
                continue
            normals = primitive.normals
            indices = primitive.indices
            if flip_winding:
                # Mirrored placements (negative-determinant RSW scale)
                # flip the rasterized winding; pre-reverse it so faces
                # stay front-facing. The normals must NOT be negated:
                # the renderer's normal matrix (inverse-transpose of the
                # mirrored instance transform) already mirrors them.
                indices = np.ascontiguousarray(indices.reshape(-1, 3)[:, ::-1]).ravel()
            attributes = {
                "POSITION": builder.add_accessor(primitive.positions, "VEC3", FLOAT,
                                                 ARRAY_BUFFER, minmax=True),
                "NORMAL": builder.add_accessor(normals, "VEC3", FLOAT, ARRAY_BUFFER),
                "TEXCOORD_0": builder.add_accessor(primitive.uvs, "VEC2", FLOAT, ARRAY_BUFFER),
            }
            primitives.append({
                "attributes": attributes,
                "indices": builder.add_accessor(indices, "SCALAR", UNSIGNED_INT,
                                                ELEMENT_ARRAY_BUFFER),
                "material": material_for(primitive.texture),
            })
        mesh_index = builder.add_mesh(primitives, name=f"{template.name}/{node.name}") if primitives else None
        mesh_cache[key] = mesh_index
        return mesh_index

    # ---- terrain ----------------------------------------------------------

    def _build_terrain(self, builder: GltfBuilder, gnd: gnd_format.Gnd, material_for) -> int:
        width, height = gnd.width, gnd.height
        cubes = gnd.cubes
        surfaces = gnd.surfaces
        offset_x = width * 5.0
        offset_z = height * 5.0

        # Texture sizes for the half-pixel UV inset.
        texture_sizes: list[tuple[int, int]] = []
        for name in gnd.textures:
            texture = self._load_texture(name)
            texture_sizes.append((texture.width, texture.height) if texture else (64, 64))

        # Per texture: positions, normals(placeholder), uvs, colors, indices
        buckets: dict[int, dict] = {}

        def top_color(tile_x: int, tile_y: int, fallback) -> tuple[int, int, int, int]:
            if 0 <= tile_x < width and 0 <= tile_y < height:
                neighbor = cubes[tile_x + tile_y * width]
                if 0 <= neighbor.top_surface < len(surfaces):
                    return surfaces[neighbor.top_surface].color_rgba
            return fallback

        # Geometry assembly. Corner order per surface: SW, SE, NW, NE
        # (matches the GND uv storage order).
        for index, cube in enumerate(cubes):
            tile_x = index % width
            tile_y = index // width

            for kind in ("top", "north", "east"):
                if kind == "top":
                    surface_index = cube.top_surface
                elif kind == "north":
                    surface_index = cube.north_surface
                else:
                    surface_index = cube.east_surface
                if surface_index < 0 or surface_index >= len(surfaces):
                    continue
                surface = surfaces[surface_index]

                if kind == "top":
                    corners = (
                        (tile_x, cube.h_sw, tile_y),
                        (tile_x + 1, cube.h_se, tile_y),
                        (tile_x, cube.h_nw, tile_y + 1),
                        (tile_x + 1, cube.h_ne, tile_y + 1),
                    )
                elif kind == "north":
                    neighbor_index = tile_x + (tile_y + 1) * width
                    if tile_y + 1 >= height:
                        continue
                    neighbor = cubes[neighbor_index]
                    corners = (
                        (tile_x, cube.h_nw, tile_y + 1),
                        (tile_x + 1, cube.h_ne, tile_y + 1),
                        (tile_x, neighbor.h_sw, tile_y + 1),
                        (tile_x + 1, neighbor.h_se, tile_y + 1),
                    )
                else:  # east
                    if tile_x + 1 >= width:
                        continue
                    neighbor = cubes[tile_x + 1 + tile_y * width]
                    corners = (
                        (tile_x + 1, cube.h_ne, tile_y + 1),
                        (tile_x + 1, cube.h_se, tile_y),
                        (tile_x + 1, neighbor.h_nw, tile_y + 1),
                        (tile_x + 1, neighbor.h_sw, tile_y),
                    )

                positions = [
                    (cx * 10.0 - offset_x, -cy, -(cz * 10.0 - offset_z))
                    for cx, cy, cz in corners
                ]

                texture_width, texture_height = texture_sizes[surface.texture_index] \
                    if 0 <= surface.texture_index < len(texture_sizes) else (64, 64)
                half_u = 0.5 / texture_width
                half_v = 0.5 / texture_height
                uvs = [
                    (half_u + surface.u[i] * (1.0 - 2.0 * half_u),
                     half_v + surface.v[i] * (1.0 - 2.0 * half_v))
                    for i in range(4)
                ]

                base_color = surface.color_rgba
                colors = tuple(
                    (c[0], c[1], c[2], 255)  # force opaque; the client ignores alpha
                    for c in (
                        base_color,
                        top_color(tile_x + 1, tile_y, base_color),
                        top_color(tile_x, tile_y + 1, base_color),
                        top_color(tile_x + 1, tile_y + 1, base_color),
                    )
                )

                bucket = buckets.setdefault(surface.texture_index, {
                    "positions": [], "uvs": [], "colors": [], "indices": [],
                })
                base = len(bucket["positions"])
                bucket["positions"].extend(positions)
                bucket["uvs"].extend(uvs)
                bucket["colors"].extend(colors)
                # Two triangles: (SW, SE, NW) and (NW, SE, NE)
                bucket["indices"].extend((base, base + 1, base + 2,
                                          base + 2, base + 1, base + 3))

        # Normals: face normals, then average per shared position except on
        # vertical (wall) edges, mirroring korangar's smooth_ground_normals.
        primitives = []
        for texture_index, bucket in sorted(buckets.items()):
            positions = np.asarray(bucket["positions"], dtype=np.float32)
            indices = np.asarray(bucket["indices"], dtype=np.uint32)
            uvs = np.asarray(bucket["uvs"], dtype=np.float32)
            colors = np.asarray(bucket["colors"], dtype=np.uint8)

            normals = _smoothed_normals(positions, indices)

            attributes = {
                "POSITION": builder.add_accessor(positions, "VEC3", FLOAT, ARRAY_BUFFER, minmax=True),
                "NORMAL": builder.add_accessor(normals, "VEC3", FLOAT, ARRAY_BUFFER),
                "TEXCOORD_0": builder.add_accessor(uvs, "VEC2", FLOAT, ARRAY_BUFFER),
                "COLOR_0": builder.add_accessor(colors, "VEC4", UNSIGNED_BYTE, ARRAY_BUFFER),
            }
            texture_name = gnd.textures[texture_index] if 0 <= texture_index < len(gnd.textures) else ""
            primitives.append({
                "attributes": attributes,
                "indices": builder.add_accessor(indices, "SCALAR", UNSIGNED_INT, ELEMENT_ARRAY_BUFFER),
                "material": material_for(texture_name),
            })

        mesh = builder.add_mesh(primitives, name="terrain")
        return builder.add_node(name="terrain", mesh=mesh)

    # ---- water -------------------------------------------------------------

    def _build_water(self, builder: GltfBuilder, gnd: gnd_format.Gnd,
                     rsw: rsw_format.Rsw, sampler: int, stats: BuildStats,
                     add_image) -> int | None:
        # Water settings come from the RSW (< 2.6) or the GND (>= 1.8).
        planes: list[gnd_format.WaterPlane] = []
        grid = (1, 1)
        if gnd.water_planes:
            planes = gnd.water_planes
            grid = gnd.water_grid
        elif rsw.water is not None:
            water = rsw.water
            planes = [gnd_format.WaterPlane(water.level, water.water_type, water.wave_height,
                                            water.wave_speed, water.wave_pitch,
                                            water.texture_cycling_interval)]

        if not planes:
            return None

        width, height = gnd.width, gnd.height
        offset_x = width * 5.0
        offset_z = height * 5.0
        grid_u, grid_v = max(grid[0], 1), max(grid[1], 1)
        cell_width = width / grid_u
        cell_height = height / grid_v

        positions: list[tuple[float, float, float]] = []
        uvs: list[tuple[float, float]] = []
        indices: list[int] = []
        used_types: set[int] = set()

        for plane_index, plane in enumerate(planes):
            cell_u = plane_index % grid_u
            cell_v = plane_index // grid_u
            level = -plane.level
            wave = plane.wave_height
            used_types.add(plane.water_type)
            repeat = 16.0 if plane.water_type in (4, 6) else 4.0

            x_start = int(cell_u * cell_width)
            x_end = int((cell_u + 1) * cell_width) if grid_u > 1 else width
            y_start = int(cell_v * cell_height)
            y_end = int((cell_v + 1) * cell_height) if grid_v > 1 else height

            for tile_y in range(y_start, y_end):
                for tile_x in range(x_start, x_end):
                    cube = gnd.cubes[tile_x + tile_y * width]
                    lowest = min(-cube.h_sw, -cube.h_se, -cube.h_nw, -cube.h_ne)
                    if lowest >= level + wave:
                        continue
                    base = len(positions)
                    for dy in (0, 1):
                        for dx in (0, 1):
                            positions.append((
                                (tile_x + dx) * 10.0 - offset_x,
                                level,
                                -((tile_y + dy) * 10.0 - offset_z),
                            ))
                            uvs.append(((tile_x + dx) / repeat, (tile_y + dy) / repeat))
                    indices.extend((base, base + 1, base + 2, base + 1, base + 3, base + 2))

        if not indices:
            return None

        water_type = sorted(used_types)[0] if used_types else 0
        texture_name = f"워터\\water{water_type}00.jpg"
        texture = self._load_texture(texture_name)
        material: dict = {
            "name": "water",
            "pbrMetallicRoughness": {
                "metallicFactor": 0.0,
                "roughnessFactor": 0.2,
            },
            "doubleSided": True,
            "alphaMode": "BLEND",
        }
        opacity = 1.0 if water_type in (4, 6) else WATER_OPACITY
        if texture is not None:
            image = add_image(texture_name, texture)
            texture_index = builder.add_texture(image, sampler)
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": texture_index}
            material["pbrMetallicRoughness"]["baseColorFactor"] = [1.0, 1.0, 1.0, opacity]
        else:
            stats.missing_textures.append(texture_name)
            material["pbrMetallicRoughness"]["baseColorFactor"] = [0.2, 0.4, 0.8, opacity]
        material_index = builder.add_material(material)

        positions_array = np.asarray(positions, dtype=np.float32)
        normals = np.zeros_like(positions_array)
        normals[:, 1] = 1.0
        primitive = {
            "attributes": {
                "POSITION": builder.add_accessor(positions_array, "VEC3", FLOAT, ARRAY_BUFFER, minmax=True),
                "NORMAL": builder.add_accessor(normals, "VEC3", FLOAT, ARRAY_BUFFER),
                "TEXCOORD_0": builder.add_accessor(np.asarray(uvs, dtype=np.float32), "VEC2", FLOAT, ARRAY_BUFFER),
            },
            "indices": builder.add_accessor(np.asarray(indices, dtype=np.uint32), "SCALAR",
                                            UNSIGNED_INT, ELEMENT_ARRAY_BUFFER),
            "material": material_index,
        }
        mesh = builder.add_mesh([primitive], name="water")
        return builder.add_node(name="water", mesh=mesh)

    # ---- light ---------------------------------------------------------------

    def _build_sun(self, builder: GltfBuilder, rsw: rsw_format.Rsw) -> int:
        light = rsw.light
        # korangar: direction-to-sun = Ry(longitude) * Rx(-latitude) * +Y
        to_sun = mu.transform_direction(
            mu.mat_mul(mu.rot_y(math.radians(light.longitude)),
                       mu.rot_x(math.radians(-light.latitude))),
            (0.0, 1.0, 0.0),
        )
        travel = mu.normalize(mu.mirror_z_point((-to_sun[0], -to_sun[1], -to_sun[2])))

        # Rotate glTF light default direction (0,0,-1) onto `travel`.
        default = (0.0, 0.0, -1.0)
        dot = max(-1.0, min(1.0, default[0] * travel[0] + default[1] * travel[1] + default[2] * travel[2]))
        if dot > 0.99999:
            rotation = (0.0, 0.0, 0.0, 1.0)
        elif dot < -0.99999:
            rotation = (1.0, 0.0, 0.0, 0.0)  # 180 degrees around X
        else:
            axis = mu.normalize(mu.cross(default, travel))
            angle = math.acos(dot)
            rotation = mu.axis_angle_quat(axis, angle)

        color = tuple(min(max(c, 0.0), 1.0) for c in light.diffuse)
        light_index = builder.add_light(color, intensity=4.0)
        return builder.add_node(
            name="sun",
            rotation=rotation,
            translation=(0.0, 500.0, 0.0),
            extensions={"KHR_lights_punctual": {"light": light_index}},
        )


def _smoothed_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Face normals averaged per position, except for vertices that sit on
    vertical edges (walls), which keep their face normal."""
    tri = indices.reshape(-1, 3)
    p0 = positions[tri[:, 0]]
    p1 = positions[tri[:, 1]]
    p2 = positions[tri[:, 2]]
    face_normals = np.cross(p1 - p0, p2 - p0)
    lengths = np.linalg.norm(face_normals, axis=1, keepdims=True)
    lengths[lengths < 1e-9] = 1.0
    face_normals /= lengths

    vertex_normals = np.zeros_like(positions)
    counts = np.zeros(len(positions), dtype=np.int32)
    for column in range(3):
        np.add.at(vertex_normals, tri[:, column], face_normals)
        np.add.at(counts, tri[:, column], 1)

    # Identify wall vertices: any triangle edge with matching x and z.
    artificial = np.zeros(len(positions), dtype=bool)
    for a_column, b_column in ((0, 1), (1, 2), (2, 0)):
        a = tri[:, a_column]
        b = tri[:, b_column]
        same_xz = (np.abs(positions[a][:, 0] - positions[b][:, 0]) < 1e-6) & \
                  (np.abs(positions[a][:, 2] - positions[b][:, 2]) < 1e-6)
        artificial[a[same_xz]] = True
        artificial[b[same_xz]] = True

    # Group by exact position for smoothing.
    keys = positions.round(4)
    view = np.ascontiguousarray(keys).view([("x", "f4"), ("y", "f4"), ("z", "f4")]).ravel()
    order = np.argsort(view, order=("x", "y", "z"))
    sorted_view = view[order]
    group_starts = np.ones(len(order), dtype=bool)
    group_starts[1:] = sorted_view[1:] != sorted_view[:-1]
    group_ids = np.cumsum(group_starts) - 1
    inverse = np.empty(len(order), dtype=np.int64)
    inverse[order] = group_ids

    group_count = group_ids[-1] + 1 if len(group_ids) else 0
    group_normals = np.zeros((group_count, 3), dtype=np.float64)
    smooth_mask = ~artificial
    np.add.at(group_normals, inverse[smooth_mask], vertex_normals[smooth_mask])

    result = np.where(smooth_mask[:, None], group_normals[inverse], vertex_normals)
    lengths = np.linalg.norm(result, axis=1, keepdims=True)
    lengths[lengths < 1e-9] = 1.0
    result = (result / lengths).astype(np.float32)
    return result
