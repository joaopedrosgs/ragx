from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ragx.cli import build_parser
from ragx.exporters.godot import pipeline


class GodotExporterTest(unittest.TestCase):
    def test_godot_exporter_exposes_only_lite_and_full(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "export", "godot", "--client", "client", "--project", "game",
            "--rathena", "server",
        ])
        self.assertEqual(args.mode, "lite")
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
