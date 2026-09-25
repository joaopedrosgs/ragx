"""Compilation preserves every frame, action, event, pixel and palette index."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ragx.exporters.godot.compact_sprites import compile_sprites


class CompactSpritesTest(unittest.TestCase):
    def test_shared_actions_images_and_palette_pairing_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'out'
            source.mkdir()
            original = Image.new('RGBA', (6, 5), (20, 40, 80, 255))
            original.putpixel((2, 3), (123, 99, 45, 17))
            metadata = {'frames': [[0, 0, 6, 5]], 'indexed_count': 1,
                        'actions': [{'delay': 3.2, 'frames': [{'layers': [[1, -2, 0, 1]],
                                     'anchor': [4, -9], 'event': 0}]}], 'events': ['hit.wav']}
            for name in ('a', 'b', 'c'):
                original.save(source / (name + '.png'))
                (source / (name + '.json')).write_text(json.dumps(metadata))
            indices = Image.new('L', original.size, 19)
            indices.save(source / 'c.idx.png')
            result = compile_sprites(source, output, page_size=16)
            self.assertEqual(result['sprites'], 3)
            self.assertEqual(result['animations'], 1)
            self.assertEqual(result['unique_images'], 2)
            manifest = json.loads((output / 'library.json').read_text())['sprites']
            self.assertEqual(manifest['a'], manifest['b'])
            self.assertNotEqual(manifest['a'][0], manifest['c'][0])
            for name, (sheet_key, action_key) in manifest.items():
                sheet = json.loads((output / 'sheets' / (sheet_key[:2] + '.json')).read_text())[sheet_key]
                actions = json.loads((output / 'animations' / (action_key[:2] + '.json')).read_text())[action_key]
                expanded = [{**a, 'frames': [actions['frame_pool'][i] for i in a['frames']]}
                            for a in actions['actions']]
                self.assertEqual(expanded, metadata['actions'])
                self.assertEqual(actions['events'], metadata['events'])
                self.assertEqual(sheet['frames'], metadata['frames'])
                page, x, y, w, h, indexed = sheet['image']
                with Image.open(output / 'pages' / page) as image:
                    self.assertEqual(image.crop((x, y, x + w, y + h)).tobytes(), original.tobytes())
                self.assertEqual(indexed, name == 'c')
                if indexed:
                    with Image.open(output / 'pages' / page.replace('.png', '.idx.png')) as image:
                        self.assertEqual(image.crop((x, y, x + w, y + h)).tobytes(), indices.tobytes())
            self.assertFalse(list(output.glob('*.sqlite*')))
            with self.assertRaisesRegex(ValueError, 'empty'):
                compile_sprites(source, output)

    def test_refuses_in_place_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'separate'):
                compile_sprites(Path(directory), Path(directory))


if __name__ == '__main__':
    unittest.main()
