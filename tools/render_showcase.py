"""Render showcase stills + a turntable orbit for one converted map, framing the
WHOLE map so nothing is ever cut off.

    blender -b --factory-startup -P render_showcase.py -- <map.gltf> <out_dir> [frames] [W] [H]

Writes:
    <out_dir>/<base>_persp.png          textured perspective (game-like)
    <out_dir>/<base>_lit.png            studio-lit (exercises normals)
    <out_dir>/orbit/<base>_####.png     turntable frames (for a GIF)
"""

import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
gltf_path = os.path.abspath(argv[0])
out_dir = os.path.abspath(argv[1])
frames = int(argv[2]) if len(argv) > 2 else 48
res_x = int(argv[3]) if len(argv) > 3 else 760
res_y = int(argv[4]) if len(argv) > 4 else 470
base = os.path.splitext(os.path.basename(gltf_path))[0]
orbit_dir = os.path.join(out_dir, "orbit")
os.makedirs(orbit_dir, exist_ok=True)

FOV_DEG = 40.0  # horizontal field of view

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=gltf_path)

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "FLAT"
scene.display.shading.color_type = "TEXTURE"
scene.display.shading.show_backface_culling = False
scene.display.render_aa = "32"            # high anti-aliasing for clean edges
scene.render.film_transparent = True      # no background (transparent alpha)
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.world = bpy.data.worlds.new("World")
scene.world.color = (0.05, 0.06, 0.09)

# ---- scene bounds (Blender Z-up; glTF -Z north -> Blender +Y north) --------
lo = Vector((1e18, 1e18, 1e18))
hi = Vector((-1e18, -1e18, -1e18))
for obj in scene.objects:
    if obj.type != "MESH":
        continue
    for corner in obj.bound_box:
        world = obj.matrix_world @ Vector(corner)
        lo = Vector(map(min, lo, world))
        hi = Vector(map(max, hi, world))
center = (lo + hi) / 2
corners = [Vector((x, y, z)) for x in (lo.x, hi.x)
           for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
radius = max((c - center).length for c in corners)

# ---- camera + fitting -------------------------------------------------------
camera_data = bpy.data.cameras.new("cam")
camera_data.sensor_fit = "HORIZONTAL"
camera_data.angle = math.radians(FOV_DEG)
camera_data.clip_start = 1.0
camera_data.clip_end = 500000
camera = bpy.data.objects.new("cam", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

aspect = res_x / res_y
th_x = math.tan(camera_data.angle / 2.0)   # tan(horizontal half-fov)
th_y = th_x / aspect                        # tan(vertical half-fov)


def aim(cam_dir, distance):
    """Point the camera at `center` from unit direction `cam_dir`, `distance` away."""
    camera.location = center + cam_dir * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()


def box_fit(cam_dir, margin=1.05):
    """Smallest distance along cam_dir that keeps ALL bounding-box corners in
    frame (both axes), so the whole map is shown."""
    aim(cam_dir, radius)  # sets rotation (independent of distance)
    world_to_cam = camera.rotation_euler.to_matrix().transposed()
    d = 0.0
    for c in corners:
        v = world_to_cam @ (c - center)         # camera-space; forward is -Z
        d = max(d, v.z + abs(v.x) * margin / th_x, v.z + abs(v.y) * margin / th_y)
    return d


def render_to(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def unit(v):
    return Vector(v).normalized()


# ---- hero perspective still (textured, ~40 deg elevation from the south) ----
scene.render.resolution_x, scene.render.resolution_y = 2000, 1250
d_persp = unit((0.0, -math.cos(math.radians(40)), math.sin(math.radians(40))))
aim(d_persp, box_fit(d_persp))
render_to(os.path.join(out_dir, f"{base}_persp.png"))

# ---- studio-lit still from a corner (shows the mesh normals) ----------------
scene.display.shading.light = "STUDIO"
d_lit = unit((0.55, -0.7, 0.6))
aim(d_lit, box_fit(d_lit))
render_to(os.path.join(out_dir, f"{base}_lit.png"))

# ---- turntable orbit (textured) ---------------------------------------------
# Constant distance = worst-case box fit over the whole orbit: tight framing
# that fills the frame, never cuts, and never changes size while spinning.
scene.display.shading.light = "FLAT"
scene.render.resolution_x, scene.render.resolution_y = res_x, res_y
elev = math.radians(40)
ch, sh = math.cos(elev), math.sin(elev)
orbit_dirs = [unit((math.sin(2 * math.pi * i / frames) * ch,
                    -math.cos(2 * math.pi * i / frames) * ch, sh))
              for i in range(frames)]
orbit_distance = max(box_fit(d, margin=1.05) for d in orbit_dirs)
for i, cam_dir in enumerate(orbit_dirs):
    aim(cam_dir, orbit_distance)
    render_to(os.path.join(orbit_dir, f"{base}_{i:04d}.png"))

print("RENDER_DONE", base, "frames", frames)
