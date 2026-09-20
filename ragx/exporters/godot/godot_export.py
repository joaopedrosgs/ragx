"""Export maps as Godot 4 scenes composed of individual model glTFs.

Layout produced inside ``converted_assets/`` (which doubles as a Godot
project — just open it with Godot 4.6):

    project.godot
    autoplay_all.gd          plays every AnimationPlayer, looping
    textures/                shared PNGs (written by ragnadot.convert)
    models/<path>.gltf+bin   one glTF per unique RSM model (+ animation)
    terrains/<map>.gltf+bin  terrain + water mesh per map
    maps/<map>.tscn          THE map: terrain and water, the RSW sun/ambient and
                             its light sources, and one node per prop instancing
                             a shared external model glTF.

Every map prop is an individual node instancing the shared model scene;
nothing is merged. Models whose RSW instance uses a non-default
animation speed get a speed-suffixed variant file (``...@s0.030.gltf``).
Instances with ``animation_type = 0`` are marked ``no_anim`` and stay
static, like the original client.

A map is a SCENE plus glTFs -- there is no sidecar JSON. There used to be a
`maps/<map>.json` manifest, so a client could instance props lazily by proximity;
it and the streaming it fed were both removed (2026-07-24) because the scene IS
the map, and a second description of the same world only has to be kept in step
with the first. Godot's frustum culling does what the streaming did.

The manifest-building code outlived the manifest and was still computing a
bounding sphere for every prop -- 1300 of them on prontera -- to throw the result
away; removed 2026-07-25.

Usage:
    python -m ragnadot.godot_export                  # all maps
    python -m ragnadot.godot_export prontera payon   # specific maps
    python -m ragnadot.godot_export --processes 8
Then open converted_assets/ in Godot (first import takes a while), or
pre-import headless:
    <godot> --headless --path converted_assets --import
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from ragx import mathutil as mu
from ragx.formats import rsw as rsw_format
from ragx.grf import normalize_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROJECT_GODOT = """\
; ragnadot - Ragnarok Online maps as Godot scenes.
config_version=5

[application]
config/name="ragnadot maps"
config/features=PackedStringArray("4.6")
"""

AUTOPLAY_GD = """\
# Attached to every map root:
# - starts all imported glTF animations, looping (subtrees marked
#   metadata/no_anim stay static — props with RSW animation_type 0)
# - applies the animated water shader with the map's original wave and
#   texture-cycling parameters (root metadata/water)
# - bakes the navigation region from the walkable GAT mesh
extends Node3D


func _ready() -> void:
	_play_all(self)
	_setup_water()


func _play_all(node: Node) -> void:
	if node.has_meta("no_anim"):
		return
	if node is AnimationPlayer:
		var names := (node as AnimationPlayer).get_animation_list()
		if names.size() > 0:
			var animation := (node as AnimationPlayer).get_animation(names[0])
			animation.loop_mode = Animation.LOOP_LINEAR
			(node as AnimationPlayer).play(names[0])
	for child in node.get_children():
		_play_all(child)


func _setup_water() -> void:
	if not has_meta("water"):
		return
	var water: Dictionary = get_meta("water")
	var water_root := get_node_or_null("Water")
	if water_root == null:
		return

	var frames: Array = water.get("frames", [])
	var images: Array[Image] = []
	for path in frames:
		var texture: Texture2D = load(path)
		if texture == null:
			continue
		var image := texture.get_image()
		if image.is_compressed():
			image.decompress()
		image.convert(Image.FORMAT_RGBA8)
		image.generate_mipmaps()
		images.append(image)
	if images.is_empty():
		return
	var array := Texture2DArray.new()
	array.create_from_images(images)

	var material := ShaderMaterial.new()
	material.shader = load("res://water.gdshader")
	material.set_shader_parameter("frames", array)
	material.set_shader_parameter("frame_count", float(images.size()))
	material.set_shader_parameter("cycle_fps", 60.0 / float(water.get("cycleInterval", 3)))
	material.set_shader_parameter("alpha", float(water.get("opacity", 0.5647)))
	material.set_shader_parameter("wave_height", float(water.get("waveHeight", 1.0)))
	material.set_shader_parameter("wave_speed", float(water.get("waveSpeed", 2.0)))
	material.set_shader_parameter("wave_pitch", float(water.get("wavePitch", 50.0)))

	var stack: Array[Node] = [water_root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is MeshInstance3D:
			(node as MeshInstance3D).material_override = material
		for child in node.get_children():
			stack.append(child)

"""

WATER_GDSHADER = """\
// Ragnarok Online water: 32-frame texture cycling plus the RSW wave
// animation (vertex displacement along the south-west -> north-east
// diagonal). Parameters come straight from the map files.
shader_type spatial;
render_mode cull_disabled, specular_disabled;

uniform sampler2DArray frames : source_color, filter_linear_mipmap, repeat_enable;
uniform float frame_count = 32.0;
uniform float cycle_fps = 20.0;       // 60 / textureCyclingInterval
uniform float alpha : hint_range(0.0, 1.0) = 0.5647;
uniform float wave_height = 1.0;      // world units
uniform float wave_speed = 2.0;       // degrees per frame at 60 fps
uniform float wave_pitch = 50.0;      // degrees per GAT tile (5 units)

varying vec3 world_position;

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	float phase = radians(wave_speed) * 60.0 * TIME
		+ radians(wave_pitch) * (world_position.x + world_position.z) / 5.0;
	VERTEX.y += wave_height * sin(phase);
}

