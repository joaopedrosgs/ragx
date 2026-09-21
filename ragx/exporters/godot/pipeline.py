"""Build all generated inputs required by the ragnadot Godot client."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


TABLES = (
    ("packet lengths + names", "gen_packets"),
    ("skill unit ids", "gen_skill_units"),
    ("item names", "gen_item_names"),
    ("weapon sprite views", "gen_weapon_views"),
    ("status names", "gen_efst_names"),
    ("server message texts", "gen_clif_messages"),
    ("achievement names", "gen_achievement_names"),
    ("quest titles", "gen_quest_names"),
    ("night-enabled maps", "gen_night_maps"),
)


def _run(label: str, args: list[str], cwd: Path) -> None:
    print(f"\n=== {label} ===\n  {' '.join(args)}", flush=True)
    started = time.time()
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PACKAGE_ROOT) + (os.pathsep + current if current else "")
    result = subprocess.run([sys.executable, "-m", *args], cwd=cwd, env=env)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit {result.returncode}")
    print(f"  ({time.time() - started:.0f}s)", flush=True)


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    client = Path(args.client).resolve()
    rathena = Path(args.rathena).resolve()
    grf = client / "data.grf"
    if not grf.is_file():
        raise FileNotFoundError(f"data.grf not found under {client}")
    if not (project / "project.godot").is_file():
        raise FileNotFoundError(f"not a Godot project: {project}")
    if not (rathena / "src" / "map").is_dir():
        raise FileNotFoundError(f"not an rAthena checkout: {rathena}")

    if not args.skip_assets:
        export = ["ragx.exporters.godot.export_project", str(grf), str(project),
                  "--mode", args.mode, "--processes", str(args.processes),
                  "--rathena", str(rathena),
                  "--memory-mb", str(args.memory_mb),
                  "--reserve-mb", str(args.reserve_mb),
                  "--timeout", str(args.timeout)]
        if args.robrowser:
            export += ["--robrowser", str(Path(args.robrowser).resolve())]
        if args.english_client:
            export += ["--english-client", str(Path(args.english_client).resolve())]
        _run("client assets", export, project)
        _run("sound effects", ["ragx", "sounds", "--client", str(client),
                               "--out", str(project)], project)

    data = project / "data"
    data.mkdir(exist_ok=True)
    for label, module in TABLES:
        command = [f"ragx.exporters.godot.{module}", "--rathena", str(rathena)]
        if module == "gen_quest_names":
            command += ["--client", str(client)]
        _run(label, command, project)

    if args.skip_assets:
        _run("official localization", ["ragx.exporters.godot.localization_tables",
             "--client", str(client), "--rathena", str(rathena),
             "--out", str(project)], project)
    else:
        _run("skill scenes", ["ragx.exporters.godot.gen_skill_scenes",
             "--project-root", str(project)], project)

    _run("server collision sync", ["ragx.exporters.godot.sync_gat", "--rathena",
         str(rathena), "--assets", str(project), "--write"], project)
    return 0
