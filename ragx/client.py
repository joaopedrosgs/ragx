"""Locating and opening a Ragnarok Online client's GRF archives.

Every command works directly from the client install — there is no separate
"extract" step. A client directory contains the main ``data.grf`` and may ship
extra archives (``event.grf``) underneath it, exactly as the game draws them.
"""

from __future__ import annotations

import os
from pathlib import Path

from .grf import GrfArchive, GrfStack

# Where Gravity's installers put the LATAM client; override with --client.
DEFAULT_CLIENT = r"C:\Gravity\Ragnarok"

MAIN_GRF = "data.grf"
# Extra archives the client layers UNDER data.grf (lower priority), used only if
# present. LATAM's event.grf is a 2010 seasonal pack: all 31 of its files also
# exist in data.grf (older Prontera, Izlude, Geffen, Alberta, Aldebaran, Hugel,
# prt_church, xmas), and the game draws the data.grf versions.
EXTRA_GRFS = ("event.grf",)


def main_grf_path(client: str | os.PathLike) -> Path:
    return Path(client) / MAIN_GRF


def _require_main(client: str | os.PathLike) -> Path:
    path = main_grf_path(client)
    if not path.is_file():
        raise SystemExit(
            f"{MAIN_GRF} not found under client dir: {Path(client)}\n"
            f"  (looked for {path})\n"
            f"  Point --client at your Ragnarok Online install folder."
        )
    return path


def open_archive(client: str | os.PathLike) -> GrfArchive:
    """Open just ``data.grf`` (the single archive most commands read)."""
    return GrfArchive(_require_main(client))


def open_stack(client: str | os.PathLike) -> GrfStack:
    """Open ``data.grf`` over any extra archives the client ships with it."""
    extras = [Path(client) / extra for extra in EXTRA_GRFS]
    paths = [p for p in extras if p.is_file()] + [_require_main(client)]
    return GrfStack(paths)


def client_grf_paths(client: str | os.PathLike) -> list[str]:
    """Return the archive stack paths in the same precedence order as open_stack."""
    extras = [Path(client) / extra for extra in EXTRA_GRFS]
    paths = [p for p in extras if p.is_file()] + [_require_main(client)]
    return [str(path) for path in paths]
