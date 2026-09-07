from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from ragx import mathutil as mu
from ragx.formats.rsm import Face
from ragx.gltf import GltfBuilder
from ragx.grf import normalize_path
from ragx.map_builder import AssetSource, BuildStats, MapBuilder
from ragx.model_builder import ModelTemplate, NodeTemplate, _bake_mesh
from ragx.textures import LoadedTexture, convert_texture


class MissingSource:
    def read(self, path: str) -> bytes:
        raise FileNotFoundError(path)

    def namelist(self) -> list[str]:
        return []


def encoded_image(mode: str, pixels, image_format: str) -> bytes:
    image = Image.new(mode, (len(pixels), 1))
    image.putdata(pixels)
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


class TextureConversionTests(unittest.TestCase):
    def test_opaque_texture_is_classified_opaque(self) -> None:
        raw = encoded_image("RGB", [(10, 20, 30)], "BMP")
        texture = convert_texture(raw, "opaque.bmp")
        self.assertFalse(texture.has_alpha)
        self.assertEqual(texture.alpha_mode, "OPAQUE")

    def test_magenta_key_is_classified_as_mask(self) -> None:
        raw = encoded_image("RGB", [(255, 0, 255), (10, 20, 30)], "BMP")
        texture = convert_texture(raw, "cutout.bmp")
        self.assertTrue(texture.has_alpha)
        self.assertEqual(texture.alpha_mode, "MASK")

    def test_fractional_tga_alpha_is_classified_as_blend(self) -> None:
        raw = encoded_image("RGBA", [(10, 20, 30, 128)], "TGA")
        texture = convert_texture(raw, "translucent.tga")
        self.assertTrue(texture.has_alpha)
        self.assertEqual(texture.alpha_mode, "BLEND")


class ModelConversionTests(unittest.TestCase):
    @staticmethod
    def triangle(two_sided: int = 0):
        return SimpleNamespace(
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            faces=[Face((0, 1, 2), (0, 1, 2), 0, two_sided, (0,))],
        )

    def test_rsm1_and_rsm2_winding_produce_outward_normals(self) -> None:
        rsm1 = _bake_mesh(self.triangle(), mu.IDENTITY, ["x.bmp"], False, flip_y=True)[0]
        rsm2 = _bake_mesh(self.triangle(), mu.IDENTITY, ["x.bmp"], False, flip_y=False)[0]
        expected = np.asarray((0.0, 0.0, -1.0), dtype=np.float32)
        np.testing.assert_allclose(rsm1.normals[0], expected)
        np.testing.assert_allclose(rsm2.normals[0], expected)

    def test_legacy_faces_remain_visible_from_both_sides(self) -> None:
        node = self.triangle()
        node.faces.append(Face((0, 1, 2), (0, 1, 2), 0, 1, (0,)))
        primitives = _bake_mesh(node, mu.IDENTITY, ["x.bmp"], False, flip_y=True)
        self.assertEqual(len(primitives), 1)
        self.assertTrue(primitives[0].double_sided)

    def test_negative_instance_scale_requests_mirrored_winding(self) -> None:
        map_builder = MapBuilder(AssetSource(MissingSource()))
        template = ModelTemplate(
            "model.rsm",
            [NodeTemplate("root", -1, (0.0, 0.0, 0.0),
                          (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))],
            True,
            False,
        )
        requested: list[bool] = []

        def capture_mesh(*args, **kwargs):
            requested.append(kwargs["flip_winding"])
            return None

        map_builder._mesh_for = capture_mesh
        instance = SimpleNamespace(
            position=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            scale=(-1.0, 1.0, 1.0),
            animation_type=0,
            animation_speed=1.0,
            name="instance",
            model_name="model.rsm",
        )
        map_builder._instantiate(GltfBuilder(), template, instance, lambda _: 0, {}, {})
        self.assertEqual(requested, [True])


class MaterialTests(unittest.TestCase):
    def test_model_material_preserves_unlit_alpha_and_culling(self) -> None:
        map_builder = MapBuilder(AssetSource(MissingSource()))
        texture_name = "translucent.tga"
        map_builder.texture_cache[normalize_path("data\\texture\\" + texture_name)] = LoadedTexture(
            b"png", 1, 1, True, "BLEND")
        builder = GltfBuilder()
        sampler = builder.add_sampler()
        material_for, _ = map_builder._material_factory(
            builder, sampler, BuildStats(), external_textures=False, uri_base="")

        index = material_for(texture_name, double_sided=False, unlit=True, opacity=100 / 255.0)
        material = builder.json["materials"][index]
        self.assertFalse(material["doubleSided"])
        self.assertEqual(material["alphaMode"], "BLEND")
        self.assertIn("KHR_materials_unlit", material["extensions"])
        self.assertAlmostEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"][3], 100 / 255.0)
        builder.to_glb()
        self.assertIn("KHR_materials_unlit", builder.json["extensionsUsed"])

    def test_cutout_texture_remains_masked(self) -> None:
        map_builder = MapBuilder(AssetSource(MissingSource()))
        texture_name = "cutout.bmp"
        map_builder.texture_cache[normalize_path("data\\texture\\" + texture_name)] = LoadedTexture(
            b"png", 1, 1, True, "MASK")
        builder = GltfBuilder()
        material_for, _ = map_builder._material_factory(
            builder, builder.add_sampler(), BuildStats(), external_textures=False, uri_base="")
        material = builder.json["materials"][material_for(texture_name)]
        self.assertEqual(material["alphaMode"], "MASK")
        self.assertEqual(material["alphaCutoff"], 0.5)

    def test_changed_shared_texture_replaces_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            texture_dir = Path(directory) / "textures"
            texture_dir.mkdir()
            target = texture_dir / "x.png"
            target.write_bytes(b"stale")
            map_builder = MapBuilder(AssetSource(MissingSource()), texture_dir=texture_dir)
            texture = LoadedTexture(b"fresh", 1, 1, False, "OPAQUE")
            map_builder._write_shared_texture("x.bmp", texture)
            self.assertEqual(target.read_bytes(), b"fresh")


if __name__ == "__main__":
    unittest.main()
