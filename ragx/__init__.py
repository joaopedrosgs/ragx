"""ragx — Ragnarok Online asset exporter.

Reads a Ragnarok Online client's GRF archives and exports the assets to
open, engine-neutral formats:

* ``ragx gltf``    — maps and models to glTF 2.0
* ``ragx sprites`` — SPR/ACT sprites to spritesheet PNG + JSON
* ``ragx effects`` — STR skill/visual effects to atlas PNG + keyframe JSON
* ``ragx ui``      — interface bitmaps to transparent PNG

See ``ragx --help`` or the bundled README for details.
"""

__version__ = "0.1.0"
