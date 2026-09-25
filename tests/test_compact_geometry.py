"""Lossless geometry compaction and single-file Godot dependencies."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ragx.gltf import GltfBuilder, UNSIGNED_INT, ELEMENT_ARRAY_BUFFER
from ragx.map_builder import MapBuilder, AssetSource
from ragx.exporters.godot.ragx_godot_export import _model_spec


class CompactGeometryTest(unittest.TestCase):
    def test_element_width_preserves_values_and_reserved_restart_index(self):
        for values, width in (([0, 10, 65534], 5123), ([0, 65535, 65536], 5125)):
            builder = GltfBuilder()
            index = builder.add_accessor(np.array(values, dtype=np.uint32),
                                         'SCALAR', UNSIGNED_INT, ELEMENT_ARRAY_BUFFER)
            accessor = builder.json['accessors'][index]
            self.assertEqual(accessor['componentType'], width)
            dtype = '<u2' if width == 5123 else '<u4'
            self.assertEqual(np.frombuffer(builder.binary, dtype=dtype).tolist(), values)

    def test_glb_keeps_shared_image_uri_without_sidecar_buffer(self):
        builder = GltfBuilder()
        builder.add_buffer_view(b'1234')
        builder.json['images'].append({'uri': '../textures/shared.png'})
        converter = MapBuilder(AssetSource(None))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'terrain.glb'
            converter._write_output(builder, target, True)
            self.assertEqual([p.name for p in target.parent.iterdir()], ['terrain.glb'])
            raw = target.read_bytes()
            size, kind = struct.unpack_from('<II', raw, 12)
            self.assertEqual(kind, 0x4e4f534a)
            document = json.loads(raw[20:20 + size])
            self.assertNotIn('uri', document['buffers'][0])
            self.assertEqual(document['images'][0]['uri'], '../textures/shared.png')

    def test_glb_model_variants_recover_original_source(self):
        self.assertEqual(_model_spec('a/b.rsm@s0.030@mirror.glb'),
                         ('a\\b.rsm', .03, True))


if __name__ == '__main__':
    unittest.main()
