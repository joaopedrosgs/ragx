"""Generate Godot gettext catalogues from the official client's locale data.

The LATAM client splits player-facing database text across several places:

* ``System/itemInfo`` — item ids, names, and descriptions;
* ``pcjobnamegender_*`` — player job ids and names;
* ``mapnametable.txt`` — map codes and display names;
* ``data/i18n`` — English/PT-BR/Spanish NPC script text.

This exporter writes ``i18n/ro_names.<locale>.po``. Runtime keys are stable
(``ITEM_NAME_501``, ``JOB_NAME_7``, ``MAP_NAME_prontera``,
``MONSTER_NAME_1002``), while server-authored NPC strings use gettext contexts
``npc_name`` and ``npc``. When an rAthena checkout is supplied, its exact NPC
scripts scope the large client corpus and its mob ids anchor monster names.

Usage:
    python -m ragnadot.localization_tables --client C:/Gravity/Ragnarok \
        --english-client C:/path/to/clean/KRO --rathena C:/path/to/rathena \
        --out C:/path/to/godot
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import lupa.lua51 as lua51

from ragx.client import client_grf_paths
from ragx.grf import GrfStack
from .lua51 import LuaEnv, rebase_chunk

EntryKey = tuple[str, str]  # (gettext context, message id)

_ITEM_FIELDS = (b"identifiedDisplayName", b"unidentifiedDisplayName")
_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
_NPC_DECL = re.compile(
    r"\t(?:script|shop|cashshop|pointshop|marketshop|duplicate)\t([^\t]+)",
    re.IGNORECASE,
)


def _decode(value: object, encoding: str) -> str:
    if not isinstance(value, bytes):
        return "" if value is None else str(value)
    for candidate in (encoding, "utf-8", "cp1252", "cp949"):
        try:
            return value.decode(candidate)
        except UnicodeDecodeError:
            continue
    return value.decode(encoding, errors="replace")


def _execute_lua(path: Path):
    runtime = lua51.LuaRuntime(encoding=None)
    chunk = rebase_chunk(path.read_bytes(), 8)
    fn = runtime.eval(b"loadstring")(chunk, b"@" + path.name.encode())
    if isinstance(fn, tuple):
        raise ValueError(f"{path}: {fn[1]!r}")
    fn()
    return runtime


def load_item_names(path: Path, encoding: str) -> dict[int, str]:
    """Read a loose System itemInfo Lua/LUB into ``item id -> display name``."""
    runtime = _execute_lua(path)
    table = runtime.eval(b"tbl")
    result: dict[int, str] = {}
    if table is None:
        return result
    for item_id in table.keys():
        if not isinstance(item_id, int):
            continue
        row = table[item_id]
        value = next((row[field] for field in _ITEM_FIELDS if row[field]), None)
        name = _decode(value, encoding)
        if name:
            result[item_id] = name
    return result


def _find_item_file(client: Path, locale: str) -> tuple[Path, str] | None:
    if locale == "pt_BR":
        candidates = [
            (client / "System" / "itemInfo.lua", "cp1252"),
            (client / "System" / "iteminfo_new.lub", "cp1252"),
        ]
    else:
        candidates = [
            (client / "System" / "english" / "iteminfo_new.lub", "cp1252"),
            (client / "System" / "english" / "itemInfo.lua", "cp1252"),
            (client / "SystemEN" / "LuaFiles514" / "itemInfo.lua", "utf-8"),
            (client / "SystemEN" / "itemInfo.lua", "utf-8"),
        ]
    return next(((path, encoding) for path, encoding in candidates
                 if path.is_file() and path.stat().st_size > 1024), None)


def build_item_catalogs(client: Path,
                        english_client: Path | None) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {"pt_BR": {}, "en": {}}
    pt_source = _find_item_file(client, "pt_BR")
    en_source = _find_item_file(client, "en")
    fallback_source = (_find_item_file(english_client, "en")
                       if english_client is not None else None)
    if pt_source:
        result["pt_BR"] = load_item_names(*pt_source)
    if fallback_source:
        result["en"].update(load_item_names(*fallback_source))
    if en_source:
        result["en"].update(load_item_names(*en_source))
    elif not result["en"] and pt_source:
        # A clean KRO client has English in its ordinary System table.
        result["en"] = result["pt_BR"]
        result["pt_BR"] = {}
    return result


def load_job_names(client: Path, locale: str) -> dict[int, str]:
    """Read the official gendered job table (the male/general display column)."""
    module = ("datainfo/pcjobnamegender_ptbr" if locale == "pt_BR"
              else "datainfo/pcjobnamegender_enus")
    with GrfStack(client_grf_paths(client)) as grf:
        env = LuaEnv(grf)
        # This client file owns JOBID despite its name.
        env.load("skillinfoz/jobinheritlist")
        env.runtime.globals()[b"pcJobTbl2"] = env.runtime.table()
        if not env.load(module, optional=True):
            return {}
        table = env.table("PCJobNameTableMan")
        if table is None:
            return {}
        encoding = "cp1252" if locale == "pt_BR" else "utf-8"
        return {int(job_id): _decode(name, encoding)
                for job_id, name in table.items() if name}


def _decode_text(data: bytes, preferred: tuple[str, ...]) -> str:
    for encoding in preferred:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode(preferred[0], errors="replace")


def load_map_names(client: Path, locale: str) -> dict[str, str]:
    with GrfStack(client_grf_paths(client)) as grf:
        path = ("data\\english\\mapnametable.txt" if locale == "en"
                else "data\\mapnametable.txt")
        if path not in grf:
            return {}
        preferred = ("utf-8-sig", "cp1252") if locale == "en" \
            else ("cp1252", "utf-8-sig")
        text = _decode_text(grf.read(path), preferred)
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split("#")
        if len(parts) < 2:
            continue
        code = Path(parts[0].strip()).stem.lower()
        name = parts[1].strip()
        if code and name:
            result[code] = name
    return result


def load_client_script_pairs(client: Path) -> dict[str, Counter[str]]:
    """Official English -> PT-BR candidates from LATAM's data/i18n CSVs."""
    pairs: dict[str, Counter[str]] = defaultdict(Counter)
    with GrfStack(client_grf_paths(client)) as grf:
        paths = [name for name in grf.namelist()
                 if name.startswith("data\\i18n\\")
                 and name.endswith(".csv")]
        for path in paths:
            try:
                text = grf.read(path).decode("utf-8-sig")
            except UnicodeDecodeError:
                continue
            for row in csv.reader(io.StringIO(text)):
                # LATAM columns: key, Korean, English, ..., PT-BR, ..., Spanish.
                if len(row) <= 7 or not row[2] or not row[7]:
                    continue
                try:
                    english = base64.b64decode(row[2]).decode("utf-8")
                    portuguese = base64.b64decode(row[7]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    continue
                if english and portuguese:
                    pairs[english][portuguese] += 1
    return pairs


def _best_translations(pairs: dict[str, Counter[str]]) -> dict[str, str]:
    return {source: choices.most_common(1)[0][0]
            for source, choices in pairs.items() if choices}


def parse_mob_db(rathena: Path) -> dict[int, str]:
    path = rathena / "db" / "re" / "mob_db.yml"
    result: dict[int, str] = {}
    current_id: int | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("- Id:"):
            current_id = int(stripped.split(":", 1)[1].split("#", 1)[0])
        elif current_id is not None and line.startswith("    Name:"):
            name = line.split(":", 1)[1].strip().strip("\"'")
            result[current_id] = name
            current_id = None
    return result


def _unescape_script_string(value: str) -> str:
    return (value.replace(r"\n", "\n").replace(r"\t", "\t")
            .replace(r'\"', '"').replace(r"\\", "\\"))


def parse_npc_sources(rathena: Path) -> tuple[set[str], set[str]]:
    """Names and literal strings the connected server can actually send."""
    names: set[str] = set()
    text: set[str] = set()
    npc_root = rathena / "npc"
    for path in npc_root.rglob("*.txt"):
        source = path.read_text(encoding="utf-8", errors="replace")
        for match in _NPC_DECL.finditer(source):
            name = match.group(1).strip().strip("\"").split("#", 1)[0]
            if name:
                names.add(name)
        for match in _QUOTED.finditer(source):
            value = _unescape_script_string(match.group(1))
            if value:
                text.add(value)
                # select()/menu strings commonly pack choices with ':'.
                if ":" in value:
                    text.update(part for part in value.split(":") if part)
    return names, text


def _po_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_po(locale: str, entries: dict[EntryKey, str], path: Path) -> None:
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: ragnadot-official-names\\n"',
        f'"Language: {locale}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for (context, message), translation in sorted(entries.items()):
        if context:
            lines.append(f"msgctxt {_po_quote(context)}")
        lines += [
            f"msgid {_po_quote(message)}",
            f"msgstr {_po_quote(translation)}",
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {path.name}: {len(entries):,} messages "
          f"({path.stat().st_size:,} bytes)")


def _add_keyed(entries: dict[EntryKey, str], prefix: str,
               values: dict[object, str]) -> None:
    for key, value in values.items():
        if value:
            entries[("", f"{prefix}{key}")] = value


def build(client: Path, english_client: Path | None,
          rathena: Path | None) -> dict[str, dict[EntryKey, str]]:
    catalogs: dict[str, dict[EntryKey, str]] = {"pt_BR": {}, "en": {}}

    items = build_item_catalogs(client, english_client)
    for locale in catalogs:
        _add_keyed(catalogs[locale], "ITEM_NAME_", items[locale])
        _add_keyed(catalogs[locale], "JOB_NAME_",
                   load_job_names(client, locale))
        _add_keyed(catalogs[locale], "MAP_NAME_",
                   load_map_names(client, locale))

    if rathena is None:
        return catalogs

    official_pairs = load_client_script_pairs(client)
    pt_by_english = _best_translations(official_pairs)
    mobs = parse_mob_db(rathena)
    _add_keyed(catalogs["en"], "MONSTER_NAME_", mobs)
    _add_keyed(catalogs["pt_BR"], "MONSTER_NAME_",
               {mob_id: pt_by_english[name]
                for mob_id, name in mobs.items() if name in pt_by_english})

    npc_names, npc_text = parse_npc_sources(rathena)
    matched_names = {source: pt_by_english[source]
                     for source in npc_names if source in pt_by_english}
    matched_text = {source: pt_by_english[source]
                    for source in npc_text if source in pt_by_english}
    for source, translated in matched_names.items():
        catalogs["pt_BR"][("npc_name", source)] = translated
        catalogs["en"][("npc_name", translated)] = source
    for source, translated in matched_text.items():
        catalogs["pt_BR"][("npc", source)] = translated
        catalogs["en"][("npc", translated)] = source
    print(f"  official NPC corpus: {len(matched_names):,}/{len(npc_names):,} names, "
          f"{len(matched_text):,}/{len(npc_text):,} literals matched")
    return catalogs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True)
    parser.add_argument("--english-client")
    parser.add_argument("--rathena")
    parser.add_argument("--out", required=True, help="Godot project root")
    args = parser.parse_args()

    catalogs = build(
        Path(args.client),
        Path(args.english_client) if args.english_client else None,
        Path(args.rathena) if args.rathena else None,
    )
    output = Path(args.out) / "i18n"
    for locale, entries in catalogs.items():
        write_po(locale, entries, output / f"ro_names.{locale}.po")


if __name__ == "__main__":
    main()
