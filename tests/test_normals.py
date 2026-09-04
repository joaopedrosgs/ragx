from __future__ import annotations

import math
import unittest

import numpy as np

from ragx.formats.rsm import Face
from ragx.map_builder import _smoothed_normals, _terrain_bucket_normals
from ragx.model_builder import _smooth_model_normals


class TerrainNormalTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
