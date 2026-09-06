"""Legacy RSM face flags must not hide open scenery in exported models."""
import unittest
from types import SimpleNamespace

import numpy as np

from ragx import mathutil as mu
from ragx.formats.rsm import Face
from ragx.model_builder import _bake_mesh


class ModelVisibilityTests(unittest.TestCase):
    def test_unflagged_planes_and_mixed_materials_remain_double_sided(self):
        for flip_y in (False, True):
            for flags in ((0,), (1,), (0, 1)):
                with self.subTest(flip_y=flip_y, flags=flags):
                    node = SimpleNamespace(
                        vertices=[(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)],
                        uvs=[(0., 0.), (1., 0.), (0., 1.)],
                        faces=[Face((0, 1, 2), (0, 1, 2), 0, flag, (0,))
                               for flag in flags])
                    primitives = _bake_mesh(node, mu.IDENTITY, ['leaf.bmp'],
                                            False, flip_y=flip_y)
                    self.assertEqual(len(primitives), 1)
                    primitive = primitives[0]
                    self.assertTrue(primitive.double_sided)
                    self.assertEqual(len(primitive.indices), 3 * len(flags))
                    for triangle in primitive.indices.reshape(-1, 3):
                        p0, p1, p2 = primitive.positions[triangle]
                        normal = np.cross(p1 - p0, p2 - p0)
                        normal /= np.linalg.norm(normal)
                        np.testing.assert_allclose(primitive.normals[triangle],
                                                   np.tile(normal, (3, 1)))
                    np.testing.assert_allclose(primitive.normals[0], [0, 0, -1])


if __name__ == '__main__':
    unittest.main()
