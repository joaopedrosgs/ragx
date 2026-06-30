"""STR (binary effect animation) parser.

An STR file describes a 2D, screen-space *effect* (skill flashes, AoE
ground decals, buff auras...). Unlike SPR/ACT it carries no image data:
each layer references external bitmaps living next to the .str inside
``data\\texture\\effect``.

An effect plays ``max_key`` integer key-frames at ``fps`` frames/second.
It is built from ``layers``; each layer is one textured quad whose four
corners, position offset, rotation, tint colour and source/destination
blend factors are driven by a list of *keyframes* (``Keyframe``):

  type 0 (BASIS)  : sets absolute values; the playback "anchor".
  type 1 (MORPH)  : per-frame *increments* added onto the preceding
                    basis frame, scaled by ``frame - basis.frame``.

This delta-accumulation model matches the official client (see
RebuildClient's RoEffectRenderer); ``bake_layer`` below resolves it to
one concrete quad per integer frame so consumers need no morph logic.

Only file version 148 ("STRM") exists in the LATAM client; the layout is
taken from korangar's ragnarok-formats/effect.rs and validated against
every STR in the GRFs (scripts/validate_parsers.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .binio import Reader

SIGNATURE = b"STRM"
SUPPORTED_VERSIONS = (148,)
# The classic effect canvas centre; quad coords are offsets from here.
EFFECT_ORIGIN = (320.0, 320.0)


@dataclass(slots=True)
class Keyframe:
    frame: int  # timeline position, in frames
    type: int  # 0 = basis (absolute), 1 = morph (per-frame delta)
    position: tuple[float, float]  # layer centre offset on the effect canvas
    uv: tuple[float, ...]  # 8 floats; rarely used, corners are in xy
    xy: tuple[float, ...]  # 8 floats: x0..x3 then y0..y3 of the quad corners
    texture_index: float  # which texture in the layer pool (Aniframe)
    animation_type: int  # texture-advance mode for the following morph span
    delay: float  # texture-advance rate for animation_type 2/3/4
    angle: float  # degrees, clockwise
    color: tuple[float, float, float, float]  # RGBA, 0..255
    src_blend: int  # D3DBLEND source factor
    dst_blend: int  # D3DBLEND destination factor
    mt_present: int


@dataclass(slots=True)
class Layer:
    texture_names: list[str]  # bitmap file names, relative to the .str folder
    keyframes: list[Keyframe]


@dataclass(slots=True)
class Str:
    version: int
    fps: int
    max_key: int  # total number of frames in the animation
    layers: list[Layer]
    leftover: int = 0  # trailing bytes after the last layer (should be 0)


# Godot-friendly blend families that an effect material can express.
BLEND_MIX = 0  # standard alpha / opaque
BLEND_ADD = 1  # additive glow (the common RO effect look)
BLEND_PREMUL = 2  # premultiplied-alpha additive


def classify_blend(src: int, dst: int) -> int:
    """Map a D3DBLEND (src, dst) pair to a BLEND_* family."""
    # SrcAlpha / InvSrcAlpha and One / Zero are ordinary compositing.
    if (src, dst) in {(5, 6), (2, 1), (3, 9)}:
        return BLEND_MIX
    # SrcAlpha / DstAlpha is the classic glow; treat as premultiplied add
    # so translucent pixels brighten without washing out (RebuildClient
    # special-cases this exact pair the same way).
    if (src, dst) == (5, 7):
        return BLEND_PREMUL
    # Everything else (One/One, SrcAlpha/SrcAlpha, ...) reads as additive.
    return BLEND_ADD


@dataclass(slots=True)
class BakedFrame:
    """One layer resolved at one integer frame, ready to draw."""

    texture_index: int
    # Quad corners in canvas pixels (offset from EFFECT_ORIGIN), y-down,
    # ordered TL, TR, BR, BL after the canonical xy corner remap.
    corners: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]
    color: tuple[float, float, float, float]  # RGBA 0..255
    blend: int  # BLEND_* family


# A handful of official files carry a garbage max_key (e.g. toto.str has
# 1.8 billion); real effects top out well under this. Clamp allocations.
MAX_REASONABLE_FRAMES = 4096


def frame_count(st: Str) -> int:
    """Trustworthy frame count: the header's max_key, but never beyond the
    last keyframe (+1) nor an absurd cap. Guards against corrupt headers."""
    last = 0
    for layer in st.layers:
        for kf in layer.keyframes:
            if 0 <= kf.frame < MAX_REASONABLE_FRAMES:
                last = max(last, kf.frame)
    header = st.max_key if 0 <= st.max_key < MAX_REASONABLE_FRAMES else 0
    return max(header, last + 1) if (header or last) else 0


def parse(data: bytes) -> Str:
    r = Reader(data)
    sig = r.bytes(4)
    if sig != SIGNATURE:
        raise ValueError(f"bad STR signature {sig!r}")
    version = r.i32()
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported STR version {version}")
    fps = r.i32()
    max_key = r.i32()
    layer_count = r.i32()
    r.skip(16)  # reserved: display/group/type, always zero in this client

    layers: list[Layer] = []
    for _ in range(layer_count):
        texture_count = r.i32()
        texture_names = [r.fixed_string(128) for _ in range(texture_count)]
        keyframe_count = r.i32()
        keyframes = [_read_keyframe(r) for _ in range(keyframe_count)]
        layers.append(Layer(texture_names=texture_names, keyframes=keyframes))

    return Str(
        version=version,
        fps=fps,
        max_key=max_key,
        layers=layers,
        leftover=r.remaining(),
    )


def _read_keyframe(r: Reader) -> Keyframe:
    frame = r.i32()
    ktype = r.i32()
    position = r.vec2()
    uv = r.f32s(8)
    xy = r.f32s(8)
    texture_index = r.f32()
    animation_type = r.i32()
    delay = r.f32()
    angle = r.f32() / (1024.0 / 360.0)  # client stores angle * 1024/360
    color = r.f32s(4)
    src_blend = r.i32()
    dst_blend = r.i32()
    mt_present = r.i32()
    return Keyframe(
        frame=frame,
        type=ktype,
        position=position,
        uv=uv,
        xy=xy,
        texture_index=texture_index,
        animation_type=animation_type,
        delay=delay,
        angle=angle,
        color=color,  # type: ignore[arg-type]
        src_blend=src_blend,
        dst_blend=dst_blend,
        mt_present=mt_present,
    )


def _corners(xy: tuple[float, ...], angle_deg: float, position: tuple[float, float]):
    """Resolve the four quad corners to canvas pixels (offset from origin).

    Corner source order in the file is x0..x3,y0..y3, already laid out as
    TL, TR, BR, BL (korangar effect.rs). Each corner is rotated about the
    layer centre (clockwise ``angle_deg``) then translated by ``position``;
    finally we subtract EFFECT_ORIGIN so (0, 0) is centre.
    """
    order = (0, 1, 2, 3)
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    out = []
    for i in order:
        cx, cy = xy[i], xy[i + 4]
        # clockwise rotation in a y-down space
        rx = cx * cos_a + cy * sin_a
        ry = -cx * sin_a + cy * cos_a
        out.append(
            (
                rx + position[0] - EFFECT_ORIGIN[0],
                ry + position[1] - EFFECT_ORIGIN[1],
            )
        )
    return (out[0], out[1], out[2], out[3])


def bake_layer(layer: Layer, max_key: int) -> list[BakedFrame | None]:
    """Resolve a layer to one ``BakedFrame`` per integer frame (or None).

    Implements the client's delta-accumulation morph model: at frame ``f``
    we take the most recent type-0 (basis) keyframe and the most recent
    type-1 (morph) keyframe before ``f``; morph fields are *increments*
    multiplied by ``delta = f - basis.frame`` and added to the basis.
    """
    frames = layer.keyframes
    n_tex = len(layer.texture_names)
    result: list[BakedFrame | None] = [None] * max_key

    # Resolve "inherit previous" blend factors (a 0 means "same as the last
    # keyframe that set one"), then classify each keyframe's blend family.
    resolved_blend: list[int] = []
    last_src, last_dst = 5, 6
    for kf in frames:
        src = kf.src_blend if kf.src_blend != 0 else last_src
        dst = kf.dst_blend if kf.dst_blend != 0 else last_dst
        last_src, last_dst = src, dst
        resolved_blend.append(classify_blend(src, dst))

    for f in range(max_key):
        basis_i = -1
        morph_i = -1
        for i, kf in enumerate(frames):
            if kf.frame <= f:
                if kf.type == 0:
                    basis_i = i
                elif kf.type == 1:
                    morph_i = i
        if basis_i < 0:
            continue
        basis = frames[basis_i]

        # No active morph span: hold the basis frame.
        if morph_i < 0 or morph_i <= basis_i:
            corners = _corners(basis.xy, basis.angle, basis.position)
            tex = _safe_int(basis.texture_index)
            color = basis.color
        else:
            morph = frames[morph_i]
            delta = f - basis.frame
            xy = tuple(basis.xy[k] + morph.xy[k] * delta for k in range(8))
            pos = (
                basis.position[0] + morph.position[0] * delta,
                basis.position[1] + morph.position[1] * delta,
            )
            angle = basis.angle + morph.angle * delta
            color = tuple(
                basis.color[k] + morph.color[k] * delta for k in range(4)
            )  # type: ignore[assignment]
            corners = _corners(xy, angle, pos)
            tex = _advance_texture(basis, morph, delta, n_tex)

        if n_tex == 0:
            continue
        tex = max(0, min(tex, n_tex - 1))
        result[f] = BakedFrame(
            texture_index=tex,
            corners=corners,
            color=tuple(max(0.0, min(255.0, c)) for c in color),  # type: ignore[arg-type]
            blend=resolved_blend[basis_i],
        )
    return result


def _safe_int(value: float, default: int = 0) -> int:
    if not math.isfinite(value):
        return default
    return int(value)


def _advance_texture(basis: Keyframe, morph: Keyframe, delta: int, n_tex: int) -> int:
    """Texture index for a morph span, per the client's animation_type."""
    at = morph.animation_type
    base = basis.texture_index
    if at == 1:
        return _safe_int(base + morph.texture_index * delta)
    if at == 2:
        return _safe_int(min(base + morph.delay * delta, n_tex - 1))
    if at == 3:
        return _safe_int((base + morph.delay * delta) % n_tex) if n_tex else 0
    if at == 4:
        return _safe_int((base - morph.delay * delta) % n_tex) if n_tex else 0
    return _safe_int(base)
