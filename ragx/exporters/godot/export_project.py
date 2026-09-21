"""Re-export every asset a ragnadot Godot project needs, from a client GRF.

The Godot project commits only code, scenes and shaders; all sprites, effects,
palettes, lookup tables and converted maps are .gitignored and regenerated from
the client by this orchestrator.

    ragx export godot --client C:/Gravity/RO --project C:/path/to/ragnarok --mode lite

Modes
-----
full   every map in the GRF (hundreds; slow to convert and to import in Godot).
starter (also lite) all sprites + effects + palettes, with server-derived cities,
       surrounding maps, interiors and training/tutorial areas.
       The default: maps are the bulk of the time/size, and the starter region is enough
       to develop and test against — plus the novice path, which is not a
       convenience: a new character spawns on `iz_int01` and cannot leave a map
       that was never exported.

Both modes export the full sprite / effect / palette / table set - those are
what the runtime actually composes characters and skills from. Only the map
coverage differs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Folders that must carry a .gdignore so Godot never imports the ~150k runtime
# files (it loads them at runtime instead). effect_export already writes its
# own; we ensure the rest after exporting.
GDIGNORE_DIRS = ["sprites", "data", "palettes", "effects", "icons"]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PACKAGE_ROOT) + (os.pathsep + current if current else "")
    return env


def _run(label: str, args: list[str]) -> None:
    """Run a bundled project step, streaming its output. Aborts the
    whole export if a step fails — a half-populated project is worse than none."""
    print(f"\n=== {label} ===\n  {' '.join(args)}", flush=True)
    t0 = time.time()
    result = subprocess.run([sys.executable, "-m", *args], cwd=PACKAGE_ROOT,
                            env=_subprocess_env())
    if result.returncode != 0:
        sys.exit(f"\n!! {label} failed (exit {result.returncode}); aborting.")
    print(f"  ({time.time() - t0:.0f}s)", flush=True)


def _ensure_gdignores(project: Path) -> None:
    for name in GDIGNORE_DIRS:
        folder = project / name
        if folder.is_dir():
            ignore = folder / ".gdignore"
            if not ignore.exists():
                ignore.write_text("", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("grf", type=Path,
                        help="path to the client data.grf")
    parser.add_argument("project", type=Path,
                        help="Godot project root to populate")
    parser.add_argument("--mode", choices=("full", "starter", "lite"), default="starter",
                        help="map coverage (lite is an alias for starter)")
    parser.add_argument("--processes", type=int, default=min(6, os.cpu_count() or 1),
                        help="parallel workers for sprite/map export (default: up to 6)")
    parser.add_argument("--memory-mb", type=int, default=6144)
    parser.add_argument("--reserve-mb", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--rathena", type=Path,
                        help="rAthena checkout (required for starter/lite map selection)")
    parser.add_argument("--english-client", type=Path,
                        help="optional clean English/KRO client for wider item coverage")
    parser.add_argument("--robrowser", type=Path,
                        help="optional roBrowser Legacy checkout: the classic status "
                             "icons Ragexe hardcodes (Blessing, Increase AGI, ...)")
    args = parser.parse_args()
    if args.processes < 1:
        parser.error('--processes must be at least 1')

    grf = args.grf.resolve()
    if not grf.is_file():
        sys.exit(f"GRF not found: {grf}")
    if grf.name.lower() != "data.grf":
        # The exporters open <client>/data.grf, so we hand them the parent dir;
        # a differently named archive will not be found.
        print(f"warning: archive is named {grf.name!r}, but the exporters open "
              "'data.grf' inside the client dir — rename or symlink it.",
              file=sys.stderr)
    client = str(grf.parent)
    project = args.project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    out = str(project)
    procs = str(args.processes)

    # Fail before expensive family exports if required map content is missing.
    selected_maps = None
    if args.mode != "full":
        if args.rathena is None:
            parser.error("starter/lite requires --rathena to resolve enabled server maps")
        from .asset_profile import from_client
        import json
        profile = from_client(args.rathena, grf.parent)
        report_path = project / ".ragx-cache" / "starter-profile.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        if profile["missing_maps"]:
            sys.exit("Required starter maps missing: " + ", ".join(profile["missing_maps"]))
        selected_maps = profile["maps"]
        print(f"starter profile: {len(selected_maps)} maps; report: {report_path}", flush=True)

    t_start = time.time()
    print(f"ragnadot export_project\n  grf     : {grf}\n  client  : {client}"
          f"\n  project : {project}\n  mode    : {args.mode}"
          f"\n  procs   : {procs}", flush=True)

    # 1-5: sprites, palettes, effects and the lookup/skill tables (mode-independent).
    _run("sprite tables",  ["ragx.exporters.godot.sprite_tables", "--client", client, "--out", out])
    _run("sprite export",  ["ragx.exporters.godot.sprite_export", "--all", "--processes", procs,
                            "--client", client, "--out", out])
    _run("palette export", ["ragx.exporters.godot.palette_export", "--client", client, "--out", out])
    _run("effect export",  ["ragx.exporters.godot.effect_export", "--all", "--client", client, "--out", out])
    # The loose textures behind the client's *procedural* effects (bolts, hit
    # sparks, impact rings). No .str references them, so effect_export skips
    # them and the bolt skills end up with nothing to draw.
    _run("effect textures", ["ragx.exporters.godot.effect_texture_export", "--all",
                             "--client", client, "--out", out])
    _run("skill tables",   ["ragx.exporters.godot.skill_tables", "--client", client, "--out", out])
    localization_args = ["ragx.exporters.godot.localization_tables", "--client", client,
                         "--out", out]
    if args.english_client:
        localization_args += ["--english-client", str(args.english_client.resolve())]
    if args.rathena:
        localization_args += ["--rathena", str(args.rathena.resolve())]
    _run("localization",   localization_args)
    _run("icon export",    ["ragx.exporters.godot.icon_export", "--client", client, "--out", out])
    # Status icons come straight from ragx (the pinned tools/vendor snapshot, or
    # RAGX_PATH): it runs the client's stateicon Lua tables and, given
    # --robrowser, fills the classic icons the client executable hardcodes.
    status_args = [sys.executable, "-m", "ragx", "status-icons",
                   "--client", client, "-o", out]
    if args.robrowser:
        status_args += ["--robrowser", str(args.robrowser.resolve())]
    print(f"\n=== status icons ===\n  {' '.join(status_args[1:])}", flush=True)
    result = subprocess.run(status_args, cwd=PACKAGE_ROOT, env=_subprocess_env())
    if result.returncode:
        sys.exit(f"Status icon export failed (exit {result.returncode})")
    # Costume effects: the client's hat-effect id -> art table (ZC_EQUIPMENT_EFFECT).
    hat_args = [sys.executable, "-m", "ragx", "hat-effects", "--client", client, "-o", out]
    print(f"\n=== hat effects ===\n  {' '.join(hat_args[1:])}", flush=True)
    result = subprocess.run(hat_args, cwd=PACKAGE_ROOT, env=_subprocess_env())
    if result.returncode:
        sys.exit(f"Hat effect export failed (exit {result.returncode})")
    _run("cursor export",  ["ragx.exporters.godot.cursor_export", "--client", client, "--out", out])
    _run("ui assets",      ["ragx.exporters.godot.ui_export", "--client", client, "--out", out])
    _run("reference UI skin", ["ragx.exporters.godot.export_ui_skin", "--client", client,
                                "--assets", out])

    # 6: maps. godot_export writes terrains/models/textures/nav/maps into --assets.
    _run("Godot maps", ["ragx.exporters.godot.ragx_godot_export", *(selected_maps or []),
                         "--client", client, "--assets", out, "--processes", procs,
                         "--memory-mb", str(args.memory_mb),
                         "--reserve-mb", str(args.reserve_mb),
                         "--timeout", str(args.timeout)])

    _ensure_gdignores(project)

    print(f"\nDone in {time.time() - t_start:.0f}s. Open the project in Godot "
          "to import the generated map resources.")


if __name__ == "__main__":
    main()
