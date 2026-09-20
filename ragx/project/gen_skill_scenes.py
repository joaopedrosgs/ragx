#!/usr/bin/env python3
"""gen_skill_scenes.py — turn exported STR effects into Godot scenes.

A skill effect ships as `effects/<name>.json` + an atlas PNG, and is played at
runtime by `fx/str_effect.gd`, which walks the keyframes every frame and writes
quad corners into a sprite batch. That works, but the effect is opaque: you
cannot open it in the editor, scrub it, or see what it does without running the
game.

This bakes each effect into `skills/<name>.tscn` — one `MeshInstance3D` per STR
layer under an `AnimationPlayer` that keys all four corners independently,
visibility, tint, atlas region, and blend shader. Real Godot animations,
editable and scrubbable.

## Why this is possible at all

An STR layer is a quad whose **four corners move independently**, which a node
transform cannot express. Generated scenes therefore animate four shader
uniforms on a unit QuadMesh. Sheared and warped layers remain exact instead of
being fitted to the nearest rectangle.

## Fidelity

Poses are sampled per frame through the *same* resolution rules
`StrEffect._resolve` uses — the type-0 basis / type-1 per-frame-increment morph
model — so playback matches the runtime, not a re-interpretation of the format.
Sampled keys are then dropped wherever linear interpolation already reproduces
them within a tolerance, which is what keeps the scenes small: Godot lerps
between keys exactly as the STR model does inside a segment.

## What these are for — and what they are not

They are an **authoring and inspection** artifact: open a skill, scrub it, see
what it actually does. They are deliberately NOT wired into `SkillFactory`, and
swapping the runtime over to them would be a mistake.

`fx/str_effect.gd` plays an effect through `SpriteBatch`, a MultiMesh keyed by
(atlas page, blend mode) — so a 34-layer effect sharing one page and one mode
draws in about **one** call, and simultaneous effects share that batch with each
other and with every actor on screen. A baked scene is 34 `MeshInstance3D` nodes
with 34 unique materials: **34 draw calls**, shared with nothing. That is the same
trade as build-time atlasing versus `Atlases`' runtime LRU pages, and it comes out
the same way — the runtime already has the better mechanism (see CLAUDE.md,
"Which trees Godot imports").

Usage:
    python tools/gen_skill_scenes.py                 # every effect
    python tools/gen_skill_scenes.py lord stormgust  # just these
    python tools/gen_skill_scenes.py --out skills
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# --- must match fx/str_effect.gd -------------------------------------------
PIXEL_SIZE = 0.028         # world units per canvas pixel; ActorSprite parity
ORIGIN = (320.0, 320.0)    # canvas centre; quad coords are offsets from here
K_FRAME, K_TYPE, K_PX, K_XY = 0, 1, 2, 4
K_ANIFRAME, K_ANITYPE, K_DELAY, K_ANGLE, K_COLOR, K_BLEND = 12, 13, 14, 15, 16, 20

# How far a sampled key may sit from the straight line between its neighbours
# before it has to be kept. Generous enough to collapse long linear runs to two
# keys, tight enough that nothing visibly drifts.
EPS_POS = 0.002    # world units
EPS_ROT = 0.002    # radians
EPS_COL = 0.004    # 0..1 per channel


def _finite_float(value: object, default: float = 0.0) -> float:
    """Gravity uses NaN/Infinity for some unused STR fields."""
    number = float(value)
    return number if math.isfinite(number) else default


def _safe_int(value: float) -> int:
    return int(value) if math.isfinite(value) else 0


def _texture_frame(base: float, step: float, delay: float, delta: float,
                   animation_type: int, texture_count: int) -> int:
    """Mirror `StrTextureAnimation.frame`, including non-finite sentinels."""
    if animation_type == 1:
        return _safe_int(base + step * delta)
    if animation_type == 2:
        return min(_safe_int(base + delay * delta),
                   max(texture_count - 1, 0))
    if animation_type == 3:
        return (_safe_int(base + delay * delta) % texture_count
                if texture_count > 0 else 0)
    if animation_type == 4:
        return (_safe_int(base - delay * delta) % texture_count
                if texture_count > 0 else 0)
    if animation_type == 5:
        if texture_count <= 1:
            return 0
        cycle = (texture_count - 1) * 2
        ping_pong = _safe_int(base + delay * delta) % cycle
        return cycle - ping_pong if ping_pong >= texture_count else ping_pong
    return _safe_int(base)


def resolve(keys: list, t: float, texture_count: int = 0) -> dict | None:
    """One layer's concrete state at float frame `t`, or None when it is not yet
    active. A direct port of `StrEffect._resolve` — see that file for why the
    two keyframe types mean what they do."""
    basis_i = -1
    for i, k in enumerate(keys):
        if int(k[K_TYPE]) == 0 and float(k[K_FRAME]) <= t:
            basis_i = i
    if basis_i < 0:
        return None
    basis = keys[basis_i]
    nxt = keys[basis_i + 1] if basis_i + 1 < len(keys) else None

    xy = [_finite_float(basis[K_XY + k]) for k in range(8)]
    px, py = (_finite_float(basis[K_PX]),
              _finite_float(basis[K_PX + 1]))
    angle = _finite_float(basis[K_ANGLE])
    color = [_finite_float(basis[K_COLOR + c]) for c in range(4)]
    aniframe = _safe_int(_finite_float(basis[K_ANIFRAME]))

    if nxt is not None and int(nxt[K_TYPE]) == 1:
        d = t - float(basis[K_FRAME])
        xy = [xy[k] + _finite_float(nxt[K_XY + k]) * d
              for k in range(8)]
        px += _finite_float(nxt[K_PX]) * d
        py += _finite_float(nxt[K_PX + 1]) * d
        angle += _finite_float(nxt[K_ANGLE]) * d
        color = [color[c] + _finite_float(nxt[K_COLOR + c]) * d
                 for c in range(4)]
        anitype = int(nxt[K_ANITYPE])
        aniframe = _texture_frame(
            _finite_float(basis[K_ANIFRAME]),
            _finite_float(nxt[K_ANIFRAME]),
            _finite_float(nxt[K_DELAY]),
            d, anitype, texture_count)
    elif nxt is not None:
        span = float(nxt[K_FRAME]) - float(basis[K_FRAME])
        f = 0.0 if span <= 0 else max(0.0, min(1.0, (t - float(basis[K_FRAME])) / span))
        xy = [xy[k] + (_finite_float(nxt[K_XY + k]) - xy[k]) * f
              for k in range(8)]
        px += (_finite_float(nxt[K_PX]) - px) * f
        py += (_finite_float(nxt[K_PX + 1]) - py) * f
        angle += (_finite_float(nxt[K_ANGLE]) - angle) * f
        color = [
            color[c] + (_finite_float(nxt[K_COLOR + c]) - color[c]) * f
            for c in range(4)
        ]

    return {"xy": xy, "px": px, "py": py, "angle": angle,
            "color": color, "aniframe": aniframe, "blend": int(basis[K_BLEND])}


def pose(state: dict) -> tuple:
    """Quad corners -> (centre, z-rotation, width, height) in world units.

    The corner maths is `StrEffect._draw_layer`'s, so a scene lands where the
    runtime would draw it. Width/height come from the two edges of the quad,
    which is exact for a rectangle and a best fit for the rare sheared one.
    """
    xy, px, py = state["xy"], state["px"], state["py"]
    rad = math.radians(state["angle"])
    ca, sa = math.cos(rad), math.sin(rad)
    pts = []
    for i in range(4):
        cx, cy = xy[i], xy[i + 4]
        rx = cx * ca + cy * sa
        ry = -cx * sa + cy * ca
        pts.append(((rx + px - ORIGIN[0]) * PIXEL_SIZE,
                    -(ry + py - ORIGIN[1]) * PIXEL_SIZE))
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    ux, uy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]   # top edge
    vx, vy = pts[3][0] - pts[0][0], pts[3][1] - pts[0][1]   # left edge
    w = math.hypot(ux, uy)
    h = math.hypot(vx, vy)
    rot = math.atan2(uy, ux)
    return (cx, cy), rot, w, h


def corners(state: dict) -> tuple[tuple[float, float], ...]:
    """Resolve the exact TL/TR/BR/BL billboard-plane corners in world units."""
    xy, px, py = state["xy"], state["px"], state["py"]
    rad = math.radians(state["angle"])
    ca, sa = math.cos(rad), math.sin(rad)
    out = []
    for i in range(4):
        cx, cy = xy[i], xy[i + 4]
        rx = cx * ca + cy * sa
        ry = -cx * sa + cy * ca
        out.append(((rx + px - ORIGIN[0]) * PIXEL_SIZE,
                    -(ry + py - ORIGIN[1]) * PIXEL_SIZE))
    return tuple(out)


def simplify(times: list, values: list, close) -> tuple[list, list]:
    """Drop any key that linear interpolation between its neighbours already
    reproduces. Godot lerps between keys, and so does the STR model inside a
    segment, so this is lossless within `close`'s tolerance."""
    if len(times) <= 2:
        return times, values
    keep_t, keep_v = [times[0]], [values[0]]
    for i in range(1, len(times) - 1):
        t0, v0 = keep_t[-1], keep_v[-1]
        t2, v2 = times[i + 1], values[i + 1]
        span = t2 - t0
        f = 0.0 if span <= 0 else (times[i] - t0) / span
        if not close(v0, v2, f, values[i]):
            keep_t.append(times[i])
            keep_v.append(values[i])
    keep_t.append(times[-1])
    keep_v.append(values[-1])
    return keep_t, keep_v


