"""Dump the client's skill catalogue + skill->effect mapping to data/*.json.

Produces, under ``<godot>/data/``:

  skills.json        id -> {const, name, max_lv, authored popup descriptions}
  effect_ids.json    EF_* number -> {const, str}  (the classic effect list)
  skill_effects.json SKID name   -> effect str basename (best effort)

It also writes Godot gettext catalogues under ``<godot>/i18n/``:

  skills.pt_BR.po    ``SKILL_NAME_<SKID>`` -> the LATAM base-table name
  skills.en.po       ``SKILL_NAME_<SKID>`` -> the client English-overlay name

LATAM stores the latter below ``data\\english\\luafiles514``. A clean KRO
client stores English in its ordinary patched table instead, so
``--english-client`` may supply one as a const-keyed fallback. The runtime uses
Godot's TranslationServer for these catalogues; the JSON is not a second
localisation system.

The classic numeric effect list (EF_LORD = 90, ...) is hardcoded in the
client binary, not the lua tables (the lua ``EFID``/``SKILL_EFFECT_INFO_
LIST`` only name a partial, modern subset). We therefore seed ``effect_
ids`` from the community Effects2Str table (references/GRFEditor) and
overlay the lua ``EFID`` names, then resolve ``skill_effects`` by, in
priority order: a curated map of iconic skills, the lua per-skill effect
overrides, and a SKID-name/str-filename heuristic.

Usage:
    python -m ragnadot.skill_tables --out C:/Users/pedro/Documents/ragnarok
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ragx.client import client_grf_paths
from ragx.grf import GrfStack
from .lua51 import LuaEnv, decode

EFFECT_PREFIX = "data\\texture\\effect\\"
SPRITE_PREFIX = "data\\sprite\\"
EFFECTS2STR = (Path(__file__).resolve().parent.parent / "references"
               / "GRFEditor" / "GRF" / "FileFormats" / "Effects2Str.cs")

# Skill -> effect .str basename for the iconic skills the client dispatches
# from hardcoded C++ (so they are absent from the lua tables). Extend freely.
# All targets verified to exist as exported .str effects. Bolt/single-target
# low spells (Fireball, Soul Strike, Frost Diver, ...) are NOT here: the
# client renders those with hardcoded sprite effects, not STR files.
CURATED_SKILL_EFFECTS = {
    # Mage / Wizard
    "WZ_VERMILION": "lord",          # Lord of Vermilion
    "WZ_STORMGUST": "stormgust",
    "WZ_METEOR": "meteor3",
    "WZ_JUPITEL": "lightning",
    "WZ_HEAVENDRIVE": "crashearth",
    "WZ_QUAGMIRE": "quagmire",
    "WZ_FIREPILLAR": "firepillar",
    "MG_FIREWALL": "firewall2",
    "MG_THUNDERSTORM": "thunderstorm",
    "MG_FROSTDIVER": "icecrash",
    "MG_NAPALMBEAT": "magical",
    "MG_SAFETYWALL": "safetywall",
    "MG_STONECURSE": "stonecurse",
    # Acolyte / Priest
    "AL_HEAL": "cure",
    "AL_PNEUMA": "pneuma1",
    "PR_MAGNUS": "magnus",
    "PR_SANCTUARY": "sanctuary",
    "PR_TURNUNDEAD": "resurrection",
    "PR_MAGNIFICAT": "magnificat",
    "PR_LEXAETERNA": "lexaeterna",
    "PR_LEXDIVINA": "lexdivina",
    "PR_BENEDICTIO": "benedictio",
    "PR_KYRIE": "kyrie",
    "PR_ASPERSIO": "aspersio",
    "PR_GLORIA": "gloria",
    "PR_SUFFRAGIUM": "suffragium",
    "PR_IMPOSITIO": "impositio",
    # Swordman / Knight / Crusader
    "SM_MAGNUM": "firehit2",
    "SM_PROVOKE": "provoke",
    "KN_BRANDISHSPEAR": "brandish2",
    "KN_BOWLINGBASH": "bowling",
    "KN_SPEARBOOMERANG": "spearboomerang",
    "KN_PIERCE": "pierce",
    "KN_SPEARSTAB": "spearstab",
    "CR_SHIELDCHARGE": "shield_charge",
    "CR_HOLYCROSS": "holy_cross",
    "CR_DEVOTION": "devotion",
    "CR_PROVIDENCE": "providence",
    # Thief / Assassin
    "TF_POISON": "poison",
    "TF_DETOXIFY": "detoxication",
    "AS_SONICBLOW": "sonicblow",
    "AS_VENOMSPLASHER": "venomsplasher",
    "AS_VENOMDUST": "venomdust",
    # Merchant / Blacksmith
    "MC_CARTREVOLUTION": "cartrevolution",
    "BS_ADRENALINE": "adrenaline",
    "BS_MAXIMIZE": "maximizepower",
    "BS_WEAPONPERFECT": "weaponperfection",
    "BS_REPAIRWEAPON": "repairweapon",
    # Archer / Hunter
    "HT_BLASTMINE": "blastmine",
    "HT_CLAYMORETRAP": "claymore",
    "HT_LANDMINE": "landmine",
    "HT_SKIDTRAP": "skidtrap",
    "HT_FREEZINGTRAP": "freezing",
    "HT_SANDMAN": "sandman",
    "AL_ANGELUS": "angelus",
}

# SKID job prefixes stripped before the str-filename heuristic.
_PREFIX_RE = re.compile(r"^[A-Z0-9]{2,4}_")


def parse_effects2str() -> dict[int, str]:
    """Parse references' Effects2Str.cs into {effect_id: str_basename}.

    The C# source stores Korean .str names as cp1252 text whose bytes are
    really cp949; round-trip them back to the true file name."""
    result: dict[int, str] = {}
    if not EFFECTS2STR.exists():
        return result
    text = EFFECTS2STR.read_text(encoding="cp1252", errors="replace")
    for match in re.finditer(r'Effects\[(\d+)\]\s*=\s*"([^"]*)"', text):
        eid = int(match.group(1))
        name = match.group(2)
        if not name or name == ".str":
            continue
        if name.lower().endswith(".str"):
            name = name[:-4]
        try:  # recover Korean names mangled through cp1252
            name = name.encode("cp1252").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        result[eid] = name
    return result


def _decode_name(value) -> str:
    """Skill display names are LATAM cp1252; fall back to cp949."""
    if not isinstance(value, bytes):
        return value if value is not None else ""
    for enc in ("cp1252", "cp949"):
        try:
            return value.decode(enc)
        except UnicodeDecodeError:
            continue
    return value.decode("cp1252", "replace")


def _skill_catalog(client: str, data_root: str = "data") -> dict[str, str]:
    """Read one official skill-name table as ``SKID const -> display name``.

    Identity/job dependencies remain in the client's base Lua tree. Only
    SkillInfoList is replaced by the requested language mirror, matching the
    LATAM client's on-disk layout.
    """
    with GrfStack(client_grf_paths(client)) as grf:
        env = LuaEnv(grf)
        for lua_file in [
            "datainfo/jobidentity", "datainfo/npcidentity",
            "skillinfoz/jobinheritlist", "skillinfoz/skillid",
        ]:
            env.load(lua_file, optional=True)
        env.load("skillinfoz/skillinfolist", data_root=data_root)
        skid = env.table("SKID")
        info = env.table("SKILL_INFO_LIST")
        result: dict[str, str] = {}
        if skid is None or info is None:
            return result
        for raw_const, raw_id in skid.items():
            const = decode(raw_const)
            row = info[int(raw_id)]
            if row is None:
                continue
            name = _decode_name(row[b"SkillName"])
            if const and name:
                result[const] = name
        return result


def _skill_descriptions(client: str, data_root: str = "data") -> dict[int, list[str]]:
    """Read Gravity's authored skill-info popup lines by numeric skill id."""
    with GrfStack(client_grf_paths(client)) as grf:
        env = LuaEnv(grf)
        for lua_file in [
            "datainfo/jobidentity", "datainfo/npcidentity",
            "skillinfoz/jobinheritlist", "skillinfoz/skillid",
        ]:
            env.load(lua_file, optional=True)
        if not env.load("skillinfoz/skilldescript", optional=True,
                        data_root=data_root):
            return {}
        table = env.table("SKILL_DESCRIPT")
        if table is None:
            return {}
        result: dict[int, list[str]] = {}
        for raw_id, row in table.items():
            if not hasattr(row, "items"):
                continue
            numbered = sorted(
                ((int(index), _decode_name(text)) for index, text in row.items()
                 if isinstance(index, (int, float))),
                key=lambda pair: pair[0],
            )
            result[int(raw_id)] = [text for _, text in numbered if text]
        return result


