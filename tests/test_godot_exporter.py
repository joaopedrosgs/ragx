from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ragx.cli import build_parser
from ragx.exporters.godot import godot_export
from ragx.exporters.godot import pipeline
from ragx import map_builder


class GodotExporterTest(unittest.TestCase):
    def test_map_exporter_owns_its_engine_scale(self) -> None:
        self.assertEqual(godot_export.WORLD_SCALE, 0.2)
        self.assertEqual(godot_export.SUN_ORIGIN, (0.0, 60.0, 0.0))

    def test_adjacent_map_stages_reuse_parsed_rsw_and_gnd(self) -> None:
        class Reader:
            def read(self, path: str) -> bytes:
                return {"data\\test.rsw": b"rsw", "data\\test.gnd": b"gnd"}[path]

        builder = map_builder.MapBuilder(map_builder.AssetSource(Reader()))
        parsed_rsw = SimpleNamespace(gnd_file="test.gnd")
        parsed_gnd = object()
        with mock.patch.object(map_builder.rsw_format, "parse", return_value=parsed_rsw) as rsw_parse, \
                mock.patch.object(map_builder.gnd_format, "parse", return_value=parsed_gnd) as gnd_parse:
            first = builder.load_map_data("test")
            second = builder.load_map_data("test")

        self.assertIs(first, second)
        self.assertEqual(rsw_parse.call_count, 1)
        self.assertEqual(gnd_parse.call_count, 1)

    def test_godot_exporter_exposes_only_lite_and_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "export", "godot", "--client", "client", "--project", "game",
            "--rathena", "server",
        ])
        self.assertEqual(args.mode, "lite")
        self.assertEqual(args.processes, min(6, os.cpu_count() or 1))
        self.assertEqual(args.memory_mb, 6144)
        with self.assertRaises(SystemExit):
            parser.parse_args([
                "export", "godot", "--client", "client", "--project", "game",
                "--rathena", "server", "--mode", "starter",
            ])

    def test_pipeline_rejects_missing_client_before_work(self) -> None:
        parser = build_parser()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            args = parser.parse_args([
                "export", "godot", "--client", str(root / "client"),
                "--project", str(root / "game"),
                "--rathena", str(root / "server"),
            ])
            with self.assertRaisesRegex(FileNotFoundError, "data.grf"):
                pipeline.run(args)


if __name__ == "__main__":
    unittest.main()
