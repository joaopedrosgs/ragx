"""Regenerate this project's Godot maps from the canonical ragx converter.

ragx owns all access to and interpretation of the original client files. The
local ``ragnadot.godot_export`` module only arranges those converted assets as
the project's decomposed glTF files and ``maps/<name>.tscn`` scenes.

Examples (run from the project root):
    python tools/ragx_godot_export.py prontera --ragx C:\path\to\ragx
    python tools/ragx_godot_export.py --processes 8 --rebuild-models
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path


PROJECT_ROOT = Path.cwd()
RAGX_ROOT = Path(__file__).resolve().parents[3]
WORLD_SCALE = 0.2


def _ragx_repo(path: Path) -> Path:
    """Accept either the ragx repository or its parent checkout directory."""
    resolved = path.resolve()
    if (resolved / "ragx" / "__init__.py").is_file():
        return resolved
    nested = resolved / "ragx"
    if (nested / "ragx" / "__init__.py").is_file():
        return nested
    raise FileNotFoundError(f"not a ragx checkout: {resolved}")


def _activate_ragx(repo: Path) -> None:
    repo_text = str(repo)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)


def _revision(repo: Path) -> str:
    if not (repo / '.git').exists():
        from ragx.incremental import code_signature
        return 'source-' + code_signature(repo / 'ragx')[:12]
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo),
         "describe", "--always", "--dirty"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _new_builder(ragx_repo: str, client_dir: str, assets_dir: str):
    _activate_ragx(Path(ragx_repo))
    from ragx.client import open_archive
    from ragx.map_builder import AssetSource, MapBuilder
    from ragx.resource_aliases import ResourceAliases

    archive = ResourceAliases(open_archive(client_dir))
    builder = MapBuilder(
        AssetSource(archive),
        texture_dir=Path(assets_dir) / "textures",
        world_scale=WORLD_SCALE,
        cache_root=assets_dir,
    )
    from ragx.incremental import digest
    adapter = Path(__file__).with_name('godot_export.py')
    builder.build_cache.signature = digest((builder.build_cache.signature + digest(adapter.read_bytes())).encode())
    return builder


def _worker_builder(ragx_repo: str, client_dir: str, assets_dir: str):
    key = (ragx_repo, client_dir, assets_dir)
    state = globals().get("_WORKER_STATE")
    if state is None or state[0] != key:
        state = (key, _new_builder(ragx_repo, client_dir, assets_dir))
        globals()["_WORKER_STATE"] = state
    return state[1]


def _map_worker(job: tuple[str, str, str, str]) -> tuple[str, str]:
    ragx_repo, client_dir, assets_dir, map_name = job
    _activate_ragx(Path(ragx_repo))
    from ragx.map_builder import MapHasNoTerrain

    try:
        builder = _worker_builder(ragx_repo, client_dir, assets_dir)
        from ragx.formats import gat, rsw
        from ragx.exporters.godot.godot_export import export_map

        started = time.time()
        cache = builder.build_cache
        previous_builds = cache.builds
        summary = cache.run('godot-map:' + map_name, {'scale': WORLD_SCALE}, lambda: export_map(
            builder, map_name, Path(assets_dir), rsw_parse=rsw.parse, gat_parse=gat.parse))
        status = 'cached' if cache.builds == previous_builds else 'built'
        return map_name, f"{status} {summary} ({time.time() - started:.1f}s) cache_hits={cache.hits} builds={cache.builds}"
    except MapHasNoTerrain as error:
        return map_name, f"SKIPPED ({error})"
    except Exception:  # noqa: BLE001
        return map_name, "FAILED\n" + traceback.format_exc()


_MODEL_SUFFIX = re.compile(r"@s(?P<speed>[0-9]+(?:\.[0-9]+)?)$")


def _model_spec(relative_path: str) -> tuple[str, float, bool]:
    """Recover the RSM name and variant settings encoded by godot_export."""
    stem = relative_path[:-5] if relative_path.lower().endswith(".gltf") \
        else relative_path
    mirrored = stem.endswith("@mirror")
    if mirrored:
        stem = stem[:-len("@mirror")]
    speed = 1.0
    match = _MODEL_SUFFIX.search(stem)
    if match is not None:
        speed = float(match.group("speed"))
        stem = stem[:match.start()]
    return stem.replace("/", "\\"), speed, mirrored


def _model_worker(job: tuple[str, str, str, str]) -> tuple[str, str]:
    ragx_repo, client_dir, assets_dir, relative_path = job
    try:
        builder = _worker_builder(ragx_repo, client_dir, assets_dir)
        model_name, speed, mirrored = _model_spec(relative_path)
        output = Path(assets_dir) / "models" / Path(relative_path)
        uri_base = "../" * (relative_path.count("/") + 1)
        stats = builder.build_model_gltf(
            model_name,
            output,
            uri_base=uri_base,
            effective_speed=speed,
            flip_winding=mirrored,
        )
        if stats is None:
            return relative_path, "FAILED missing source model"
        if stats.model_errors:
            return relative_path, f"FAILED {stats.model_errors[0]}"
        return relative_path, "ok"
    except Exception:  # noqa: BLE001
        return relative_path, "FAILED\n" + traceback.format_exc()


def _run_jobs(label: str, jobs: list[tuple[str, str, str, str]], worker,
              processes: int) -> int:
    failures = 0
    total = len(jobs)
    if processes > 1:
        # RSM templates can be large. Bound each worker's lifetime as well as
        # ragx's own caches so a whole-client export cannot accumulate gigabytes.
        with mp.Pool(processes, maxtasksperchild=8) as pool:
            results = pool.imap_unordered(worker, jobs, chunksize=1)
            for index, (name, message) in enumerate(results, 1):
                print(f"[{label} {index}/{total}] {name}: {message}", flush=True)
                failures += message.startswith("FAILED")
    else:
        for index, job in enumerate(jobs, 1):
            name, message = worker(job)
            print(f"[{label} {index}/{total}] {name}: {message}", flush=True)
            failures += message.startswith("FAILED")
    return failures


def _available_maps(ragx_repo: Path, client_dir: str) -> list[str]:
    _activate_ragx(ragx_repo)
    from ragx.commands.maps_cmd import list_maps

    return list_maps(client_dir)


def _model_paths(assets_dir: Path) -> list[str]:
    models_dir = assets_dir / "models"
    return sorted(
        path.relative_to(models_dir).as_posix()
        for path in models_dir.rglob("*.gltf")
    ) if models_dir.is_dir() else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("maps", nargs="*", help="map names; omit for every map")
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--assets", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--ragx", type=Path, default=RAGX_ROOT)
    parser.add_argument("--processes", type=int,
                        default=max(1, min(2, os.cpu_count() or 1)))
    parser.add_argument('--memory-mb', type=int, default=4096)
    parser.add_argument('--reserve-mb', type=int, default=2048)
    parser.add_argument('--timeout', type=float, default=7200)
    parser.add_argument(
        "--rebuild-models",
        action="store_true",
        help="after maps, rebuild every generated model variant with ragx",
    )
    args = parser.parse_args()
    if args.processes < 1:
        parser.error("--processes must be at least 1")

    ragx_repo = _ragx_repo(args.ragx)
    _activate_ragx(ragx_repo)
    from ragx.process_budget import supervise, worker_limit
    args.processes = worker_limit(args.processes, args.memory_mb, args.reserve_mb)
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if not os.environ.get('RAGX_SUPERVISED'):
        return supervise([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                         memory_mb=args.memory_mb, reserve_mb=args.reserve_mb, timeout=args.timeout)
    assets_dir = args.assets.resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)
    from ragx.exporters.godot.godot_export import prepare_project

    prepare_project(assets_dir)
    maps = args.maps or _available_maps(ragx_repo, args.client)
    print(
        f"ragx={ragx_repo} revision={_revision(ragx_repo)} "
        f"world_scale={WORLD_SCALE} maps={len(maps)}",
        flush=True,
    )

    common = (str(ragx_repo), str(Path(args.client).resolve()), str(assets_dir))
    map_jobs = [(*common, map_name) for map_name in maps]
    failures = _run_jobs("map", map_jobs, _map_worker, args.processes)

    if args.rebuild_models:
        paths = _model_paths(assets_dir)
        print(f"rebuilding {len(paths)} generated model variants", flush=True)
        model_jobs = [(*common, relative_path) for relative_path in paths]
        failures += _run_jobs("model", model_jobs, _model_worker, args.processes)

    print(f"done: failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
