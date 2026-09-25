"""Native UI atlas dependencies preserve source slices and pixels."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ragx.exporters.godot.compact_ui import compile_ui
from ragx.exporters.godot.compact_images import compile_images


class CompactUiTest(unittest.TestCase):
    def test_native_region_preserves_style_margins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            Image.new('RGBA', (12, 10), (5, 70, 100, 200)).save(source / 'panel.png')
            style = ('[gd_resource type="StyleBoxTexture" load_steps=2 format=3]\n'
                     '[ext_resource type="Texture2D" path="res://ui/skin/panel.png" id="1"]\n'
                     '[resource]\ntexture = ExtResource("1")\ntexture_margin_left = 3.0\n'
                     'content_margin_top = 4.0\naxis_stretch_vertical = 1\n')
            (source / 'panel.tres').write_text(style)
            output = root / 'output'
            compile_ui(source, output)
            self.assertEqual((output / 'panel.tres').read_text(),
                             style.replace('panel.png', 'panel.texture.tres'))
            resource = (output / 'panel.texture.tres').read_text()
            self.assertIn('region = Rect2(1, 1, 12, 10)', resource)
            self.assertIn('filter_clip = true', resource)
            self.assertIn('importer="texture"', (output / 'pages/00000.png.import').read_text())

    def test_image_catalog_retains_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            for name in ('a', 'b'):
                Image.new('RGBA', (2, 3), (1, 2, 3, 4)).save(source / (name + '.png'))
            output = root / 'output'
            result = compile_images(source, output)
            self.assertEqual(result['unique'], 1)
            images = json.loads((output / 'library.json').read_text())['images']
            self.assertEqual(images['a.png'], images['b.png'])
            self.assertIn('importer="image"', (output / 'pages/00000.png.import').read_text())


if __name__ == '__main__':
    unittest.main()
