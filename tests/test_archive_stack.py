from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from ragx.commands import maps_cmd
from ragx.grf import GrfStack, normalize_path


class FakeArchive:
    def __init__(self, files: dict[str, bytes]):
        self.files = {normalize_path(name): value for name, value in files.items()}
        self.entries = {name: object() for name in self.files}

    def read(self, path: str) -> bytes:
        return self.files[normalize_path(path)]

    def close(self) -> None:
        pass


class ArchiveStackTests(unittest.TestCase):
    def test_later_archive_overrides_base_entry(self) -> None:
        stack = object.__new__(GrfStack)
        stack.archives = [
            FakeArchive({"data\\prontera.rsw": b"base"}),
            FakeArchive({"data\\prontera.rsw": b"event"}),
        ]
        self.assertEqual(stack.read("DATA/prontera.rsw"), b"event")
        self.assertEqual(stack.namelist(), ["data\\prontera.rsw"])

    def test_map_listing_uses_layered_stack(self) -> None:
        archive = mock.Mock()
        archive.namelist.return_value = [
            "data\\prontera.rsw",
            "data\\event_only.rsw",
            "data\\subdir\\not_a_map.rsw",
        ]
        with mock.patch.object(maps_cmd.client_mod, "open_stack", return_value=archive) as open_stack:
            maps = maps_cmd.list_maps("client")
        self.assertEqual(maps, ["event_only", "prontera"])
        open_stack.assert_called_once_with("client")
        archive.close.assert_called_once_with()

    def test_map_conversion_worker_uses_layered_stack(self) -> None:
        fake_builder = mock.Mock()
        fake_builder.build.return_value = SimpleNamespace(
            instances=1,
            animated_instances=0,
            missing_models=[],
            missing_textures=[],
            model_errors=[],
        )
        maps_cmd.__dict__.pop("_WORKER_BUILDER", None)
        with mock.patch.object(maps_cmd.client_mod, "open_stack", return_value=mock.Mock()) as open_stack, \
             mock.patch("ragx.map_builder.MapBuilder", return_value=fake_builder), \
             mock.patch.object(maps_cmd.os.path, "getsize", return_value=0):
            name, message = maps_cmd.convert_one("client", "prontera", "out", "gltf")
        maps_cmd.__dict__.pop("_WORKER_BUILDER", None)
        self.assertEqual(name, "prontera")
        self.assertTrue(message.startswith("ok"))
        open_stack.assert_called_once_with("client")


if __name__ == "__main__":
    unittest.main()