void fragment() {
	float frame = mod(floor(TIME * cycle_fps), frame_count);
	vec4 color = texture(frames, vec3(UV, frame));
	ALBEDO = color.rgb;
	ALPHA = alpha;
}
"""

KEEP_IMPORT = "[remap]\n\nimporter=\"keep\"\n"

_INVALID_NODE_CHARS = re.compile(r'[./:@%"\\]')


def _node_name(base: str, used: set[str]) -> str:
    name = _INVALID_NODE_CHARS.sub("_", base).strip() or "node"
    candidate = name
    counter = 2
    while candidate in used:
        candidate = f"{name}{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _fmt(value: float) -> str:
    text = f"{value:.6g}"
    return "0" if text in ("-0", "-0.0") else text


def _transform3d(matrix: mu.Matrix, scale: tuple[float, float, float],
                 origin: tuple[float, float, float]) -> str:
    """Serialize basis = R*S plus origin in .tscn order (basis rows)."""
    values = []
    for row in range(3):
        for col in range(3):
            values.append(matrix[row * 4 + col] * scale[col])
    values.extend(origin)
    return "Transform3D(" + ", ".join(_fmt(v) for v in values) + ")"


def _sun_matrix(light: rsw_format.LightSettings) -> mu.Matrix:
    # Same math as the monolithic gltf export: direction-to-sun, then a
    # basis whose -Z is the light travel direction.
    to_sun = mu.transform_direction(
        mu.mat_mul(mu.rot_y(math.radians(light.longitude)),
                   mu.rot_x(math.radians(-light.latitude))),
        (0.0, 1.0, 0.0),
    )
    travel = mu.normalize(mu.mirror_z_point((-to_sun[0], -to_sun[1], -to_sun[2])))
    z_axis = (-travel[0], -travel[1], -travel[2])
    x_axis = mu.cross((0.0, 1.0, 0.0), z_axis)
    if sum(c * c for c in x_axis) < 1e-8:
        x_axis = (1.0, 0.0, 0.0)
    x_axis = mu.normalize(x_axis)
    y_axis = mu.cross(z_axis, x_axis)
    return (
        x_axis[0], y_axis[0], z_axis[0], 0.0,
        x_axis[1], y_axis[1], z_axis[1], 0.0,
        x_axis[2], y_axis[2], z_axis[2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


SUN_ORIGIN = mu.scaled((0.0, 300.0, 0.0))


def _sun_transform(light: rsw_format.LightSettings) -> str:
    return _transform3d(_sun_matrix(light), (1.0, 1.0, 1.0), SUN_ORIGIN)


def _quat_trs(translation, rotation, scale) -> np.ndarray:
    """A node's local 4x4 from its glTF TRS (rotation is a quaternion xyzw)."""
    x, y, z, w = rotation
    basis = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)
    matrix = np.eye(4)
    matrix[:3, :3] = basis * np.asarray(scale, dtype=np.float64)  # columns scaled
    matrix[:3, 3] = translation
    return matrix


## RSW light sources (object_type 2) exported per map, and how bright to make them.
## The cap exists only to stop a pathological map emitting thousands; it is set
## above the busiest real map measured (lhz_dun01, 270) so nothing authentic is
## truncated. These are unshadowed and distance-faded, and Godot's clustered
## lighting handles a few hundred of those cheaply.
##
## The energy is tuned, not guessed: the RSW colours are deliberately dim and warm
## (0.2-0.5, torch-coloured), so 0.6 was invisible against ambient 0.35 + sun 0.9,
## and 8.0 blew the floor out. 2.0 reads as a lamp pool without washing the art.
MAX_MAP_LIGHTS = 512
MAP_LIGHT_ENERGY = 2.0
## Effect emitters per map. The densest map in the game (in_sphinx1) declares 179,
## so this is headroom rather than a real limit; it exists so a malformed RSW cannot
## write a million-node scene.
MAX_MAP_EFFECTS = 512

