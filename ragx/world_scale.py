"""Bake a positive unit conversion into glTF data, preserving node scales.

Scaling positions and local translations together scales an entire hierarchy,
including animated translations, without an engine-side root scale. Normals,
UVs, rotations, animation times and dimensionless node scales stay unchanged.
"""
from __future__ import annotations

import math
import numpy as np


def bake_world_scale(builder, factor: float) -> None:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("world_scale must be finite and positive")
    if factor == 1.0:
        return
    document = builder.json
    accessors = set()
    for mesh in document.get("meshes", []):
        for primitive in mesh["primitives"]:
            accessors.add(primitive["attributes"]["POSITION"])
            for target in primitive.get("targets", []):
                if "POSITION" in target:
                    accessors.add(target["POSITION"])
    for animation in document.get("animations", []):
        for channel in animation["channels"]:
            if channel["target"]["path"] == "translation":
                accessors.add(animation["samplers"][channel["sampler"]]["output"])
    for index in accessors:
        accessor = document["accessors"][index]
        view = document["bufferViews"][accessor["bufferView"]]
        if accessor["componentType"] != 5126 or accessor["type"] != "VEC3":
            raise ValueError("world-space vectors must be float VEC3 accessors")
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        values = np.ndarray((accessor["count"], 3), dtype="<f4",
                            buffer=builder.binary, offset=offset,
                            strides=(view.get("byteStride", 12), 4))
        values *= factor
        for bound in ("min", "max"):
            if bound in accessor:
                accessor[bound] = [float(value) * factor for value in accessor[bound]]
    for node in document.get("nodes", []):
        if "translation" in node:
            node["translation"] = [value * factor for value in node["translation"]]
        if "matrix" in node:
            matrix = list(node["matrix"])
            matrix[12:15] = [value * factor for value in matrix[12:15]]
            node["matrix"] = matrix
    for light in document.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", []):
        if "range" in light:
            light["range"] *= factor
