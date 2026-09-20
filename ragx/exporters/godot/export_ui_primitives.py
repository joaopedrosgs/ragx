"""Build text-free, layout-independent RO skin primitives through ragx.

This is project packaging, not a second GRF parser. Source decoding and magenta
keying belong to ragx. Every crop/composition is recorded in manifest.json.
Generated PNGs and Godot StyleBoxTextures stay under ignored ui/skin/primitives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

class SkinBuilder:
    def __init__(self, archive, output: Path, resource_prefix: str):
        self.archive = archive
        self.output = output
        self.resource_prefix = resource_prefix.rstrip('/')
        self.assets: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.inputs: list[dict] = []
        self.output.mkdir(parents=True, exist_ok=True)

    def source(self, relative: str, rect: tuple[int, int, int, int] | None = None) -> Image.Image:
        from ragx.commands.ui_cmd import UI_GRF_PREFIX, _keyed_png
        key = UI_GRF_PREFIX + relative.replace('/', '\\')
        raw = self.archive.read(key)
        image = _keyed_png(raw)
        self.sources[relative] = {
            'grf_path': key,
            'archive': str(next(a.path for a in reversed(self.archive.archives) if key in a)),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'size': list(image.size),
        }
        entry: dict = {'source': relative}
        if rect is not None:
            x, y, w, h = rect
            if x < 0 or y < 0 or x + w > image.width or y + h > image.height:
                raise ValueError(f'Crop outside {relative}: {rect}')
            image = image.crop((x, y, x + w, y + h))
            entry['crop_xywh'] = list(rect)
        self.inputs.append(entry)
        return image

    def save(self, name: str, image: Image.Image, *, margins=None, padding=None,
             tile_y=False, draw_center=True, role='', operation='copy') -> None:
        image = image.convert('RGBA')
        dest = self.output / (name + '.png')
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest)
        item = dict(name=name, texture=name + '.png', size=list(image.size),
                    role=role, operation=operation, sources=self.inputs)
        self.inputs = []
        if margins is not None:
            l, t, r, b = margins
            if min(margins) < 0 or l+r >= image.width or t+b >= image.height:
                raise ValueError(f'Invalid nine-slice center: {name}')
            padding = padding if padding is not None else margins
            item.update(slice_ltrb=list(margins), content_ltrb=list(padding),
                        minimum_size=[l+r+1, t+b+1], tile_vertical=tile_y,
                        style=name + '.tres', draw_center=draw_center)
            lines = ['[gd_resource type="StyleBoxTexture" load_steps=2 format=3]', '',
                     f'[ext_resource type="Texture2D" path="{self.resource_prefix}/{name}.png" id="1"]', '',
                     '[resource]', 'texture = ExtResource("1")']
            for side, value in zip(('left', 'top', 'right', 'bottom'), margins):
                lines.append(f'texture_margin_{side} = {float(value)}')
            for side, value in zip(('left', 'top', 'right', 'bottom'), padding):
                lines.append(f'content_margin_{side} = {float(value)}')
            if tile_y:
                lines.append('axis_stretch_vertical = 1')
            if not draw_center:
                lines.append('draw_center = false')
            dest.with_suffix('.tres').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.assets.append(item)

    def strip(self, names: list[str]) -> Image.Image:
        pieces = [self.source(n) for n in names]
        if len({p.height for p in pieces}) != 1:
            raise ValueError(f'Mismatched strip heights: {names}')
        image = Image.new('RGBA', (sum(p.width for p in pieces), pieces[0].height))
        x = 0
        for piece in pieces:
            image.paste(piece, (x, 0))
            x += piece.width
        return image

    @staticmethod
    def unmatte_corners(image: Image.Image) -> Image.Image:
        """Recover alpha on the original BMP's white-matted outer corner pixels.

        The keyed BMP keeps pale opaque staircase pixels just inside magenta.
        Using the adjacent dark outline as foreground recovers partial coverage
        instead of deleting the bevel or leaving a white halo on dark panels.
        """
        result = image.copy()
        for y in range(image.height):
            for x in range(image.width):
                if min(x,image.width-1-x) >= 6 or min(y,image.height-1-y) >= 6:
                    continue
                color=image.getpixel((x,y))
                if color[3] == 0 or min(color[:3]) < 210:
                    continue
                neighbors=[]
                boundary=False
                for nx,ny in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
                    if not (0<=nx<image.width and 0<=ny<image.height):
                        boundary=True
                        continue
                    pixel=image.getpixel((nx,ny))
                    if pixel[3]==0:
                        boundary=True
                    elif max(pixel[:3])<210:
                        neighbors.append(pixel)
                if not boundary or not neighbors:
                    continue
                foreground=min(neighbors,key=lambda p:sum(p[:3]))
                coverage=sum((255-color[c])/(255-foreground[c]) for c in range(3))/3
                result.putpixel((x,y),(*foreground[:3],round(255*max(0,min(1,coverage)))))
        return result

    @staticmethod
    def compact(image: Image.Image, margins: tuple[int, int, int, int], *,
                center_width=2, center_height=2) -> Image.Image:
        """Keep source corners/edges; remove large blank interiors, without scaling.

        This cannot remove embedded text. Callers first select a verified blank
        source region. Midpoints only supply homogeneous empty center strips.
        """
        l, t, r, b = margins
        xs = [(0, l), (image.width//2, center_width), (image.width-r, r)]
        ys = [(0, t), (image.height//2, center_height), (image.height-b, b)]
        result = Image.new('RGBA', (l+center_width+r, t+center_height+b))
        dy = 0
        for sy, height in ys:
            dx = 0
            for sx, width in xs:
                if width and height:
                    result.paste(image.crop((sx, sy, sx+width, sy+height)), (dx, dy))
                dx += width
            dy += height
        return result

    def build(self) -> None:
        # The source frame pieces contain no title glyph, controls or content.
        frame = Image.new('RGBA', (42, 42), (255, 255, 255, 255))
        for suffix, xy in {'lu': (0,0), 'mu': (14,0), 'ru': (28,0),
                           'lm': (0,14), 'rm': (28,14), 'ld': (0,28),
                           'md': (14,28), 'rd': (28,28)}.items():
            frame.paste(self.source(f'sysbox_{suffix}.bmp'), xy)
        self.save('surfaces/window', frame, margins=(14,14,14,14), padding=(8,8,8,8),
                  tile_y=True, role='White panel with original striped frame; separate header/content/footer.',
                  operation='assemble eight frame pieces around a white center')

        # Foreground border: clear the connected white interior, retaining the
        # original curved strokes and exterior rim for drawing above content.
        from PIL import ImageDraw
        outline = frame.copy()
        ImageDraw.floodfill(outline, (21, 21), (0, 0, 0, 0))
        self.save('surfaces/window_outline', outline,
                  role='Foreground frame rim; protects original corner strokes from content backgrounds.',
                  operation='derive assembled window; flood-clear connected white interior')

        # A native texture resource is usable by capture tools without an editor
        # filesystem scan. Its pixels are identical to the companion PNG.
        rgba = ', '.join(str(value) for value in outline.tobytes())
        resource = ('[gd_resource type="ImageTexture" load_steps=2 format=3]\n\n'
                    '[sub_resource type="Image" id="pixels"]\n'
                    'data = {"data": PackedByteArray(' + rgba + '), '
                    '"format": "RGBA8", "height": 42, "mipmaps": false, "width": 42}\n\n'
                    '[resource]\nimage = SubResource("pixels")\n')
        (self.output/'surfaces/window_outline.tres').write_text(resource, encoding='utf-8')

        # Header pieces have no baked window icon (titlebar_fix does).
        self.save('surfaces/title', self.strip([f'basic_interface/titlebar_{s}.bmp' for s in ('left','mid','right')]),
                  margins=(4,3,4,3), padding=(5,2,5,2),
                  role='Blank blue header; use a Label and independent icon buttons.', operation='join three caps')

        # Small round-corner surface: blank center and original four corners.
        panel = Image.new('RGBA', (16,16), (255,255,255,255))
        for suffix, xy in {'lu':(0,0),'ru':(9,0),'ld':(0,9),'rd':(9,9)}.items():
            panel.paste(self.source(f'sysboxs_{suffix}.bmp'), xy)
        # Edges continue the corner's last/first scanline, never cross the body.
        for x in range(7,9):
            for y in range(7):
                panel.putpixel((x,y),panel.getpixel((6,y)))
                panel.putpixel((x,9+y),panel.getpixel((6,9+y)))
        for y in range(7,9):
            for x in range(16):
                panel.putpixel((x,y),panel.getpixel((x,6)))
        self.save('surfaces/dialog', panel, margins=(7,7,7,7), padding=(8,8,8,8),
                  role='Speech, tooltips and item descriptions; no illustration box or text positions.',
                  operation='assemble small corners, extend clean edge pixels, white center')

        for state, token in [('normal','out'),('hover','over'),('pressed','press'),('disabled','disable')]:
            image = self.strip([f'basic_interface/btn_{token}_{s}.bmp' for s in ('left','mid','right')])
            image = self.unmatte_corners(image)
            self.save(f'buttons/{state}', image, margins=(6,5,6,5), padding=(8,4,8,4),
                      role=f'Blank button, {state}; label/icon supplied by Control.',
                      operation='join three caps; recover alpha of white-matted outer corners')
        for state, token in [('normal','tab'),('selected','tab_a')]:
            image = self.strip([f'basic_interface/{token}_{s}.bmp' for s in ('l','m','r')])
            self.save(f'tabs/{state}', image, margins=(4,4,4,3), padding=(8,3,8,3),
                      role=f'Blank {state} tab; tabs can be arranged in any container.', operation='join three caps')

        # Empty input rectangle only. Source login labels and window layout are discarded.
        image = self.source('login_interface/win_login.bmp', (91,29,127,18))
        self.save('fields/normal', self.compact(image, (1,1,1,1)), margins=(1,1,1,1), padding=(5,4,5,4),
                  role='Text input or inset border; editable text is never in the bitmap.',
                  operation='extract blank input box; preserve corners and compact empty center')
        image = self.source('select_character/select_mark.bmp')
        self.save('surfaces/focus', self.compact(image, (7,7,7,7)), margins=(7,7,7,7), padding=(0,0,0,0),
                  draw_center=False, role='Focus/selection outline; overlay, never changes content layout.',
                  operation='compact blank selection outline; transparent center')

        for role, prefix in [('blue','gzeblue'),('red','gzered')]:
            image = self.strip([f'basic_interface/{prefix}_{s}.bmp' for s in ('left','mid','right')])
            self.save(f'gauges/{role}', image, margins=(4,3,4,3), padding=(0,0,0,0),
                      role='Independent gauge fill; clip at low values instead of shrinking rounded caps.',
                      operation='join left, repeatable fill, right')
        image = self.source('basic_interface/gze_bg.bmp')
        self.save('gauges/track', self.compact(image,(4,3,4,3),center_height=3),
                  margins=(4,3,4,3), padding=(0,0,0,0),
                  role='Independent gauge track; no baked HP/SP label or field location.',
                  operation='compact horizontal track')
        image = self.source('scroll0mid.bmp')
        # Godot derives a vertical scrollbar's width from content margins, not
        # texture margins. Zero padding creates an invisible zero-width rail.
        self.save('scrollbars/track', image, margins=(4,4,4,4), padding=(6,0,7,0),
                  role='Scrollbar track; thumb and arrows are independent controls.')
        pieces = [self.source(f'scroll0bar_{s}.bmp') for s in ('up','mid','down')]
        image = Image.new('RGBA',(pieces[0].width,sum(p.height for p in pieces)))
        y = 0
        for piece in pieces:
            image.paste(piece,(0,y))
            y += piece.height
        self.save('scrollbars/thumb', image, margins=(5,4,5,4), padding=(6,0,7,0),
                  role='Blue scrollbar thumb; height follows the visible content ratio.',
                  operation='join top, middle and bottom caps vertically')
        self.save('slots/empty', self.source('basic_interface/no_skill.bmp'), margins=(3,3,3,3), padding=(3,3,3,3),
                  role='Empty slot outline; content icons are independent TextureRects.')
        image = self.source('basic_interface/itemwin_mid.bmp', (0,12,32,20))
        # The white field surrounding this well is its actual background, not
        # magenta-key transparency. Pure white is cleared; blue/grey edge pixels
        # are kept for use against the original white panel.
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                if pixels[x,y][:3] == (255,255,255):
                    pixels[x,y] = (0,0,0,0)
        self.save('decorations/item_well', image,
                  role='Unscaled oval decoration, positioned by the slot container; not a nine-slice.',
                  operation='extract oval from one inventory cell; key white field transparent')

        icons = {
            'window': 'sys_base_off.bmp', 'close': 'basic_interface/sys_close_off.bmp',
            'close_hover': 'basic_interface/sys_close_on.bmp',
            'minimize':'basic_interface/sys_mini_off.bmp','minimize_hover':'basic_interface/sys_mini_on.bmp',
            'checkbox_off':'checkbox_0.bmp','checkbox_on':'checkbox_1.bmp',
            'radio_off':'radiobtn_off.bmp','radio_on':'radiobtn_on.bmp','resize':'btn_resize.bmp',
            'arrow_up':'basic_interface/arw_up.bmp','arrow_down':'basic_interface/arw_down.bmp',
            'arrow_left':'basic_interface/arw_left.bmp','arrow_right':'basic_interface/arw_right.bmp',
            'scroll_up':'scroll0up.bmp','scroll_down':'scroll0down.bmp',
            'character_add':'select_character/btn_add_out.bmp',
            'character_previous':'select_character/chr_arrow_l_out.bmp',
            'character_next':'select_character/chr_arrow_r_out.bmp',
            'page_current':'select_character/page_ball_fill.bmp','page_empty':'select_character/page_ball_empty.bmp',
            'map_plus':'minimap/i_plus_1.bmp','map_minus':'minimap/i_minus_1.bmp',
        }
        for name, source in icons.items():
            self.save('icons/'+name,self.source(source), role='Fixed-aspect icon; layout and hit target belong to the Control.')

        self.save('surfaces/equipment_center', self.source('basic_interface/equipwin_bg2.bmp', (114, 0, 54, 4)),
                  margins=(1, 1, 1, 1), padding=(0, 0, 0, 0), tile_y=True, role='Portrait stripe, no slots or text')
        self.save('surfaces/classic_footer', self.source('basic_interface/equipwin_bg3.bmp', (100, 0, 10, 20)),
                  margins=(1, 1, 1, 1), padding=(4, 0, 4, 0), role='Blank striped footer')
        self.save('decorations/costume_crest', self.source('basic_interface/equipwin_special.bmp', (80, 2, 122, 36)),
                  role='Separate costume crest decoration')
        self.save('slots/costume', self.source('basic_interface/equipwin_special.bmp', (5, 5, 24, 24)),
                  margins=(3, 3, 3, 3), padding=(0, 0, 0, 0), role='Independent costume slot')
        self.save('surfaces/costume_row', self.source('basic_interface/equipwin_special.bmp', (40, 2, 4, 130)),
                  margins=(1, 0, 1, 0), padding=(0, 0, 0, 0), role='Full-height costume gradient and row separators, without slots or crest')

        self.save('surfaces/login_footer', self.source('login_interface/win_login.bmp', (0, 92, 280, 28)),
                  margins=(4,1,4,4), padding=(5,3,5,4), role='Original rounded striped flow footer, no text')
        self.save('surfaces/party_toolbar', self.source('basic_interface/mesbtn_mid.bmp'),
                  margins=(1,1,1,1), padding=(0,0,0,0), role='Party toolbar strip continued behind buttons')

        manifest = dict(schema=1, description='RO text-free UI primitives; margins are left/top/right/bottom in native pixels.',
                        sources=self.sources, assets=self.assets,
                        rendering=dict(texture_filter='nearest at integer UI scales; choose linear for fractional scales',
                                       mipmaps=False, compression='lossless', colorspace='sRGB',
                                       alpha='straight', control_positions='none', text='none'),
                        composition_rules=['Use Containers and dynamic/localized Labels.',
                                           'Only nine-slice backgrounds and borders; preserve icon aspect ratio.',
                                           'Slice margins preserve art; content margins are independent layout defaults.',
                                           'Do not resize below minimum_size; wrap/scroll content when constrained.',
                                           'Keep portraits, item/skill icons, map artwork and text outside surface textures.'])
        (self.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',default='C:/Gravity/Ragnarok')
    parser.add_argument('--assets',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    from ragx.client import open_stack
    output=args.assets/'ui/skin/primitives'
    with open_stack(args.client) as archive:
        builder=SkinBuilder(archive,output,'res://ui/skin/primitives')
        builder.build()
    print(json.dumps(dict(ok=True,textures=len(builder.assets),
                         nine_slices=sum('style' in a for a in builder.assets),output=str(output))))


if __name__=='__main__':
    main()
