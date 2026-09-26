"""Unified command-line interface for ragx.

    ragx <command> [options]

Commands
    maps             convert maps / models to glTF 2.0
    sprites          export SPR/ACT sprites to spritesheet PNG + JSON
    effects          export STR skill/visual effects to atlas PNG + JSON
    effect-textures  export the loose textures the client's procedural effects
                     (bolts, hit sparks, impact rings) are drawn from
    cursors          export the animated mouse cursors (arrow, target ring, …)
    ui               export interface bitmaps to transparent PNG
    status-icons     export status (EFST) icons and the EFST id -> icon table
    hat-effects      export the costume effect table (hat-effect id -> art)

The argument parser lives here (so ``ragx --help`` stays instant); the actual
work is in ``ragx.commands.*`` and imported lazily once a command is chosen.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from . import __version__
from .client import DEFAULT_CLIENT

# Per-command logic module; imported only when that command runs.
_MODULES = {
    "sounds": "ragx.commands.sounds_cmd",
    "maps": "ragx.commands.maps_cmd",
    "sprites": "ragx.commands.sprites_cmd",
    "effects": "ragx.commands.effects_cmd",
    "effect-textures": "ragx.commands.effect_textures_cmd",
    "cursors": "ragx.commands.cursors_cmd",
    "ui": "ragx.commands.ui_cmd",
    "status-icons": "ragx.commands.status_icons_cmd",
    "hat-effects": "ragx.commands.hat_effects_cmd",
    "navigation": "ragx.commands.navigation_cmd",
}


def _common_parent() -> argparse.ArgumentParser:
    """Options shared by every command (client location + output root)."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--client", metavar="DIR", default=DEFAULT_CLIENT,
        help="Ragnarok Online client install directory containing data.grf "
             f"(default: {DEFAULT_CLIENT})")
    parent.add_argument(
        "-o", "--out", metavar="DIR", default="ragx_out",
        help="output root; each command writes a named sub-folder under it "
             "(default: ./ragx_out)")
    parent.add_argument('--memory-mb', type=int, default=4096, help='process-tree private memory ceiling')
    parent.add_argument('--reserve-mb', type=int, default=2048, help='available RAM reserved for the desktop')
    parent.add_argument('--timeout', type=float, default=7200, help='conversion timeout in seconds')
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ragx",
        description="Export Ragnarok Online client assets to open formats "
                    "(glTF, PNG sprite sheets, effect atlases, UI images).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ragx maps prontera\n"
            "  ragx maps --all -j 8 --format glb\n"
            "  ragx sprites 몬스터/poring\n"
            "  ragx sprites --all -j 8\n"
            "  ragx effects lord stormgust\n"
            "  ragx ui --skin \"America Latina\"\n"
            "  ragx ui --list equip\n"
            "  ragx ui --folder swap_equipment --folder inventory\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")

    common = _common_parent()
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # --- maps -------------------------------------------------------------
    p = sub.add_parser(
        "maps", parents=[common],
        help="convert maps / models to glTF 2.0",
        description="Convert Ragnarok maps to glTF 2.0. Output goes to "
                    "<out>/maps/<map>.gltf (+ .bin) with textures shared in "
                    "<out>/maps/textures/. Use --format glb for single files.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("maps", nargs="*", metavar="MAP",
                   help="map names without extension (e.g. prontera)")
    p.add_argument("--all", action="store_true",
                   help="convert every map in the client")
    p.add_argument("-f", "--format", choices=("gltf", "glb"), default="gltf",
                   help="output format: gltf = .bin + shared textures/ (default); "
                        "glb = single self-contained file")
    p.add_argument("-j", "--processes", type=int, default=1, metavar="N",
                   help="parallel worker processes (default: 1)")
    p.add_argument("--world-scale", type=float, default=1.0, metavar="FACTOR",
                   help="bake every map/model length by FACTOR (default: 1.0)")
    p.add_argument("--list", action="store_true",
                   help="list the available map names and exit")

    # --- sprites ----------------------------------------------------------
    p = sub.add_parser(
        "sprites", parents=[common],
        help="export SPR/ACT sprites to spritesheet PNG + JSON",
        description="Export SPR/ACT sprite pairs to a shelf-packed spritesheet "
                    "PNG plus an animation JSON, under <out>/sprites/.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sprites", nargs="*", metavar="PATH",
                   help="sprite paths relative to data/sprite/, no extension "
                        "(e.g. 몬스터/poring)")
    p.add_argument("--all", action="store_true",
                   help="export every sprite in the client")
    p.add_argument("-j", "--processes", type=int, default=1, metavar="N",
                   help="parallel worker processes (default: 1)")

    # --- effects ----------------------------------------------------------
    p = sub.add_parser(
        "effects", parents=[common],
        help="export STR skill/visual effects to atlas PNG + JSON",
        description="Export STR effects (skills, spells, visual effects) to a "
                    "packed texture atlas PNG plus a keyframe JSON, under "
                    "<out>/effects/.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("effects", nargs="*", metavar="NAME",
                   help="effect names relative to texture/effect/, no extension "
                        "(matched by full path or basename)")
    p.add_argument("--all", action="store_true",
                   help="export every effect in the client")

    # --- effect-textures --------------------------------------------------
    p = sub.add_parser(
        "effect-textures", parents=[common],
        help="export the loose textures behind the client's procedural effects",
        description="Some RO effects have no .str and no model — the client "
                    "builds them in code from single textures (Cold Bolt rains "
                    "'icearrow' billboards, then draws a 'ring_blue' impact "
                    "ring). Export that loose art, magenta-keyed, to "
                    "<out>/effects/tex/. Defaults to the textures directly "
                    "under the effect dir, which is where the procedural art "
                    "lives; --all walks the whole tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("textures", nargs="*", metavar="NAME",
                   help="texture names relative to texture/effect/ (extension "
                        "optional; matched by full path or basename)")
    p.add_argument("--all", action="store_true",
                   help="export the whole effect texture tree, not just loose ones")

    # --- cursors ----------------------------------------------------------
    p = sub.add_parser(
        "cursors", parents=[common],
        help="export the animated mouse cursors",
        description="Export data\\sprite\\cursors.spr/.act — one animated cursor "
                    "per state (normal arrow, talk hand, skill-target ring, …) — "
                    "to <out>/cursor/<name>/ as frame PNGs plus a JSON with the "
                    "frame delay and click hotspot.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", metavar="NAME",
                   help="export just one cursor state (e.g. target)")

    # --- ui ---------------------------------------------------------------
    p = sub.add_parser(
        "ui", parents=[common],
        help="export interface bitmaps to transparent PNG",
        description="Export interface bitmaps (window chrome, buttons, gauges) "
                    "from the client to magenta-keyed transparent PNGs, plus "
                    "composited 9-slice theme textures, under <out>/ui/.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skin", metavar="NAME", default=None,
                   help="overlay a named skin from <client>/skin/<NAME>/ on top "
                        "of the base interface")
    p.add_argument("--groups", nargs="*", metavar="GROUP", default=None,
                   help="limit to specific interface groups (default: all)")
    p.add_argument("--folder", action="append", metavar="NAME", default=None,
                   help="export EVERY bitmap under an interface folder, at any "
                        "depth (repeatable), instead of the built-in groups")
    p.add_argument("--list", nargs="?", const="", default=None, metavar="TEXT",
                   help="list interface files whose path contains TEXT, and exit")

    sub.add_parser("sounds", parents=[common],
                   help="export all sound effects to PCM WAV under audio/sfx")

    # --- hat-effects ------------------------------------------------------
    sub.add_parser(
        "hat-effects", parents=[common],
        help="export the costume effect table (hat-effect id -> art)",
        description="Run the client's hateffectinfo.lub and write "
                    "<out>/data/hat_effects.json: for each hat-effect id the "
                    "server sends in ZC_EQUIPMENT_EFFECT, the STR (named as the "
                    "effects command exports it) or effect-table id the client "
                    "draws, with its offsets and ordering flags. Needs lupa.")

    # --- navigation -------------------------------------------------------
    p = sub.add_parser(
        "navigation", parents=[common],
        help="export the Navigation window's map, NPC and monster tables",
        description="Read navi_map/navi_npc/navi_mob and write "
                    "<out>/data/navigation.json. Prefer --navi-dir, the folder "
                    "rAthena's `map-server-generator --generate-navi` writes: it "
                    "describes the server's own NPCs and spawns with plain names. "
                    "The client's copies obfuscate NPC and monster names, which "
                    "are then left empty. Needs lupa.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--navi-dir", metavar="DIR", default=None,
                   help="rAthena's generated navigation folder "
                        "(generated/clientside/data/luafiles514/lua files/navigation)")

    # --- status-icons -----------------------------------------------------
    p = sub.add_parser(
        "status-icons", parents=[common],
        help="export status (EFST) icons and the EFST id -> icon table",
        description="Export the icons the client shows for timed statuses "
                    "(Blessing, Increase AGI, ...) from data\\texture\\effect\\ "
                    "to <out>/icons/status/, plus <out>/icons/status.json mapping "
                    "each EFST id to its icon. Newer statuses come from the "
                    "client's stateiconimginfo.lub; the classic ones the client "
                    "executable hardcodes need --robrowser. Runs the client's "
                    "Lua tables, so it needs lupa (pip install ragx[lua]).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--robrowser", metavar="DIR", default=None,
                   help="roBrowser Legacy checkout, for the classic icons the "
                        "client executable hardcodes (src/DB/Status/)")
    p.add_argument("--text", action="append", metavar="LOCALE=ROOT[:ENCODING]",
                   default=None,
                   help="a language root for the tooltip text, repeatable "
                        "(default: pt_BR=data, en=data\\english, es=data\\spanish, "
                        "all cp1252)")

    return parser


def _force_utf8() -> None:
    """RO asset names are Korean (CP949); make sure our console can print them
    instead of dying on the default Windows cp1252 codec."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if min(args.memory_mb, args.reserve_mb, args.timeout, getattr(args, 'processes', 1)) <= 0:
        parser.error('workers and budgets must be positive')
    from .process_budget import supervise, worker_limit
    if not os.environ.get('RAGX_SUPERVISED') and not getattr(args, 'list', False):
        return supervise([sys.executable, '-m', 'ragx', *(sys.argv[1:] if argv is None else argv)],
                         memory_mb=args.memory_mb, reserve_mb=args.reserve_mb, timeout=args.timeout)
    if hasattr(args, 'processes'):
        args.processes = worker_limit(args.processes, args.memory_mb, args.reserve_mb)
    module = importlib.import_module(_MODULES[args.command])
    return module.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
