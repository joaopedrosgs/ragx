"""Resolve the starter map selection from enabled Renewal scripts and GRF entries.

This is build-time selection metadata, never a runtime world manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .map_closure import WARP_RE


def enabled_scripts(root: Path, entry: str, fingerprints: dict | None = None) -> list[Path]:
    active: dict[Path, None] = {}
    visiting: set[Path] = set()

    def visit(relative: str) -> None:
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
        if path in visiting:
            raise ValueError(f'NPC import cycle: {relative}')
        visiting.add(path)
        data = path.read_bytes()
        if fingerprints is not None:
            fingerprints[path.relative_to(root.resolve()).as_posix()] = hashlib.sha256(data).hexdigest()
        for raw in data.decode('utf-8-sig').splitlines():
            line = raw.split('//', 1)[0].strip()
            match = re.match(r'^(import|npc|delnpc):\s*(.*?)\s*$', line)
            if not match:
                continue
            kind, target = match.groups()
            if kind == 'import':
                visit(target)
            elif kind == 'npc' and target == 'clear':
                active.clear()
            else:
                script = (root / target).resolve()
                script.relative_to(root.resolve())
                if kind == 'delnpc':
                    active.pop(script, None)
                else:
                    if not script.is_file():
                        raise FileNotFoundError(script)
                    active[script] = None
        visiting.remove(path)

    visit(entry)
    return list(active)


def go_maps(root: Path) -> list[str]:
    header = (root / 'src/common/mapindex.hpp').read_text(encoding='utf-8')
    # Resolve this pinned project's Renewal branch, including MAP_NOVICE.
    header = re.sub(r'#ifdef RENEWAL\s*(.*?)#else.*?#endif', r'\1', header, flags=re.S)
    names = dict(re.findall(r'#define\s+(MAP_\w+)\s+"([^"]+)"', header))
    source = (root / 'src/map/atcommand.cpp').read_text(encoding='utf-8')
    table = source.split('ACMD_FUNC(go)', 1)[1].split('} data[] = {', 1)[1].split('};', 1)[0]
    return sorted({names[key] for key in re.findall(r'\{\s*(MAP_\w+)', table)})


def resolve(root: Path, available: set[str], entry: str = 'npc/re/scripts_main.conf') -> dict:
    seeds = go_maps(root)
    reasons: dict[str, set[str]] = {name: {'@go'} for name in seeds}
    edges: dict[str, set[str]] = {}
    city_edges: list[tuple[str, str, str]] = []
    source_hashes = {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                     for relative in ('src/common/mapindex.hpp', 'src/map/atcommand.cpp', entry)}
    for path in enabled_scripts(root, entry, source_hashes):
        relative = path.relative_to(root.resolve()).as_posix()
        data = path.read_bytes()
        source = re.sub(r'/\*.*?\*/', '', data.decode('utf-8-sig', errors='replace'), flags=re.S)
        source = '\n'.join(line.split('//', 1)[0] for line in source.splitlines())
        tutorial = relative == 'npc/re/jobs/novice/academy.txt' or '/custom/lasagna/' in relative
        relevant = False
        for line in source.splitlines():
            warp = WARP_RE.match(line.strip())
            if warp:
                src, dst = warp.group('src', 'dst')
                edges.setdefault(src, set()).add(dst)
                if '/warps/cities/' in relative:
                    city_edges.append((src, dst, relative))
                relevant = True
        if tutorial:
            # Placed NPCs and literal destinations complement static warp edges.
            maps = set(re.findall(r'^\s*([\w@-]+),\d+,\d+,\d+\s', source, re.M))
            maps.update(re.findall(r'\bwarp\s+"([\w@-]+)"\s*,', source))
            # Academy constructs prt_fild + an NPC suffix; keep every existing
            # matching map instead of treating the prefix as a real destination.
            for prefix in re.findall(r'\bwarp\s+"([\w@-]+)"\s*\+', source):
                maps.update(name for name in available if name.startswith(prefix))
            # Includes constant arrays used by the Academy's computed warp names.
            maps.update(value for value in re.findall(r'"([\w@-]+)"', source) if value in available)
            for name in maps - {'this', 'Random', 'SavePoint'}:
                reasons.setdefault(name, set()).add(relative)
            relevant = True
        if relevant:
            source_hashes[relative] = hashlib.sha256(data).hexdigest()
    # City warp files describe interiors across multiple floors. Follow their
    # connected component, but do not continue down the global field graph.
    changed = True
    city = set(seeds)
    while changed:
        changed = False
        for src, dst, origin in city_edges:
            if src in city or dst in city:
                for name in (src, dst):
                    reasons.setdefault(name, set()).add(origin)
                    if name not in city:
                        city.add(name)
                        changed = True
    for seed in seeds:
        for name in edges.get(seed, ()):
            reasons.setdefault(name, set()).add(f'city exit: {seed}')
    for name in available:
        if re.fullmatch(r'(?:iz_int|int_land)\d*|new_zone\d+|iz_ac\d+', name):
            reasons.setdefault(name, set()).add('training/academy variants')
    return {'schema': 1, 'profile': 'starter', 'entry': entry, 'go_maps': seeds,
            'maps': sorted(reasons), 'missing_maps': sorted(set(reasons) - available),
            'reasons': {name: sorted(reasons[name]) for name in sorted(reasons)},
            'source_sha256': source_hashes,
            'scope': 'City warp components, direct city exits, Academy and Lasagna scripts; no recursive field traversal.'}


def from_client(root: Path, client: Path, entry: str = 'npc/re/scripts_main.conf') -> dict:
    from ragx.client import open_archive
    from ragx.resource_aliases import ResourceAliases
    archive = ResourceAliases(open_archive(client))
    try:
        # Match the map adapter's base-archive policy and require all three files.
        entries = set(archive.namelist())
        available = {name[5:-4] for name in entries if name.startswith('data\\')
                     and name.count('\\') == 1 and name.endswith('.rsw')
                     and all(name[:-4] + ext in entries for ext in ('.gnd', '.gat'))}
    finally:
        archive.close()
    return resolve(root.resolve(), available, entry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rathena', type=Path, required=True)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--entry', default='npc/re/scripts_main.conf')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = from_client(args.rathena, args.client, args.entry)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ok': not report['missing_maps'], 'maps': len(report['maps']),
                      'missing_maps': report['missing_maps'], 'report': str(args.out)}))
    return int(bool(report['missing_maps']))


if __name__ == '__main__':
    raise SystemExit(main())