## RSW effect type -> the flame frames it burns with, as `<stem>NN.bmp` under
## ``data\texture\effect\``. The map scenes reference the composed strip directly, so
## this is what decides which emitters become fire.
##
## The ids are the client's own internal effect table, compiled into its executable
## and NOT in the GRF: `skilleffectinfo/effectid.lub`'s EFID is a different id space
## (121 entries, nothing in 42..50) and `effecttool/<map>.lub` covers 93 maps but not
## the sphinx. So they are identified one at a time by looking at what stands where
## they sit. 47 is 922 of the 1527 emitters across every exported map, and is
## unmistakably the sphinx/payon brazier. 44 (town street lamps), 45 (gef_dun01),
## 974, 324 and the rest are left alone rather than guessed at.
EFFECT_FLAMES = {
    47: {
        "frames": "torch_red",
        "count": 13,
        "size": 0.8,        ## quad side, metres
        "amount": 12,       ## particles per emitter
        "lifetime": 0.7,
        "rise": 0.4,        ## metres/sec upward
        ## Height above the GROUND, because the RSW leaves effect Y at zero (all 179
        ## of in_sphinx1's are exactly 0.0) -- the height is the effect's own, not the
        ## map's. 1.85 clears the brazier's rim: at rim height RO's ~55-degree camera
        ## looks into the cup and the near rim hides the fire burning in it.
        "lift": 1.85,
    },
}
FLAME_SHADER = "res://fx/shaders/map_flame.gdshader"


def _gat_heights(builder, map_name: str, gat_parse=None):
    """(width, height, mean-corner-height per cell) for the map's GAT, or None.

    Needed because the RSW gives an effect no usable Y, so the emitter has to be put
    on the ground the player walks on -- the same table `nav/<map>_gat.bin` carries.
    """
    if gat_parse is None:
        from ragx.formats import gat as gat_format
        gat_parse = gat_format.parse

    raw = builder.source.try_read(f"data\\{map_name}.gat")
    if raw is None:
        return None
    gat = gat_parse(raw)
    means = [(t[0] + t[1] + t[2] + t[3]) * 0.25 for t in gat.tiles]
    return (gat.width, gat.height, means)


def _ground_at(heights, px: float, pz: float) -> float:
    """World-space Y of the floor under an RSW position, in Godot units.

    GAT tiles are 5 RO units and the grid is centred on the origin, the same mapping
    the terrain mesh uses. Y is negated because RO's vertical axis points down.
    """
    if heights is None:
        return 0.0
    width, height, means = heights
    tx = int((px + width * 2.5) / 5.0)
    tz = int((pz + height * 2.5) / 5.0)
    if tx < 0 or tz < 0 or tx >= width or tz >= height:
        return 0.0
    return -means[tz * width + tx] * mu.WORLD_SCALE


def _write_flame_strip(builder, stem: str, count: int, assets_dir: Path) -> str | None:
    """Compose ``<stem>01..NN.bmp`` into one horizontal strip PNG under particles/.

    A strip because that is what a frame-animated particle material samples: one
    texture, one column per frame. Written as a real imported asset rather than
    assembled at runtime so the map scene can reference it like any other resource --
    which is what lets the fire show up when you just OPEN the scene.

    RO's effect bitmaps are 24-bit and key on magenta, the same rule
    `effect_texture_export` uses; the frames are black-background additive art, so
    the keyed background and the black both add nothing under additive blending.
    """
    from PIL import Image
    import numpy as np

    out_dir = assets_dir / "particles"
    dest = out_dir / f"{stem}.png"

    frames = []
    for i in range(1, count + 1):
        texture = builder._load_texture(f"effect\\{stem}{i:02d}.bmp")
        if texture is None:
            continue
        try:
            img = Image.open(io.BytesIO(texture.png))
            img.load()
        except Exception:  # noqa: BLE001
            continue
        frames.append(img.convert("RGB"))
    if not frames:
        return None

    w, h = frames[0].size
    strip = Image.new("RGB", (w * len(frames), h), (0, 0, 0))
    for i, f in enumerate(frames):
        if f.size != (w, h):
            f = f.resize((w, h))
        strip.paste(f.convert("RGB"), (i * w, 0))

    out_dir.mkdir(parents=True, exist_ok=True)
    _stable_png(dest, strip)
    return f"particles/{stem}.png"


def _stable_bytes(path: Path, data: bytes) -> None:
    # ragx owns stable publication and records outputs for incremental builds.
    from ragx.incremental import write_bytes
    write_bytes(path, data)


