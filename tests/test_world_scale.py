"""Unit scaling must preserve shared geometry, animation and surface normals."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ragx.gltf import GltfBuilder, FLOAT, ARRAY_BUFFER
from ragx.formats.gnd import Cube, Gnd, Surface
from ragx.map_builder import MapBuilder
from ragx.world_scale import bake_world_scale


class WorldScaleTests(unittest.TestCase):
    def test_geometry_and_animated_hierarchy_share_one_unit_conversion(self):
        builder = GltfBuilder()
        positions = builder.add_accessor(np.array([[5, 10, -5]], dtype=np.float32),
                                         "VEC3", FLOAT, ARRAY_BUFFER, minmax=True)
        normals = builder.add_accessor(np.array([[0, 1, 0]], dtype=np.float32),
                                       "VEC3", FLOAT, ARRAY_BUFFER)
        motion = builder.add_accessor(np.array([[10, 15, 20]], dtype=np.float32),
                                      "VEC3", FLOAT, ARRAY_BUFFER)
        builder.json["meshes"] = [{"primitives": [
            {"attributes": {"POSITION": positions, "NORMAL": normals}},
            {"attributes": {"POSITION": positions, "NORMAL": normals}}]}]
        builder.json["nodes"] = [{"translation": [5, 10, 15], "scale": [2, 3, 4]},
                                  {"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10, 20, 30, 1]}]
        builder.json["animations"] = [{"channels": [{"sampler": 0, "target": {"path": "translation"}}],
                                       "samplers": [{"output": motion}]}]
        bake_world_scale(builder, 0.2)
        def values(index):
            view = builder.json["bufferViews"][builder.json["accessors"][index]["bufferView"]]
            return np.frombuffer(builder.binary, dtype="<f4", count=3, offset=view["byteOffset"])
        np.testing.assert_allclose(values(positions), [1, 2, -1])
        np.testing.assert_allclose(values(normals), [0, 1, 0])
        np.testing.assert_allclose(values(motion), [2, 3, 4])
        self.assertEqual(builder.json["accessors"][positions]["min"], [1, 2, -1])
        self.assertEqual(builder.json["nodes"][0]["translation"], [1, 2, 3])
        self.assertEqual(builder.json["nodes"][0]["scale"], [2, 3, 4])
        self.assertEqual(builder.json["nodes"][1]["matrix"][12:15], [2, 4, 6])

    def test_builder_does_not_scale_completed_geometry_a_second_time(self):
        builder = GltfBuilder()
        position = builder.add_accessor(np.array([[5, 0, 0]], dtype=np.float32),
                                        "VEC3", FLOAT, ARRAY_BUFFER, minmax=True)
        builder.json["meshes"] = [{"primitives": [{"attributes": {"POSITION": position}}]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.gltf"
            MapBuilder(None, world_scale=0.2)._write_output(builder, path, True)
            document = json.loads(path.read_text())
            self.assertEqual(document["accessors"][position]["max"], [5, 0, 0])
            self.assertEqual(document["nodes"], [])

    def test_one_gat_cell_remains_one_metre_after_file_output(self):
        surface = Surface(
            u=(0.0, 1.0, 0.0, 1.0), v=(0.0, 0.0, 1.0, 1.0),
            texture_index=-1, light_map_index=0,
            color_rgba=(255, 255, 255, 255),
        )
        gnd = Gnd(
            version=(1, 7), width=1, height=1, zoom=10.0,
            textures=[], surfaces=[surface],
            cubes=[Cube(0.0, 0.0, 0.0, 0.0, 0, -1, -1)],
        )
        builder = GltfBuilder()
        map_builder = MapBuilder(None, world_scale=0.2)
        terrain = map_builder._build_terrain(builder, gnd, lambda _name: 0)
        builder.add_scene_node(terrain)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terrain.gltf"
            map_builder._write_output(builder, path, True)
            document = json.loads(path.read_text())
            accessor = document["accessors"][0]
            # One GND cube is two GAT cells: 2 metres, centred at the origin.
            self.assertEqual(accessor["min"][0], -1.0)
            self.assertEqual(accessor["max"][0], 1.0)

    def test_default_is_byte_identical_and_invalid_scales_fail(self):
        builder = GltfBuilder()
        original = bytes(builder.binary)
        bake_world_scale(builder, 1.0)
        self.assertEqual(bytes(builder.binary), original)
        for factor in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                MapBuilder(None, world_scale=factor)
