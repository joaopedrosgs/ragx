"""Locating and opening a Ragnarok Online client's GRF archives.

Every command works directly from the client install — there is no separate
"extract" step. A client directory contains the main ``data.grf`` and may layer
extra archives (``event.grf``) on top, exactly as the game does.
"""

from __future__ import annotations

import os
from pathlib import Path

from .grf import GrfArchive, GrfStack

# Where Gravity's installers put the LATAM client; override with --client.
DEFAULT_CLIENT = r"C:\Gravity\Ragnarok"

MAIN_GRF = "data.grf"
# Extra archives the client layers on top of data.grf (higher priority), used
# only if present. The sprite/effect data lives in data.grf; event.grf carries
# a few overrides.
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
    """Open ``data.grf`` plus any extra archives the client layers on top."""
    paths = [_require_main(client)]
    for extra in EXTRA_GRFS:
        candidate = Path(client) / extra
        if candidate.is_file():
            paths.append(candidate)
    return GrfStack(paths)