def _stable_png(path: Path, image) -> None:
    from ragx.incremental import save_png
    save_png(path, image)


def export_map(builder, map_name: str, assets_dir: Path,
               rsw_parse=None, gat_parse=None) -> str:
    """Build terrains/<map>.gltf, required models/*.gltf and
    maps/<map>.tscn. Returns a summary string."""

    if rsw_parse is None:
        rsw_parse = rsw_format.parse
    rsw = rsw_parse(builder.source.read(f"data\\{map_name}.rsw"))

    terrain_rel = f"terrains/{map_name}.gltf"
    terrain_path = assets_dir / terrain_rel
    terrain_path.parent.mkdir(exist_ok=True)
    stats = builder.build_terrain_gltf(map_name, terrain_path, uri_base="../",
                                       include_water=False)

    # Water in its own file: the map script drives it with the original
    # wave/cycling parameters through water.gdshader.
    water_rel = f"terrains/{map_name}_water.gltf"
    water_info = builder.build_water_gltf(map_name, assets_dir / water_rel, uri_base="../")

    # No navigation mesh. It existed to bake a NavigationRegion3D into the scene,
    # but nothing walks on it — the player moves on the GAT grid (world/map_grid.gd,
    # fed by nav/<map>_gat.bin below) and every other unit is server-driven. Baking
    # it cost a recast pass on every map entry and 200 MB of _nav glTF across the
    # exported set, for a region no code ever queried.

    _write_gat_png(builder, map_name, assets_dir, gat_parse)
    _write_gat_bin(builder, map_name, assets_dir, gat_parse)
    _write_minimap(builder, map_name, assets_dir)

    # ---- model files (deduplicated on disk) -------------------------
    ext_resources: list[tuple[str, str]] = [
        ("Script", "res://autoplay_all.gd"),
        ("PackedScene", f"res://{terrain_rel}"),
    ]
    water_ext = -1
    if water_info is not None:
        water_ext = len(ext_resources)
        ext_resources.append(("PackedScene", f"res://{water_rel}"))
    ext_ids: dict[str, int] = {}
    instances: list[dict] = []
    missing = 0

    for instance in rsw.models:
        template = builder._load_template(instance.model_name, stats)
        if template is None:
            missing += 1
            continue

        animate = instance.animation_type != 0 and not template.is_static
        speed = instance.animation_speed if instance.animation_speed and instance.animation_speed > 0 else 1.0
        effective_speed = max(speed, 0.03)

        sx, sy, sz = instance.scale
        scale = (sx, sy, sz)
        # Mirrored placements flip the winding; those instances use a
        # variant model with pre-reversed triangles so lighting matches.
        mirrored = (sx * sy * sz) < 0

        key = normalize_path(instance.model_name).replace("\\", "/")
        suffix = ""
        if not template.is_static and effective_speed != 1.0:
            suffix += f"@s{effective_speed:.3f}"
        if mirrored:
            suffix += "@mirror"
        model_rel = f"models/{key}{suffix}.gltf"
        model_path = assets_dir / model_rel
        if f"res://{model_rel}" not in ext_ids and (
                getattr(builder, 'build_cache', None) is not None or not model_path.exists()):
            model_path.parent.mkdir(parents=True, exist_ok=True)
            depth = model_rel.count("/")  # "models/a/b.gltf" -> climb depth dirs
            builder.build_model_gltf(instance.model_name, model_path,
                                     uri_base="../" * depth,
                                     effective_speed=effective_speed,
                                     flip_winding=mirrored)

        resource = f"res://{model_rel}"
        if resource not in ext_ids:
            ext_ids[resource] = len(ext_resources)
            ext_resources.append(("PackedScene", resource))


        px, py, pz = instance.position
        rx, ry, rz = (math.radians(a) for a in instance.rotation)
        rotation = mu.mirror_z_matrix(mu.euler_rotation_matrix_zxy(rx, ry, rz))
        origin = mu.scaled((px, -py, -pz))
        no_anim = (not animate) and (not template.is_static)

        instances.append({
            "name": instance.name or Path(key).stem,
            "ext": ext_ids[resource],
            "transform": _transform3d(rotation, scale, origin),
            "no_anim": no_anim,
        })

    # ---- flame emitters: real nodes, not markers ----------------------
    # The scene carries the particles themselves, so opening a map in the editor
    # shows its fire. Doing it in client code instead meant the .tscn held only
    # positions and the fire appeared solely when something loaded the map through
    # `MapLoader` -- which is exactly the "I opened the scene and there was no fire"
    # trap, and it broke the project's own rule that opening a map and running it are
    # the same thing.
    #
    # One node per emitter rather than one system for all of them. That is more draw
    # calls, but each node frustum-culls on its own (a map-wide emitter never culls,
    # so it simulates every particle on every frame wherever you stand), and it is
    # what makes them editable: select a torch, change its flame.
    flame_groups: dict[int, list] = {}
    for effect in rsw.effects[:MAX_MAP_EFFECTS]:
        if effect.effect_type in EFFECT_FLAMES:
            flame_groups.setdefault(effect.effect_type, []).append(effect)

    flame_res: dict[int, dict] = {}
    if flame_groups:
        heights = _gat_heights(builder, map_name, gat_parse)
        shader_ext = len(ext_resources)
        ext_resources.append(("Shader", FLAME_SHADER))
        for etype in sorted(flame_groups):
            preset = EFFECT_FLAMES[etype]
            strip_rel = _write_flame_strip(builder, preset["frames"], preset["count"],
                                           assets_dir)
            if strip_rel is None:
                del flame_groups[etype]
                continue
            strip_ext = len(ext_resources)
            ext_resources.append(("Texture2D", f"res://{strip_rel}"))
            flame_res[etype] = {
                "shader_ext": shader_ext,
                "strip_ext": strip_ext,
                "preset": preset,
                "heights": heights,
            }

    # ---- write the .tscn ---------------------------------------------
    ambient = rsw.light.ambient
    diffuse = rsw.light.diffuse
    sub_resource_count = 1 + 3 * len(flame_res)
    lines: list[str] = []
    lines.append(f"[gd_scene load_steps={len(ext_resources) + sub_resource_count + 1} format=3]")
    lines.append("")
    for index, (rtype, rpath) in enumerate(ext_resources):
        lines.append(f'[ext_resource type="{rtype}" path="{rpath}" id="{index + 1}"]')
    lines.append("")
    lines.append('[sub_resource type="Environment" id="env"]')
    lines.append("background_mode = 1")
    lines.append("background_color = Color(0.05, 0.05, 0.08, 1)")
    lines.append("ambient_light_source = 2")
    # RSW lighting is authored for RO's original renderer — near-1.0 ambient plus
    # a bright sun — which through Godot's tonemapper and RO's telephoto camera
    # clips lit terrain to near-white. The client used to correct this after
    # loading; now that the scene IS what loads, it has to be right in the file.
    lines.append("ambient_light_energy = 0.35")
    lines.append(f"ambient_light_color = Color({_fmt(ambient[0])}, {_fmt(ambient[1])}, {_fmt(ambient[2])}, 1)")

    # One set of flame resources per type, shared by every emitter of that type --
    # 179 torches reference the same material, mesh and process material.
    for etype in sorted(flame_res):
        info = flame_res[etype]
        preset = info["preset"]
        size = float(preset["size"])
        lines.append("")
        lines.append(f'[sub_resource type="ShaderMaterial" id="flame_mat_{etype}"]')
        lines.append(f'shader = ExtResource("{info["shader_ext"] + 1}")')
        lines.append(f'shader_parameter/strip = ExtResource("{info["strip_ext"] + 1}")')
        lines.append(f'shader_parameter/frames = {preset["count"]}')
        lines.append("")
        lines.append(f'[sub_resource type="QuadMesh" id="flame_mesh_{etype}"]')
        lines.append(f'material = SubResource("flame_mat_{etype}")')
        lines.append(f"size = Vector2({_fmt(size)}, {_fmt(size)})")
        # Anchor the quad's BOTTOM at the particle, so the flame stands on the
        # emitter instead of straddling it and sinking half of itself into the bowl.
        lines.append(f"center_offset = Vector3(0, {_fmt(size * 0.5)}, 0)")
        lines.append("")
        lines.append(f'[sub_resource type="ParticleProcessMaterial" id="flame_proc_{etype}"]')
        lines.append("direction = Vector3(0, 1, 0)")
        lines.append("spread = 8.0")
        lines.append(f"initial_velocity_min = {_fmt(float(preset['rise']) * 0.6)}")
        lines.append(f"initial_velocity_max = {_fmt(float(preset['rise']))}")
        lines.append("gravity = Vector3(0, 0, 0)")   # fire rises on its velocity alone
        lines.append("scale_min = 0.6")
        # A random start frame per particle, or every torch flickers in lockstep and
        # the whole room strobes.
        lines.append("anim_speed_min = 1.0")
        lines.append("anim_speed_max = 1.0")
        lines.append("anim_offset_max = 1.0")

    lines.append("")
    used_names: set[str] = set()
    root_name = _node_name(map_name, used_names)
    lines.append(f'[node name="{root_name}" type="Node3D"]')
    lines.append('script = ExtResource("1")')
    if water_info is not None:
        frames = ", ".join(f'"res://textures/{uri}"' for uri in water_info["frames"])
        lines.append(
            "metadata/water = {"
            f'"cycleInterval": {water_info["cycleInterval"]}, '
            f'"frames": [{frames}], '
            f'"opacity": {_fmt(water_info["opacity"])}, '
            f'"waveHeight": {_fmt(water_info["waveHeight"])}, '
            f'"wavePitch": {_fmt(water_info["wavePitch"])}, '
            f'"waveSpeed": {_fmt(water_info["waveSpeed"])}'
            "}")
    lines.append("")
    lines.append('[node name="Terrain" parent="." instance=ExtResource("2")]')
    used_names.add("Terrain")
    if water_info is not None:
        lines.append("")
        lines.append(f'[node name="Water" parent="." instance=ExtResource("{water_ext + 1}")]')
        used_names.add("Water")
    lines.append("")
    lines.append('[node name="Sun" type="DirectionalLight3D" parent="."]')
    lines.append(f"transform = {_sun_transform(rsw.light)}")
    lines.append(f"light_color = Color({_fmt(min(diffuse[0], 1.0))}, {_fmt(min(diffuse[1], 1.0))}, {_fmt(min(diffuse[2], 1.0))}, 1)")
    lines.append("light_energy = 0.9")
    lines.append("shadow_enabled = true")
    used_names.add("Sun")
    lines.append("")
    lines.append('[node name="WorldEnvironment" type="WorldEnvironment" parent="."]')
    lines.append('environment = SubResource("env")')
    used_names.add("WorldEnvironment")

    # RSW light sources -- the map's lamps, torches and braziers. The parser has
    # always read these (object_type 2) and the export dropped them, so every map
    # was lit by its sun and ambient alone.
    #
    # Positions convert exactly like a prop's: (px, -py, -pz).
    #
    # Colour and range are written as the RSW states them, because they are map
    # DATA. How bright a lamp should be, and whether it casts a shadow, is a look
    # decision and lives in `MapLoader._apply_light_policy` -- the RSW's colours are
    # dim by authoring convention (0.3, 0.2, 0.1 is typical) and need normalising
    # before Godot shows them at all.
    for light in rsw.lights[:MAX_MAP_LIGHTS]:
        lx, ly, lz = light.position
        name = _node_name(light.name or "Light", used_names)
        lines.append("")
        lines.append(f'[node name="{name}" type="OmniLight3D" parent="."]')
        # A point light needs no basis, so write identity rather than build one.
        lox, loy, loz = mu.scaled((lx, -ly, -lz))
        lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, %s, %s, %s)"
                     % (_fmt(lox), _fmt(loy), _fmt(loz)))
        cr, cg, cb = light.color
        lines.append("light_color = Color(%s, %s, %s, 1)"
                     % (_fmt(min(cr, 1.0)), _fmt(min(cg, 1.0)), _fmt(min(cb, 1.0))))
        lines.append(f"light_energy = {_fmt(MAP_LIGHT_ENERGY)}")
        lines.append(f"omni_range = {_fmt(max(light.range, 1.0) * mu.WORLD_SCALE)}")
        used_names.add(name)
    if len(rsw.lights) > MAX_MAP_LIGHTS:
        print("  note: %s has %d light sources, exported the first %d"
              % (map_name, len(rsw.lights), MAX_MAP_LIGHTS))

    # RSW effect emitters (object_type 4) -- the torch flames, braziers and street
    # lamp glows. `formats/rsw.py` has always parsed these and the export dropped
    # them, so a lit torch was a lamp with no fire on it. in_sphinx1 alone declares
    # 179 (all type 47), pay_dun01 32, prontera 166.
    #
    # Written as markers carrying the RSW's own numbers rather than as finished
    # particle nodes, and the reason is the same split the lights follow: WHERE an
    # emitter is and WHICH kind it is are map data, while what a type-47 flame looks
    # like is a look decision that belongs in versioned code (`fx/map_effects.gd`)
    # rather than in gitignored `maps/`. It also keeps the .tscn cheap -- one marker
    # per emitter, no per-node materials -- and lets the client draw all of a map's
    # flames from ONE particle system.
    for etype in sorted(flame_res):
        info = flame_res[etype]
        preset = info["preset"]
        for effect in flame_groups[etype]:
            ex, _ey, ez = effect.position
            eox, _eoy, eoz = mu.scaled((ex, 0.0, -ez))
            ground = _ground_at(info["heights"], ex, ez)
            name = _node_name("Flame%d" % etype, used_names)
            lines.append("")
            lines.append(f'[node name="{name}" type="GPUParticles3D" parent="."]')
            lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, %s, %s, %s)"
                         % (_fmt(eox), _fmt(ground + float(preset["lift"])), _fmt(eoz)))
            # Fire emits light, it does not block it -- and with the lamps casting
            # shadows, a casting flame quad would throw a hard rectangle per lamp.
            lines.append("cast_shadow = 0")
            lines.append(f"amount = {int(preset['amount'])}")
            lines.append(f"lifetime = {_fmt(float(preset['lifetime']))}")
            # Already burning when the map appears, rather than igniting on entry.
            lines.append(f"preprocess = {_fmt(float(preset['lifetime']))}")
            lines.append(f'process_material = SubResource("flame_proc_{etype}")')
            lines.append(f'draw_pass_1 = SubResource("flame_mesh_{etype}")')
            used_names.add(name)
    if len(rsw.effects) > MAX_MAP_EFFECTS:
        print("  note: %s has %d effect emitters, exported the first %d"
              % (map_name, len(rsw.effects), MAX_MAP_EFFECTS))

    for item in instances:
        lines.append("")
        name = _node_name(item["name"], used_names)
        lines.append(f'[node name="{name}" parent="." instance=ExtResource("{item["ext"] + 1}")]')
        lines.append(f"transform = {item['transform']}")
        if item["no_anim"]:
            lines.append("metadata/no_anim = true")

    maps_dir = assets_dir / "maps"
    maps_dir.mkdir(exist_ok=True)
    _stable_bytes(maps_dir / f"{map_name}.tscn", ("\n".join(lines) + "\n").encode('utf-8'))

    # No JSON manifest. The scene above is the map: the client loads it directly
    # (world/map_loader.gd), so a parallel plain-data description of the same
    # world would just be a second thing to keep in step.

    summary = f"ok instances={len(instances)}"
    summary += " water" if water_info is not None else ""
    if missing:
        summary += f" missing_models={missing}"
    if stats.missing_textures:
        summary += f" missing_textures={len(stats.missing_textures)}"
    return summary