def build_skill_translations(client: str,
                             english_client: str | None = None) -> dict[str, dict[str, str]]:
    """Build locale catalogues from the language trees shipped by Gravity.

    A LATAM install has Portuguese in the base table and a full English mirror.
    A clean KRO install has no language mirror and its patched base table is
    English. English from the same client always wins; the optional KRO client
    only fills constants absent from the LATAM overlay.
    """
    paths = client_grf_paths(client)
    with GrfStack(paths) as grf:
        english_path = (
            "data\\english\\luafiles514\\lua files\\"
            "skillinfoz\\skillinfolist.lub"
        )
        has_english_overlay = english_path in grf

    base = _skill_catalog(client)
    english: dict[str, str] = {}
    if english_client:
        english.update(_skill_catalog(english_client))
    if has_english_overlay:
        english.update(_skill_catalog(client, "data\\english"))
        return {"pt_BR": base, "en": english}

    # KRO's patched base table is the English catalogue.
    english.update(base)
    return {"en": english}


def _po_quote(value: str) -> str:
    """A JSON string literal is also a valid one-line gettext string literal."""
    return json.dumps(value, ensure_ascii=False)


def write_skill_po(locale: str, names: dict[str, str], path: Path) -> None:
    """Write a Godot-importable gettext catalogue keyed by stable SKID names."""
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: ragnadot-skills\\n"',
        f'"Language: {locale}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for const, name in sorted(names.items()):
        lines += [
            f'msgid {_po_quote("SKILL_NAME_" + const)}',
            f'msgstr {_po_quote(name)}',
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {path.name} ({path.stat().st_size:,} bytes, {len(names)} skills)")


def build(client: str) -> dict[str, dict]:
    grf = GrfStack(client_grf_paths(client))
    env = LuaEnv(grf)
    for lua_file in [
        "datainfo/jobidentity", "datainfo/npcidentity",
        "skillinfoz/jobinheritlist", "skillinfoz/skillid",
        "skillinfoz/skillinfolist", "skilleffectinfo/actorstate",
        "skilleffectinfo/effectid", "skilleffectinfo/skilleffectinfolist",
    ]:
        env.load(lua_file, optional=True)

    # --- effect basenames present in the GRF (for the heuristic) ----------
    effect_files: dict[str, str] = {}  # basename -> rel path under effect/
    for n in grf.namelist():
        if n.startswith(EFFECT_PREFIX) and n.endswith(".str"):
            rel = n[len(EFFECT_PREFIX):-4]
            base = rel.rsplit("\\", 1)[-1].lower()
            forward = rel.replace("\\", "/")
            # On a basename collision prefer the canonical top-level effect
            # (fewest path segments) over nested variants.
            existing = effect_files.get(base)
            if existing is None or forward.count("/") < existing.count("/"):
                effect_files[base] = forward

    # --- effect id list ---------------------------------------------------
    effect_ids: dict[str, dict] = {}
    for eid, base in parse_effects2str().items():
        effect_ids[str(eid)] = {"str": base.replace("\\", "/")}
    efid = env.table("EFID")
    if efid is not None:
        for k, v in efid.items():
            entry = effect_ids.setdefault(str(int(v)), {})
            entry["const"] = decode(k)

    # --- skill catalogue --------------------------------------------------
    skid = env.table("SKID")
    id_to_const = {int(v): decode(k) for k, v in skid.items()}
    info = env.table("SKILL_INFO_LIST")
    descriptions = _skill_descriptions(client)
    descriptions_en = _skill_descriptions(client, "data\\english")
    skills: dict[str, dict] = {}
    for sid, const in sorted(id_to_const.items()):
        entry: dict = {"const": const}
        row = info[sid] if info is not None else None
        if row is not None:
            entry["name"] = _decode_name(row[b"SkillName"])
            max_lv = row[b"MaxLv"]
            if max_lv is not None:
                entry["max_lv"] = int(max_lv)
        if sid in descriptions:
            entry["description"] = descriptions[sid]
        if sid in descriptions_en:
            entry["description_en"] = descriptions_en[sid]
        skills[str(sid)] = entry

    # --- skill -> effect str ---------------------------------------------
    # --- effect SPRITES, a corpus the .str passes never see ---------------
    # RO keeps animated effect sprites in `data\sprite\<effect>\`: `sight.spr` is
    # 5 frames of 64x64 and is the real MG_SIGHT flame. Missable twice over --
    # only `.str` files were ever searched, and `data\sprite\<item>\mg_sight.spr`
    # also exists but is a 24x24 skill ICON, which is what makes it look like the
    # effect is simply absent from the GRF.
    #
    # 197 sprites live there and only ~19 are named after a player skill, so this is
    # an exact, modest win rather than a broad fix. The ones it does resolve
    # (MG_SIGHT, MG_FIREBALL, AC_OWL) had a stand-in or nothing at all.
    effect_sprites: dict[str, str] = {}
    sight = next((n for n in grf.namelist() if n.lower().endswith("\\sight.spr")), None)
    if sight is not None:
        sprite_dir = sight.rsplit("\\", 1)[0].lower()
        for n in grf.namelist():
            if n.lower().startswith(sprite_dir + "\\") and n.lower().endswith(".spr"):
                base = n.rsplit("\\", 1)[-1][:-4].lower()
                # The path `sprite_export` writes it to, minus the extension.
                effect_sprites[base] = n[len(SPRITE_PREFIX):-4].replace("\\", "/")

    skill_sprites: dict[str, str] = {}
    for const in id_to_const.values():
        stem = _PREFIX_RE.sub("", const).lower()
        for key in (const.lower(), stem):
            if key and key in effect_sprites:
                skill_sprites[const] = effect_sprites[key]
                break

    skill_effects: dict[str, str] = {}

    def assign(const: str, base: str) -> None:
        base = base.lower()
        if const and base in effect_files:
            skill_effects[const] = effect_files[base]

    # 1) curated iconic skills
    for const, base in CURATED_SKILL_EFFECTS.items():
        assign(const, base)
    # 2) lua per-skill effect overrides (effectID -> EFID const -> str)
    sel = env.table("SKILL_EFFECT_INFO_LIST")
    if sel is not None:
        for sid, row in sel.items():
            const = id_to_const.get(int(sid))
            if const is None or const in skill_effects:
                continue
            eff = row[b"effectID"] if hasattr(row, "__getitem__") else None
            num = None
            if hasattr(eff, "values"):
                vals = [int(x) for x in eff.values()]
                num = vals[0] if vals else None
            if num is not None and str(num) in effect_ids:
                base = effect_ids[str(num)].get("str", "")
                if base:
                    assign(const, base.rsplit("/", 1)[-1])
    # 3) SKID-name heuristic: strip job prefix, match an str file name.
    #
    # `new_<stem>` is tried too, and it is not a curiosity: Gravity shipped the
    # third-class effects under that prefix, so the bare stem misses almost all
    # of them. `RK_IGNITIONBREAK` is `new_ignitionbreak`, `AB_ADORAMUS` is
    # `new_adoramus`, `WL_COMET` is `new_comet`. Forty-three skills resolve on
    # this alone, thirty-eight of them third-class, and they are the visible
    # offensive ones rather than passives.
    #
    # Exact basenames only, never a substring. `RK_RUNEMASTERY` substring-matches
    # `master.str`, which is a completely unrelated effect — the kind of
    # plausible-looking wrong answer this table exists to avoid.
    for const in id_to_const.values():
        if const in skill_effects:
            continue
        stem = _PREFIX_RE.sub("", const).lower()
        if not stem:
            continue
        # Underscores are dropped in the file names as often as they are kept:
        # `SO_PSYCHIC_WAVE` is `new_psychicwave`, `AG_ALL_BLOOM` is `allbloom`,
        # `CD_MEDIALE_VOTUM` is `medialevotum`. Forty more skills resolve on the
        # flattened spelling, most of the fourth-class tree among them.
        #
        # Only when the result is long enough to be a name rather than an
        # initialism: flattening a two-part const can land on a short unrelated
        # file, and a wrong mapping is worse than none because nothing
        # downstream questions it.
        candidates = [stem, "new_" + stem]
        flat = stem.replace("_", "")
        if len(flat) >= 6 and flat != stem:
            candidates += [flat, "new_" + flat]
        for candidate in candidates:
            if candidate in effect_files:
                skill_effects[const] = effect_files[candidate]
                break

    return {
        "skills": skills,
        "effect_ids": effect_ids,
        "skill_effects": skill_effects,
        "skill_effect_sprites": skill_sprites,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument(
        "--english-client",
        help=("optional clean English/KRO client; fills names missing from the "
              "main client's data\\\\english overlay"),
    )
    parser.add_argument("--out", required=True, help="Godot project root")
    args = parser.parse_args()

    out_dir = Path(args.out) / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = build(args.client)
    for name, payload in tables.items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        print(f"  {name}.json ({path.stat().st_size:,} bytes, {len(payload)} entries)")

    translations = build_skill_translations(args.client, args.english_client)
    i18n_dir = Path(args.out) / "i18n"
    for locale, names in translations.items():
        write_skill_po(locale, names, i18n_dir / f"skills.{locale}.po")

    write_skill_enum(tables["skills"], Path(args.out) / "fx" / "skill.gd")


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def write_skill_enum(skills: dict[str, dict], path: Path) -> None:
    """Emit a GDScript `Skill` class of SKID-name -> id constants so game
    code gets compile-time names + autocomplete (SkillFactory.create(
    Skill.WZ_VERMILION))."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for sid, entry in sorted(skills.items(), key=lambda kv: int(kv[0])):
        const = entry.get("const", "")
        if _IDENT_RE.match(const):
            rows.append((const, int(sid)))
    lines = [
        "class_name Skill",
        "## Auto-generated by ragnadot.skill_tables: SKID constant -> skill id.",
        "## Pass these to SkillFactory.create() / .cast().",
        "",
        "const NONE := 0",
    ]
    lines += [f"const {const} := {sid}" for const, sid in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  fx/skill.gd ({path.stat().st_size:,} bytes, {len(rows)} skills)")


if __name__ == "__main__":
    main()
