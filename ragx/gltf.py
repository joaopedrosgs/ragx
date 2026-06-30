"""Minimal glTF 2.0 / GLB writer.

Builds a single-buffer binary glTF with embedded PNG textures. Only the
features the converter needs are implemented: meshes with POSITION /
NORMAL / TEXCOORD_0 / COLOR_0, node hierarchies (TRS or matrix),
materials, samplers, animations and KHR_lights_punctual.
"""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

FLOAT = 5126
UNSIGNED_INT = 5125
UNSIGNED_SHORT = 5123
UNSIGNED_BYTE = 5121

ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963

_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GltfBuilder:
    def __init__(self, generator: str = "ragx"):
        self.binary = bytearray()
        self.json: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "accessors": [],
            "bufferViews": [],
            "samplers": [],
            "textures": [],
            "images": [],
            "animations": [],
        }
        self._extensions_used: set[str] = set()

    # ---- low-level -------------------------------------------------

    def _align(self, alignment: int = 4) -> None:
        while len(self.binary) % alignment:
            self.binary.append(0)

    def add_buffer_view(self, data: bytes, target: int | None = None) -> int:
        self._align()
        offset = len(self.binary)
        self.binary.extend(data)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.json["bufferViews"].append(view)
        return len(self.json["bufferViews"]) - 1

    def add_accessor(self, array: np.ndarray, accessor_type: str,
                     component_type: int, target: int | None = None,
                     minmax: bool = False) -> int:
        components = _TYPE_COMPONENTS[accessor_type]
        flat = np.ascontiguousarray(array)
        count = flat.size // components
        view = self.add_buffer_view(flat.tobytes(), target)
        accessor: dict[str, Any] = {
            "bufferView": view,
            "componentType": component_type,
            "count": count,
            "type": accessor_type,
        }
        if component_type == UNSIGNED_BYTE and accessor_type != "SCALAR":
            accessor["normalized"] = True
        if minmax:
            reshaped = flat.reshape(count, components)
            accessor["min"] = [float(v) for v in reshaped.min(axis=0)]
            accessor["max"] = [float(v) for v in reshaped.max(axis=0)]
        self.json["accessors"].append(accessor)
        return len(self.json["accessors"]) - 1

    # ---- images / materials -----------------------------------------

    def add_sampler(self, wrap: bool = True) -> int:
        sampler = {
            "magFilter": 9729,  # LINEAR
            "minFilter": 9987,  # LINEAR_MIPMAP_LINEAR
            "wrapS": 10497 if wrap else 33071,
            "wrapT": 10497 if wrap else 33071,
        }
        samplers = self.json["samplers"]
        if sampler in samplers:
            return samplers.index(sampler)
        samplers.append(sampler)
        return len(samplers) - 1

    def add_image_png(self, png: bytes, name: str | None = None) -> int:
        view = self.add_buffer_view(png)
        image: dict[str, Any] = {"bufferView": view, "mimeType": "image/png"}
        if name:
            image["name"] = name
        self.json["images"].append(image)
        return len(self.json["images"]) - 1

    def add_image_uri(self, uri: str, name: str | None = None) -> int:
        """Reference an external image file (used for .gltf output where
        textures are shared between maps)."""
        image: dict[str, Any] = {"uri": uri, "mimeType": "image/png"}
        if name:
            image["name"] = name
        self.json["images"].append(image)
        return len(self.json["images"]) - 1

    def add_texture(self, image_index: int, sampler_index: int) -> int:
        self.json["textures"].append({"sampler": sampler_index, "source": image_index})
        return len(self.json["textures"]) - 1

    def add_material(self, material: dict[str, Any]) -> int:
        self.json["materials"].append(material)
        return len(self.json["materials"]) - 1

    # ---- scene graph -------------------------------------------------

    def add_mesh(self, primitives: list[dict[str, Any]], name: str | None = None) -> int:
        mesh: dict[str, Any] = {"primitives": primitives}
        if name:
            mesh["name"] = name
        self.json["meshes"].append(mesh)
        return len(self.json["meshes"]) - 1

    def add_node(self, name: str | None = None, mesh: int | None = None,
                 translation=None, rotation=None, scale=None, matrix=None,
                 children: list[int] | None = None,
                 extensions: dict[str, Any] | None = None) -> int:
        node: dict[str, Any] = {}
        if name:
            node["name"] = name
        if mesh is not None:
            node["mesh"] = mesh
        if matrix is not None:
            # glTF matrices are column-major; ours are row-major.
            m = matrix
            node["matrix"] = [
                m[0], m[4], m[8], m[12],
                m[1], m[5], m[9], m[13],
                m[2], m[6], m[10], m[14],
                m[3], m[7], m[11], m[15],
            ]
        else:
            if translation is not None and tuple(translation) != (0.0, 0.0, 0.0):
                node["translation"] = list(translation)
            if rotation is not None and tuple(rotation) != (0.0, 0.0, 0.0, 1.0):
                node["rotation"] = list(rotation)
            if scale is not None and tuple(scale) != (1.0, 1.0, 1.0):
                node["scale"] = list(scale)
        if children:
            node["children"] = children
        if extensions:
            node["extensions"] = extensions
        self.json["nodes"].append(node)
        return len(self.json["nodes"]) - 1

    def add_scene_node(self, node_index: int) -> None:
        self.json["scenes"][0]["nodes"].append(node_index)

    # ---- animation -----------------------------------------------------

    def add_animation(self, name: str) -> dict[str, Any]:
        animation = {"name": name, "samplers": [], "channels": []}
        self.json["animations"].append(animation)
        return animation

    def add_animation_channel(self, animation: dict[str, Any], node: int, path: str,
                              times: np.ndarray, values: np.ndarray,
                              interpolation: str = "LINEAR") -> None:
        input_accessor = self.add_accessor(times.astype(np.float32), "SCALAR", FLOAT, minmax=True)
        value_type = "VEC3" if path in ("translation", "scale") else "VEC4"
        output_accessor = self.add_accessor(values.astype(np.float32), value_type, FLOAT)
        sampler_index = len(animation["samplers"])
        animation["samplers"].append({
            "input": input_accessor,
            "output": output_accessor,
            "interpolation": interpolation,
        })
        animation["channels"].append({
            "sampler": sampler_index,
            "target": {"node": node, "path": path},
        })

    # ---- lights (KHR_lights_punctual) ----------------------------------

    def add_light(self, color: tuple[float, float, float], intensity: float,
                  light_type: str = "directional") -> int:
        self._extensions_used.add("KHR_lights_punctual")
        extensions = self.json.setdefault("extensions", {})
        lights = extensions.setdefault("KHR_lights_punctual", {}).setdefault("lights", [])
        lights.append({"type": light_type, "color": list(color), "intensity": intensity})
        return len(lights) - 1

    # ---- output ---------------------------------------------------------

    def _document(self) -> dict[str, Any]:
        if self._extensions_used:
            self.json["extensionsUsed"] = sorted(self._extensions_used)
        # Drop empty top-level arrays (animations etc.) to keep validators happy.
        return {k: v for k, v in self.json.items() if v != [] or k in ("scenes", "nodes")}

    def to_glb(self) -> bytes:
        document = self._document()
        self._align()
        bin_bytes = bytes(self.binary)
        document["buffers"] = [{"byteLength": len(bin_bytes)}]

        json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)

        total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
        out = bytearray()
        out += struct.pack("<III", GLB_MAGIC, 2, total)
        out += struct.pack("<II", len(json_bytes), CHUNK_JSON)
        out += json_bytes
        out += struct.pack("<II", len(bin_bytes), CHUNK_BIN)
        out += bin_bytes
        return bytes(out)

    def to_gltf(self, bin_uri: str) -> tuple[bytes, bytes]:
        """Return (json_bytes, bin_bytes) for text glTF with an external
        buffer file. Image URIs must have been added via add_image_uri."""
        document = self._document()
        self._align()
        bin_bytes = bytes(self.binary)
        document["buffers"] = [{"uri": bin_uri, "byteLength": len(bin_bytes)}]
        json_bytes = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
        return json_bytes, bin_bytes