def _write_gat_png(builder, map_name: str, assets_dir: Path,
                   gat_parse=None) -> None:
    """1 pixel per GAT tile, value = terrain type (0 walkable, 1 blocked,
    2 water, 3 walkable water, 4 snipable water, 5/6 cliff). Row 0 is the
    map's north edge, like the minimap."""
    from PIL import Image

    if gat_parse is None:
        from ragx.formats import gat as gat_format
        gat_parse = gat_format.parse

    raw = builder.source.try_read(f"data\\{map_name}.gat")
    if raw is None:
        return
    gat = gat_parse(raw)
    pixels = bytearray(gat.width * gat.height)
    for index, tile in enumerate(gat.tiles):
        x = index % gat.width
        y = index // gat.width
        # flip vertically: GAT row 0 is the south edge
        pixels[(gat.height - 1 - y) * gat.width + x] = min(tile[4], 255)
    image = Image.frombytes("L", (gat.width, gat.height), bytes(pixels))
    nav_dir = assets_dir / "nav"
    nav_dir.mkdir(exist_ok=True)
    _stable_png(nav_dir / f"{map_name}_gat.png", image)


def _write_gat_bin(builder, map_name: str, assets_dir: Path,
                   gat_parse=None) -> None:
    """The raw walk grid as compact binary for the client's MapGrid — the data
    the *_gat.png can't be (a picture). One record per GAT cell in NATIVE GAT
    order (cell (x, y) at index y*width + x, y=0 = south), which matches the
    server's coordinate system so a server-sent (x, y) indexes straight in
    (NOT flipped like the png).

    Format (little-endian):
        magic   'RGAT'
        version u8   (=1)
        flags   u8   (=0)
        width   u32
        height  u32
        types   u8  [width*height]   0 walkable, 1 blocked, 2 water,
                                     3 walkable water, 4 snipable water, 5/6 cliff
        heights f32 [width*height]   mean of the 4 corner heights, raw GAT units
                                     (the client applies the map's vertical scale)
    """
    import struct

    if gat_parse is None:
        from ragx.formats import gat as gat_format
        gat_parse = gat_format.parse

    raw = builder.source.try_read(f"data\\{map_name}.gat")
    if raw is None:
        return
    gat = gat_parse(raw)
    n = gat.width * gat.height
    types = bytearray(n)
    heights = bytearray(4 * n)
    for i, tile in enumerate(gat.tiles):
        types[i] = min(tile[4], 255)
        avg = (tile[0] + tile[1] + tile[2] + tile[3]) * 0.25 * mu.WORLD_SCALE
        struct.pack_into("<f", heights, i * 4, avg)
    header = b"RGAT" + struct.pack("<BBII", 1, 0, gat.width, gat.height)
    nav_dir = assets_dir / "nav"
    nav_dir.mkdir(exist_ok=True)
    _stable_bytes(nav_dir / f"{map_name}_gat.bin", header + types + heights)


