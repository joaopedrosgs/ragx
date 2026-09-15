"""``ragx status-icons`` — status (EFST) icons and the EFST id -> icon table.

The client picks a status icon from TWO places:

* Newer statuses are data: ``data\\luafiles514\\lua files\\stateicon\\
  stateiconimginfo.lub`` holds ``StateIconImgList[priority][EFST id] = "x.tga"``
  (450 rows, 282 files, in the 2026-01-07 LATAM client).
* The classic ones -- Blessing, Increase AGI, Angelus... -- are hardcoded in the
  client executable and appear in no data file. roBrowser Legacy recovered them
  into ``src/DB/Status/StatusInfo.js`` (icon names written as CP949 byte
  escapes), keyed by ``src/DB/Status/StatusConst.js``, whose numbers are the
  client's own EFST ids: the client's ``EFST_IDs`` table agrees (BLESSING 10,
  INC_AGI 12, CLAIRVOYANCE 184). Pass a checkout with ``--robrowser``.

The client table always wins. roBrowser only fills ids the client does not list,
and every row records its source, so a wrong classic icon can be traced to the
table that supplied it.

Art is ``data\\texture\\effect\\<file>`` (32x32 TGA, almost all RGBA). Writes
``<out>/icons/status/<basename>.png`` and ``<out>/icons/status.json``:

    {"<efst>": {"icon": basename, "priority": n, "source": "client" | "robrowser"}}
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from .. import client as client_mod
from ..lua import LuaEnv, decode

EFFECT_PREFIX = "data\\texture\\effect\\"
MAGENTA = (255, 0, 255)

_CONST_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*:\s*(-?\d+)\s*,?\s*$", re.M)
_ENTRY_RE = re.compile(r"StatusInfo\[SC\.([A-Z0-9_]+)\]\s*=\s*\{(.*?)\n\};", re.S)
_ICON_RE = re.compile(r"\bicon\s*:\s*'((?:[^'\\]|\\.)*)'")


def decode_js_string(literal: str) -> str:
    """A single-quoted JS string body -> the text it names.

    ``\\xNN`` escapes are BYTES of a CP949 name, so they are gathered and decoded
    as one run rather than as separate code points.
    """
    raw = bytearray()
    i = 0
    while i < len(literal):
        char = literal[i]
        if char == "\\" and i + 1 < len(literal):
            nxt = literal[i + 1]
            if nxt == "x" and i + 3 < len(literal):
                raw.append(int(literal[i + 2:i + 4], 16))
                i += 4
                continue
            raw.extend(nxt.encode("cp949", errors="replace"))
            i += 2
            continue
        raw.extend(char.encode("cp949", errors="replace"))
        i += 1
    return raw.decode("cp949", errors="replace")


def icon_file(name: str) -> str:
    """Normalise a table's icon name to the GRF's lower-case ``.tga`` form."""
    name = name.strip().lower()
    return name if name.endswith(".tga") else name + ".tga"


def parse_robrowser(status_const_js: str, status_info_js: str) -> dict[int, str]:
    """``EFST id -> icon file`` from roBrowser's two status tables."""
    consts = {name: int(value) for name, value in _CONST_RE.findall(status_const_js)}
    out: dict[int, str] = {}
    for const, body in _ENTRY_RE.findall(status_info_js):
        icon = _ICON_RE.search(body)
        if icon is None or const not in consts:
            continue
        text = decode_js_string(icon.group(1))
        if text:
            out[consts[const]] = icon_file(text)
    return out


def client_table(env: LuaEnv) -> dict[int, tuple[int, str]]:
    """``EFST id -> (priority, icon file)`` from the client's StateIconImgList."""
    table = env.table("StateIconImgList")
    out: dict[int, tuple[int, str]] = {}
    if table is None:
        return out
    for priority, entries in table.items():
        for efst, raw in entries.items():
            out[int(efst)] = (int(priority), icon_file(decode(raw)))
    return out


def merge(client: dict[int, tuple[int, str]], robrowser: dict[int, str]) -> dict[int, dict]:
    """The client's rows, then roBrowser's for ids the client does not list."""
    rows = {efst: {"file": file, "priority": priority, "source": "client"}
            for efst, (priority, file) in client.items()}
    for efst, file in robrowser.items():
        if efst not in rows:
            rows[efst] = {"file": file, "priority": 0, "source": "robrowser"}
    return rows


def _png(data: bytes):
    """Open a TGA; key magenta out of the rare one without an alpha channel."""
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    had_alpha = "A" in image.getbands()
    image = image.convert("RGBA")
    if not had_alpha:
        pixels = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                r, g, b, _ = pixels[x, y]
                if (r, g, b) == MAGENTA:
                    pixels[x, y] = (0, 0, 0, 0)
    return image


def export(grf, out: Path, robrowser: Path | None = None) -> dict:
    """Write the icons and table under ``out/icons``; return a summary."""
    icons_dir = out / "icons"
    dest = icons_dir / "status"
    dest.mkdir(parents=True, exist_ok=True)

    classic: dict[int, str] = {}
    if robrowser is not None:
        db = robrowser / "src" / "DB" / "Status"
        classic = parse_robrowser(
            (db / "StatusConst.js").read_text(encoding="utf-8", errors="replace"),
            (db / "StatusInfo.js").read_text(encoding="utf-8", errors="replace"))

    env = LuaEnv(grf)
    env.load("stateicon/efstids", optional=True)
    env.load("stateicon/stateiconimginfo", optional=True)
    rows = merge(client_table(env), classic)

    table: dict[str, dict] = {}
    written: set[str] = set()
    missing: set[str] = set()
    for efst, row in sorted(rows.items()):
        base = row["file"][:-4]
        if base not in written and row["file"] not in missing:
            path = EFFECT_PREFIX + row["file"]
            if path not in grf:
                missing.add(row["file"])
                continue
            _png(grf.read(path)).save(dest / (base + ".png"))
            written.add(base)
        if base in written:
            table[str(efst)] = {"icon": base, "priority": row["priority"],
                                "source": row["source"]}

    (icons_dir / "status.json").write_text(
        json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8")
    return {
        "icons": len(written),
        "efst_ids": len(table),
        "client": sum(1 for r in table.values() if r["source"] == "client"),
        "robrowser": sum(1 for r in table.values() if r["source"] == "robrowser"),
        "missing_files": sorted(missing),
    }


def run(args) -> int:
    robrowser = Path(args.robrowser) if args.robrowser else None
    with client_mod.open_stack(args.client) as grf:
        summary = export(grf, Path(args.out), robrowser)
    print(f"status icons: {summary['icons']} PNGs, {summary['efst_ids']} EFST ids "
          f"({summary['client']} client, {summary['robrowser']} robrowser), "
          f"{len(summary['missing_files'])} files missing -> "
          f"{Path(args.out) / 'icons' / 'status.json'}")
    if robrowser is None:
        print("  (no --robrowser: the classic hardcoded icons such as Blessing are not included)")
    return 0
