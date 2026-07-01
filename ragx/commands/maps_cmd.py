"""``ragx maps`` — convert Ragnarok maps to glTF 2.0.

Output (default ``gltf`` format) is ``<out>/maps/<map>.gltf`` + ``<map>.bin``
with every texture written once into a shared ``<out>/maps/textures/`` folder
and referenced by each map that uses it. ``--format glb`` embeds geometry and
textures into a single self-contained file per map instead.

Coordinates are standard glTF (right-handed, +Y up) with -Z = map north and the
terrain centred at the origin; one unit is one client world unit. Animations
(windmills, clock towers, swinging signs, ...) are exported as glTF animation
channels, one animation per animated object so each loops over its own duration.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from .. import client as client_mod

MAP_PREFIX = "data\\"


def list_maps(client_dir: str) -> list[str]:
    """Every top-level ``.rsw`` map name (without path or extension)."""
    archive = client_mod.open_archive(client_dir)
    try:
        return sorted(
            name[len(MAP_PREFIX):-4]  # strip "data\" and ".rsw"
            for name in archive.namelist()
            if name.endswith(".rsw") and name.count("\\") == 1
        )
    finally:
        archive.close()


def convert_one(client_dir: str, map_name: str, out_dir: str,
                fmt: str = "gltf") -> tuple[str, str]:
    """Convert a single map. Also the multiprocessing worker entry point;
    returns ``(map_name, summary-or-error)``."""
    from ..map_builder import AssetSource, MapBuilder, MapHasNoTerrain

    builder = globals().get("_WORKER_BUILDER")
    if builder is None or getattr(builder, "_client_dir", None) != client_dir:
        archive = client_mod.open_archive(client_dir)
        builder = MapBuilder(AssetSource(archive),
                             texture_dir=os.path.join(out_dir, "textures"))
        builder._client_dir = client_dir
        globals()["_WORKER_BUILDER"] = builder

    out_path = os.path.join(out_dir, f"{map_name}.{fmt}")
    try:
        t0 = time.time()
        stats = builder.build(map_name, out_path)
        size_mb = os.path.getsize(out_path) / 1e6
        message = (f"ok {time.time()-t0:5.1f}s {size_mb:7.1f}MB "
                   f"instances={stats.instances} animated={stats.animated_instances}")
        if stats.missing_models:
            message += f" missing_models={len(stats.missing_models)}"
        if stats.missing_textures:
            message += f" missing_textures={len(stats.missing_textures)}"
        if stats.model_errors:
            message += f" model_errors={len(stats.model_errors)}: {stats.model_errors[:2]}"
        return map_name, message
    except MapHasNoTerrain as error:
        return map_name, f"SKIPPED ({error})"
    except Exception:  # noqa: BLE001
        return map_name, "FAILED\n" + traceback.format_exc()


def _convert_star(job: tuple[str, str, str, str]) -> tuple[str, str]:
    return convert_one(*job)


def run(args) -> int:
    if args.list:
        for name in list_maps(args.client):
            print(name)
        return 0

    if args.all:
        maps = list_maps(args.client)
    else:
        maps = args.maps
    if not maps:
        sys.exit("no maps given (pass map names, --all, or --list)")

    out_dir = str(Path(args.out) / "maps")
    os.makedirs(out_dir, exist_ok=True)
    print(f"converting {len(maps)} map(s) -> {out_dir} (format: {args.format})",
          flush=True)

    t0 = time.time()
    failures = 0
    if args.processes > 1:
        import multiprocessing as mp
        jobs = [(args.client, m, out_dir, args.format) for m in maps]
        with mp.Pool(args.processes) as pool:
            for index, (name, message) in enumerate(
                    pool.imap_unordered(_convert_star, jobs, chunksize=1)):
                print(f"[{index+1}/{len(maps)}] {name}: {message}", flush=True)
                failures += "FAILED" in message
    else:
        for index, map_name in enumerate(maps):
            name, message = convert_one(args.client, map_name, out_dir, args.format)
            print(f"[{index+1}/{len(maps)}] {name}: {message}", flush=True)
            failures += "FAILED" in message

    print(f"\ndone: {len(maps) - failures}/{len(maps)} maps in {time.time()-t0:.0f}s")
    return 1 if failures else 0
