from __future__ import annotations

import math
import json
import unittest

import numpy as np

from ragx import mathutil
from ragx.formats.gnd import Cube, Gnd
from ragx.gltf import GltfBuilder
from ragx.formats.rsm import Face, Node
from ragx.map_builder import AssetSource, MapBuilder, _smoothed_normals, _terrain_bucket_normals
from ragx.model_builder import _bake_mesh, _smooth_model_normals


class TerrainNormalTests(unittest.TestCase):
    def test_surface_less_terrain_is_a_valid_empty_gltf_scene(self) -> None:
        gnd = Gnd(
            version=(1, 7), width=1, height=1, zoom=10.0,
            textures=[], surfaces=[],
            cubes=[Cube(0.0, 0.0, 0.0, 0.0, -1, -1, -1)],
        )
        builder = GltfBuilder()
        terrain = MapBuilder(None)._build_terrain(builder, gnd, lambda _name: 0)
        builder.add_scene_node(terrain)

        document_bytes, binary = builder.to_gltf("terrain.bin")
        document = json.loads(document_bytes)

        self.assertEqual(binary, b"")
        self.assertNotIn("buffers", document)
        self.assertNotIn("meshes", document)
        self.assertEqual(document["nodes"], [{"name": "terrain"}])

    def test_smoothing_crosses_texture_bucket_boundaries(self) -> None:
        buckets = {
            0: {
                "positions": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                              (0.0, 0.0, -1.0)],
                "indices": [0, 1, 2],
            },
            1: {
                "positions": [(0.0, 0.0, 0.0), (0.0, 1.0, -1.0),
                              (1.0, 0.0, 0.0)],
                "indices": [0, 1, 2],
            },
        }

        normals = _terrain_bucket_normals(buckets)

        np.testing.assert_allclose(normals[0][0], normals[1][0], atol=1e-6)
        self.assertAlmostEqual(float(np.linalg.norm(normals[0][0])), 1.0, places=6)

    def test_wall_vertex_receives_existing_ground_normal(self) -> None:
        positions = np.asarray([
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0),
            (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0),
        ], dtype=np.float32)
        indices = np.asarray([0, 1, 2, 3, 4, 5], dtype=np.uint32)

        normals = _smoothed_normals(positions, indices)

        np.testing.assert_allclose(normals[3], normals[0], atol=1e-6)


class ModelNormalTests(unittest.TestCase):
    def test_smoothing_uses_position_and_all_groups(self) -> None:
        positions = [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
        ]
        faces = [
            Face((0, 1, 2), (0, 1, 2), 0, 0, (0, 1)),
            Face((3, 4, 5), (0, 1, 2), 0, 0, (0,)),
            Face((6, 7, 8), (0, 1, 2), 0, 0, (1,)),
        ]
        face_normals = [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0)]

        normals = _smooth_model_normals(faces, face_normals, positions)

        expected = np.asarray((1.0, 1.0, 2.0)) / math.sqrt(6.0)
        np.testing.assert_allclose(normals[0][0], expected, atol=1e-6)
        np.testing.assert_allclose(
            normals[1][0], np.asarray((1.0, 0.0, 1.0)) / math.sqrt(2.0),
            atol=1e-6)
        np.testing.assert_allclose(
            normals[2][0], np.asarray((0.0, 1.0, 1.0)) / math.sqrt(2.0),
            atol=1e-6)

    def test_world_scale_changes_positions_but_not_normals(self) -> None:
        node = Node(
            name="triangle",
            parent_name="",
            texture_indices=[0],
            texture_names=[],
            offset_matrix=(1.0, 0.0, 0.0,
                           0.0, 1.0, 0.0,
                           0.0, 0.0, 1.0),
            translation1=(0.0, 0.0, 0.0),
            translation2=(0.0, 0.0, 0.0),
            rotation_angle=0.0,
            rotation_axis=(0.0, 1.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            vertices=[(0.0, 0.0, 0.0), (5.0, 0.0, 0.0),
                      (0.0, 5.0, 0.0)],
            uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            uv_colors=[],
            faces=[Face((0, 1, 2), (0, 1, 2), 0, 0, (0,))],
            scale_keyframes=[],
            rotation_keyframes=[],
            translation_keyframes=[],
        )

        native = _bake_mesh(node, mathutil.IDENTITY, ["test.bmp"], False)[0]
        scaled = _bake_mesh(node, mathutil.IDENTITY, ["test.bmp"], False,
                            world_scale=0.2)[0]

        np.testing.assert_allclose(scaled.positions, native.positions * 0.2,
                                   atol=1e-6)
        np.testing.assert_allclose(scaled.normals, native.normals, atol=1e-6)


class WorldScaleTests(unittest.TestCase):
    def test_map_builder_rejects_invalid_scale(self) -> None:
        source = AssetSource(object())
        for scale in (0.0, -1.0, float("inf"), float("nan")):
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError):
                    MapBuilder(source, world_scale=scale)


if __name__ == "__main__":
    unittest.main()