def _write_minimap(builder, map_name: str, assets_dir: Path) -> None:
    """The RO minimap image for the client's minimap window. The source bitmap
    lives in the UI texture folder (texture\\<UI>\\map\\<map>.bmp); magenta is the
    color key, so we emit a transparent PNG. Skipped silently if the map has no
    minimap in the archive."""
    from PIL import Image

    from .ui_export import UI_KOR

    texture = builder._load_texture(f"{UI_KOR}\\map\\{map_name}.bmp")
    if texture is None:
        return
    nav_dir = assets_dir / "nav"
    nav_dir.mkdir(exist_ok=True)
    image = Image.open(io.BytesIO(texture.png))
    image.load()
    _stable_png(nav_dir / f"{map_name}_minimap.png", image)


def prepare_project(assets_dir: Path) -> None:
    # Only seed these when missing: a real game project keeps a hand-maintained
    # project.godot (autoloads, main scene) and may have customised the root
    # glue scripts. Re-exporting into an existing project must not clobber them.
    for name, body in (
        ("project.godot", PROJECT_GODOT),
        ("autoplay_all.gd", AUTOPLAY_GD),
        ("water.gdshader", WATER_GDSHADER),
    ):
        target = assets_dir / name
        if not target.exists():
            target.write_text(body, encoding="utf-8")
    for ignored in ("renders", "renders_gltf"):
        folder = assets_dir / ignored
        if folder.is_dir():
            (folder / ".gdignore").write_text("", encoding="utf-8")
    # Keep the editor importer away from the monolithic per-map glTFs at
    # the project root (ragnadot.convert output) — the Godot pipeline uses
    # the decomposed files instead.
    for gltf in assets_dir.glob("*.gltf"):
        stub = gltf.with_name(gltf.name + ".import")
        if not stub.exists():
            stub.write_text(KEEP_IMPORT, encoding="utf-8")