def _close_vec(a, b, f, got, eps):
    return all(abs((a[i] + (b[i] - a[i]) * f) - got[i]) <= eps for i in range(len(got)))


def fmt_f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def track(idx: int, path: str, times: list, values: list, kind: str) -> str:
    """One Godot value track. `update = 0` is linear interpolation between keys;
    the region track uses `update = 1` (discrete) because a texture frame must
    switch, not fade."""
    discrete = kind in {"rect", "bool", "resource"}
    vals = ", ".join(values)
    ts = ", ".join(fmt_f(t) for t in times)
    trans = ", ".join("1" for _ in times)
    return (f'tracks/{idx}/type = "value"\n'
            f'tracks/{idx}/imported = false\n'
            f'tracks/{idx}/enabled = true\n'
            f'tracks/{idx}/path = NodePath("{path}")\n'
            f'tracks/{idx}/interp = 1\n'
            f'tracks/{idx}/loop_wrap = true\n'
            f'tracks/{idx}/keys = {{\n'
            f'"times": PackedFloat32Array({ts}),\n'
            f'"transitions": PackedFloat32Array({trans}),\n'
            f'"update": {1 if discrete else 0},\n'
            f'"values": [{vals}]\n}}\n')


def build_scene(name: str, data: dict, atlas_path: str) -> tuple[str, int, int, int]:
    """Return an exact editable STR scene and its summary counts."""
    fps = float(data.get("fps") or 60)
    max_key = int(data.get("max_key") or 0)
    # The exported key is "textures" — a page rect [x, y, w, h] per texture id.
    # (EffectDb renames it to `rects` once loaded; the file does not.)
    rects = data.get("textures") or []
    tex_w = float(data.get("w") or 1)
    tex_h = float(data.get("h") or 1)
    layers = data.get("layers") or []

    subres: list[str] = []
    nodes: list[str] = []
    tracks: list[str] = []
    ti = 0
    warped = 0
    live = 0

    for li, layer in enumerate(layers):
        keys = layer.get("keys") or []
        pool = layer.get("tex") or []
        if not keys or not pool:
            continue
        live += 1
        node = f"L{li}"

        frames = list(range(0, max_key + 1))
        samples = [(f, resolve(keys, float(f), len(pool))) for f in frames]
        active = [(f, s) for f, s in samples if s is not None]
        if not active:
            continue

        # visibility: hidden until the first basis key
        first = active[0][0]
        if first > 0:
            tracks.append(track(ti, f"{node}:visible", [0.0, first / fps],
                                ["false", "true"], "bool"))
            ti += 1

        t_corner = [[], [], [], []]
        v_corner = [[], [], [], []]
        t_col, v_col = [], []
        t_rct, v_rct = [], []
        t_blend, v_blend = [], []
        for f, st in active:
            resolved_corners = corners(st)
            for corner_index in range(4):
                t_corner[corner_index].append(f / fps)
                v_corner[corner_index].append(resolved_corners[corner_index])
            c = st["color"]
            t_col.append(f / fps)
            v_col.append((c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, c[3] / 255.0))
            idx = st["aniframe"]
            idx = idx % len(pool) if pool else 0
            g = pool[idx]
            r = rects[g] if 0 <= g < len(rects) else [0, 0, 1, 1]
            t_rct.append(f / fps); v_rct.append(tuple(r))
            t_blend.append(f / fps); v_blend.append(int(st["blend"]))
            xy = st["xy"]
            if abs((xy[0] + xy[2]) - (xy[1] + xy[3])) > 0.5 or \
               abs((xy[4] + xy[6]) - (xy[5] + xy[7])) > 0.5:
                warped += 1

        for corner_index in range(4):
            t_corner[corner_index], v_corner[corner_index] = simplify(
                t_corner[corner_index], v_corner[corner_index],
                lambda a, b, f, g: _close_vec(a, b, f, g, EPS_POS))
        t_col, v_col = simplify(t_col, v_col, lambda a, b, f, g: _close_vec(a, b, f, g, EPS_COL))
        # A region only ever switches, so collapse runs of the identical rect.
        rt, rv = [t_rct[0]], [v_rct[0]]
        for i in range(1, len(t_rct)):
            if v_rct[i] != rv[-1]:
                rt.append(t_rct[i]); rv.append(v_rct[i])
        t_rct, v_rct = rt, rv
        bt, bv = [t_blend[0]], [v_blend[0]]
        for i in range(1, len(t_blend)):
            if v_blend[i] != bv[-1]:
                bt.append(t_blend[i]); bv.append(v_blend[i])
        t_blend, v_blend = bt, bv

        corner_names = ("corner_tl", "corner_tr", "corner_br", "corner_bl")
        for corner_index, corner_name in enumerate(corner_names):
            tracks.append(track(
                ti, f"{node}:material_override:shader_parameter/{corner_name}",
                t_corner[corner_index],
                [f"Vector2({fmt_f(x)}, {fmt_f(y)})"
                 for x, y in v_corner[corner_index]], "v2"))
            ti += 1
        tracks.append(track(ti, f"{node}:material_override:shader_parameter/tint", t_col,
                            [f"Color({fmt_f(r)}, {fmt_f(g)}, {fmt_f(b)}, {fmt_f(a)})" for r, g, b, a in v_col], "col")); ti += 1
        tracks.append(track(ti, f"{node}:material_override:shader_parameter/uv_rect", t_rct,
                            [f"Vector4({fmt_f(r[0] / tex_w)}, {fmt_f(r[1] / tex_h)}, "
                             f"{fmt_f(r[2] / tex_w)}, {fmt_f(r[3] / tex_h)})"
                             for r in v_rct], "rect")); ti += 1
        if len(v_blend) > 1:
            shader_refs = (
                'ExtResource("shader_mix")',
                'ExtResource("shader_add")',
                'ExtResource("shader_premul")',
            )
            tracks.append(track(
                ti, f"{node}:material_override:shader", t_blend,
                [shader_refs[max(0, min(mode, 2))] for mode in v_blend],
                "resource"))
            ti += 1

        # One material per editable layer. The root loads the loose source atlas
        # and assigns `sheet`, avoiding an imported duplicate texture corpus.
        blend = int(active[0][1]["blend"])
        r0 = v_rct[0]
        c0 = v_col[0]
        first_corners = [values[0] for values in v_corner]
        shader_id = ("shader_mix", "shader_add", "shader_premul")[
            max(0, min(blend, 2))]
        subres.append(
            f'[sub_resource type="ShaderMaterial" id="Mat_{li}"]\n'
            f'render_priority = {min(li, 127)}\n'
            f'shader = ExtResource("{shader_id}")\n'
            f'shader_parameter/corner_tl = Vector2({fmt_f(first_corners[0][0])}, {fmt_f(first_corners[0][1])})\n'
            f'shader_parameter/corner_tr = Vector2({fmt_f(first_corners[1][0])}, {fmt_f(first_corners[1][1])})\n'
            f'shader_parameter/corner_br = Vector2({fmt_f(first_corners[2][0])}, {fmt_f(first_corners[2][1])})\n'
            f'shader_parameter/corner_bl = Vector2({fmt_f(first_corners[3][0])}, {fmt_f(first_corners[3][1])})\n'
            f'shader_parameter/uv_rect = Vector4({fmt_f(r0[0] / tex_w)}, {fmt_f(r0[1] / tex_h)}, {fmt_f(r0[2] / tex_w)}, {fmt_f(r0[3] / tex_h)})\n'
            f'shader_parameter/tint = Color({fmt_f(c0[0])}, {fmt_f(c0[1])}, {fmt_f(c0[2])}, {fmt_f(c0[3])})\n')
        nodes.append(
            f'[node name="{node}" type="MeshInstance3D" parent="."]\n'
            f'mesh = SubResource("Quad")\n'
            f'material_override = SubResource("Mat_{li}")\n'
            f'cast_shadow = 0\n'
            f'sorting_offset = {fmt_f(float(li) * 0.001)}\n')

    length = max(max_key / fps, 0.001)
    anim = (f'[sub_resource type="Animation" id="Anim"]\n'
            f'resource_name = "play"\n'
            f'length = {fmt_f(length)}\n'
            f'loop_mode = 0\n' + "".join(tracks))
    quad = '[sub_resource type="QuadMesh" id="Quad"]\nsize = Vector2(1, 1)\n'
    lib = ('[sub_resource type="AnimationLibrary" id="Lib"]\n'
           '_data = {\n"play": SubResource("Anim")\n}\n')

    steps = len(subres) + 8
    text = (f'[gd_scene load_steps={steps} format=3]\n\n'
            f'[ext_resource type="Script" path="res://fx/generated_str_scene.gd" id="scene_script"]\n'
            f'[ext_resource type="Shader" path="res://fx/shaders/generated_str_mix.gdshader" id="shader_mix"]\n'
            f'[ext_resource type="Shader" path="res://fx/shaders/generated_str_add.gdshader" id="shader_add"]\n'
            f'[ext_resource type="Shader" path="res://fx/shaders/generated_str_premul.gdshader" id="shader_premul"]\n\n'
            + quad + "\n" + "".join(s + "\n" for s in subres)
            + anim + "\n" + lib + "\n"
            f'[node name="{name}" type="Node3D"]\n'
            f'script = ExtResource("scene_script")\n'
            f'atlas_path = "{atlas_path}"\n\n'
            f'[node name="AnimationPlayer" type="AnimationPlayer" parent="."]\n'
            f'libraries = {{\n"": SubResource("Lib")\n}}\n'
            f'autoplay = "play"\n\n'
            + "\n".join(nodes)
            + '\n[connection signal="animation_finished" from="AnimationPlayer" '
              'to="." method="_on_animation_finished"]\n')
    return text, live, ti, warped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("effects", nargs="*", help="effect names (default: all)")
    ap.add_argument("--project-root", type=Path, default=ROOT,
                    help="Godot project root (default: this repository)")
    ap.add_argument("--effects-dir", type=Path, default=Path("effects"))
    ap.add_argument("--out", type=Path, default=Path("skills"))
    args = ap.parse_args()

    project_root = args.project_root.resolve()
    src: Path = args.effects_dir
    if not src.is_absolute():
        src = project_root / src
    src = src.resolve()
    if not src.is_dir():
        print(f"no effects/ at {src} — run the effect export first", file=sys.stderr)
        return 1

    manifest_path = src / ".kro-str-manifest"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_effects = manifest.get("effects", [])
        all_json = [
            src / Path(str(effect) + ".json")
            for effect in manifest_effects
        ]
    except (OSError, ValueError, AttributeError):
        all_json = sorted(src.rglob("*.json"))
    if args.effects:
        wanted = {
            effect.replace("\\", "/").removesuffix(".json").lower()
            for effect in args.effects
        }
        json_files = [
            path for path in all_json
            if path.relative_to(src).with_suffix("").as_posix().lower() in wanted
            or path.stem.lower() in wanted
        ]
    else:
        json_files = all_json

    out: Path = args.out
    if not out.is_absolute():
        out = project_root / out
    out = out.resolve()
    try:
        out.relative_to(project_root)
    except ValueError:
        print(f"--out must be inside the Godot project ({project_root})",
              file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)

    made = skipped = warped_total = 0
    biggest: list[tuple[int, str]] = []
    for jf in json_files:
        rel = jf.relative_to(src).with_suffix("")
        name = rel.as_posix()
        pf = jf.with_suffix(".png")
        if not jf.is_file() or not pf.is_file():
            skipped += 1
            continue
        data = json.loads(jf.read_bytes().decode("utf-8", "replace"))
        atlas_path = "res://" + pf.relative_to(project_root).as_posix()
        node_name = name.replace("/", "_")
        text, layers, keys, warped = build_scene(
            node_name, data, atlas_path)
        if layers == 0:
            skipped += 1
            continue
        target = out / Path(name + ".tscn")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        made += 1
        warped_total += warped
        biggest.append((target.stat().st_size, name))

    biggest.sort(reverse=True)
    if not args.effects:
        (out / ".kro-scene-manifest").write_text(
            json.dumps({
                "version": 2,
                "renderer": "exact-four-corner-shader",
                "scenes": sorted(name for _, name in biggest),
            }, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
    print(f"wrote {made} skill scene(s) -> {out}  ({skipped} skipped)")
    if biggest:
        total = sum(s for s, _ in biggest)
        print(f"  {total / 1e6:.1f} MB total, largest: "
              + ", ".join(f"{n} {s / 1e3:.0f} KB" for s, n in biggest[:3]))
    if warped_total:
        print(f"  preserved {warped_total} genuinely warped quad keyframe(s)")
    stale_tex = out / "tex"
    if stale_tex.is_dir():
        print(f"  stale pre-v2 imported atlas copies remain at {stale_tex}; "
              "they are no longer referenced and may be removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
