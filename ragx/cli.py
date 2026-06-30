"""Unified command-line interface for ragx.

    ragx <command> [options]

Commands
    gltf      convert maps / models to glTF 2.0
    sprites   export SPR/ACT sprites to spritesheet PNG + JSON
    effects   export STR skill/visual effects to atlas PNG + JSON
    ui        export interface bitmaps to transparent PNG

The argument parser lives here (so ``ragx --help`` stays instant); the actual
work is in ``ragx.commands.*`` and imported lazily once a command is chosen.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__
from .client import DEFAULT_CLIENT

# Per-command logic module; imported only when that command runs.
_MODULES = {
    "gltf": "ragx.commands.gltf_cmd",
    "sprites": "ragx.commands.sprites_cmd",
    "effects": "ragx.commands.effects_cmd",
    "ui": "ragx.commands.ui_cmd",
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
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ragx",
        description="Export Ragnarok Online client assets to open formats "
                    "(glTF, PNG sprite sheets, effect atlases, UI images).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ragx gltf prontera\n"
            "  ragx gltf --all -j 8 --format glb\n"
            "  ragx sprites 몬스터/poring\n"
            "  ragx sprites --all -j 8\n"
            "  ragx effects lord stormgust\n"
            "  ragx ui --skin \"America Latina\"\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")

    common = _common_parent()
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # --- gltf -------------------------------------------------------------
    p = sub.add_parser(
        "gltf", parents=[common],
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
                   help="gltf = .bin + shared textures/ (default); glb = single file")
    p.add_argument("-j", "--processes", type=int, default=1, metavar="N",
                   help="parallel worker processes (default: 1)")
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
    module = importlib.import_module(_MODULES[args.command])
    return module.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