def _worker(job: tuple[str, str, str]) -> tuple[str, str]:
    client_dir, map_name, assets = job
    from ragx.grf import GrfArchive
    from ragx.map_builder import AssetSource, MapBuilder, MapHasNoTerrain

    builder = globals().get("_WORKER_BUILDER")
    if builder is None:
        archive = GrfArchive(os.path.join(client_dir, "data.grf"))
        builder = MapBuilder(AssetSource(archive), texture_dir=os.path.join(assets, "textures"))
        globals()["_WORKER_BUILDER"] = builder
    try:
        t0 = time.time()
        summary = export_map(builder, map_name, Path(assets))
        return map_name, f"{summary} ({time.time() - t0:.1f}s)"
    except MapHasNoTerrain as error:
        return map_name, f"SKIPPED ({error})"
    except Exception:  # noqa: BLE001
        return map_name, "FAILED\n" + traceback.format_exc()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("maps", nargs="*")
    parser.add_argument("--client", default=r"C:\Gravity\Ragnarok")
    parser.add_argument("--assets", default=str(PROJECT_ROOT / "converted_assets"), type=Path)
    parser.add_argument("--processes", type=int, default=1)
    arguments = parser.parse_args()

    assets_dir = arguments.assets
    if not (assets_dir / "textures").is_dir():
        (assets_dir / "textures").mkdir(parents=True, exist_ok=True)
    prepare_project(assets_dir)

    maps = arguments.maps
    if not maps:
        from ragx.grf import GrfArchive
        archive = GrfArchive(os.path.join(arguments.client, "data.grf"))
        maps = sorted(n[5:-4] for n in archive.namelist()
                      if n.endswith(".rsw") and n.count("\\") == 1)
        archive.close()

    jobs = [(arguments.client, m, str(assets_dir)) for m in maps]
    failures = 0
    t0 = time.time()
    if arguments.processes > 1:
        import multiprocessing as mp
        with mp.Pool(arguments.processes) as pool:
            for index, (map_name, message) in enumerate(pool.imap_unordered(_worker, jobs, chunksize=1)):
                print(f"[{index + 1}/{len(maps)}] {map_name}: {message}", flush=True)
                failures += "FAILED" in message
    else:
        for index, job in enumerate(jobs):
            map_name, message = _worker(job)
            print(f"[{index + 1}/{len(maps)}] {map_name}: {message}", flush=True)
            failures += "FAILED" in message

    print(f"\ndone: {len(maps) - failures}/{len(maps)} maps in {time.time() - t0:.0f}s")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
