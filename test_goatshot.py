"""Rendering, selection, export, and portal lifecycle checks without a display."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import cairo
from PIL import Image
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
from goatshot import Editor, Goatshot, render_crop, load_image


def background(w=1280, h=800):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(s)
    gradient = cairo.LinearGradient(0, 0, w, h)
    gradient.add_color_stop_rgb(0, .12, .24, .36)
    gradient.add_color_stop_rgb(1, .27, .12, .37)
    c.set_source(gradient); c.paint()
    c.set_source_rgb(.97, .97, .97); c.rectangle(180, 105, 900, 550); c.fill()
    c.set_source_rgb(.88, .88, .9); c.rectangle(180, 105, 900, 40); c.fill()
    c.set_source_rgb(.22, .22, .25); c.select_font_face('Sans'); c.set_font_size(14)
    c.move_to(205, 131); c.show_text('Notes')
    c.set_font_size(28); c.move_to(235, 220); c.show_text('A screenshot worth sharing')
    c.set_font_size(16); c.move_to(235, 260); c.show_text('Select an area, add a note, copy or save.')
    for n, width in enumerate([540, 460, 510, 350]):
        c.set_source_rgb(.83, .85, .88); c.rectangle(235, 310+n*34, width, 10); c.fill()
    from gi.repository import Gdk
    return Gdk.pixbuf_get_from_surface(s, 0, 0, w, h)


class Headless:
    transform = Editor.transform
    view_rect = Editor.view_rect
    handles = Editor.handles
    hit_handle = Editor.hit_handle
    toolbar = Editor.toolbar
    panel = staticmethod(Editor.panel)
    button = Editor.button
    draw = Editor.draw
    point = Editor.point
    press = Editor.press
    motion = Editor.motion
    release = Editor.release
    key = Editor.key
    commit_text = Editor.commit_text
    add_text = Editor.add_text
    action = Editor.action

    def __init__(self, pixbuf):
        self.pixbuf = pixbuf
        self.selection = (250, 190, 650, 380)
        self.items = [('arrow', [(620, 400), (780, 300)], (1., 0., 0.), 3, '')]
        self.current = None; self.drag = None; self.tool = 'arrow'
        self.color = (1., 0., 0.); self.width = 3; self.dim = .48; self.hover = None; self.buttons = []
        self.redo_items = []
        self.canvas = Mock()
        self.canvas.get_allocation.return_value = SimpleNamespace(width=1280, height=800)
        self.canvas.get_window.return_value = None
        self.transform()


class RenderingTests(unittest.TestCase):
    def test_crop_keeps_source_pixels_and_excludes_overlay(self):
        p = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 100, 80)
        p.fill(0xffffffff)
        s = render_crop(p, (10, 20, 30, 40), [])
        self.assertEqual((s.get_width(), s.get_height()), (30, 40))
        self.assertEqual(bytes(s.get_data())[:4], b'\xff\xff\xff\xff')

    def test_fractional_crop_rounding_and_clipping(self):
        p = background(100, 80)
        s = render_crop(p, (10.4, 20.2, 30.1, 40.2), [])
        self.assertEqual((s.get_width(), s.get_height()), (31, 41))
        with self.assertRaises(ValueError): render_crop(p, (50, 50, 0, 0), [])

    def test_every_tool_renders_and_exports(self):
        p = background()
        for tool in ['pen', 'line', 'arrow', 'rectangle', 'marker', 'text']:
            with self.subTest(tool=tool):
                item = (tool, [(300, 250), (500, 350)], (1., 0., 0.), 3, 'Hello')
                s = render_crop(p, (250, 190, 650, 380), [item])
                with tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp)/'capture.png'; s.write_to_png(str(target))
                    with Image.open(target) as image:
                        self.assertEqual(image.size, (650, 380))

    def test_image_decoding_and_inline_text(self):
        e = Headless(background())
        e.add_text((300, 300))
        for key in ['a', 'p']:
            e.key(None, SimpleNamespace(keyval=ord(key), state=0))
        self.assertEqual(e.current[4], 'ap')
        self.assertEqual(e.tool, 'arrow')
        e.commit_text()
        self.assertEqual(e.items[-1][4], 'ap')
        e.action('undo')
        self.assertEqual(len(e.items), 1)
        e.action('redo')
        self.assertEqual(len(e.items), 2)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'test.png'
            render_crop(e.pixbuf, e.selection, e.items).write_to_png(str(target))
            decoded = load_image(str(target))
            self.assertEqual((decoded.get_width(), decoded.get_height()), (650, 380))

    def test_selection_drag_and_resize(self):
        e = Headless(background())
        e.selection = None
        e.press(None, SimpleNamespace(x=900, y=600, button=1))
        e.motion(None, SimpleNamespace(x=100, y=100))
        e.release(None, SimpleNamespace(button=1))
        self.assertEqual(e.selection, (100, 100, 800, 500))
        e.press(None, SimpleNamespace(x=100, y=100, button=1))
        e.motion(None, SimpleNamespace(x=40, y=60))
        e.release(None, SimpleNamespace(button=1))
        self.assertEqual(e.selection, (40, 60, 860, 540))

    def test_preview_and_toolbar_fit(self):
        e = Headless(background())
        for selection in [(250,190,650,380), (0,0,1280,800), (1240,760,40,40)]:
            e.selection = selection
            s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 800)
            e.draw(e.canvas, cairo.Context(s))
            for name, (x,y,w,h), tip in e.buttons:
                self.assertTrue(0 <= x <= 1280-w, name)
                self.assertTrue(0 <= y <= 800-h, name)
        e.selection = (250,190,650,380)
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32,1280,800)
        e.draw(e.canvas,cairo.Context(s))
        s.write_to_png(str(Path(__file__).parent/'preview.png'))

    def test_cancelled_portal_response_does_not_open_editor(self):
        fake = SimpleNamespace(pending=True, request_path='/request/test', cleanup=Mock(), error=Mock(), open_image=Mock())
        Goatshot.response(fake, None, None, '/request/test', None, None,
                           SimpleNamespace(unpack=lambda:(1, {})))
        fake.cleanup.assert_called_once(); fake.error.assert_not_called(); fake.open_image.assert_not_called()

    def test_successful_portal_response_opens_returned_file(self):
        fake = SimpleNamespace(pending=True, request_path='/request/test', cleanup=Mock(), error=Mock(), open_image=Mock())
        Goatshot.response(fake, None, None, '/request/test', None, None,
                           SimpleNamespace(unpack=lambda:(0, {'uri':'file:///tmp/test%20capture.png'})))
        fake.open_image.assert_called_once_with('/tmp/test capture.png')



class DesktopTests(unittest.TestCase):
    def test_settings_validation_and_roundtrip(self):
        from desktop import load_settings, save_settings, validated, DEFAULTS
        bad = validated({'width': True, 'dim': float('nan'), 'color': [2, 0, 0], 'save_folder': 'relative'})
        self.assertEqual(bad, DEFAULTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            self.assertEqual(load_settings(path), DEFAULTS)
            settings = dict(DEFAULTS, width=6, color=[0, .5, 1], copy_after_save=True)
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text('broken json')
            self.assertEqual(load_settings(path), DEFAULTS)

    def test_autostart_is_background_only(self):
        from desktop import set_autostart
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'autostart.desktop'
            set_autostart(True, path)
            self.assertIn('goatshot --background', path.read_text())
            set_autostart(False, path)
            self.assertFalse(path.exists())

    def test_tray_properties_match_dbus_interface(self):
        from desktop import Tray, SNI_XML
        from gi.repository import Gio
        iface = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        for prop in iface.properties:
            value = Tray.property(SimpleNamespace(app=SimpleNamespace(settings={'icon':'neon'})), None, None, None, None, prop.name)
            self.assertIsNotNone(value, prop.name)
            self.assertEqual(value.get_type_string(), prop.signature, prop.name)

    def test_tray_embeds_neon_pixels_in_argb_order(self):
        from desktop import Tray, tray_pixmaps
        from PIL import Image
        pixmaps = tray_pixmaps()
        self.assertEqual([w for w,h,data in pixmaps], [22,32,48,64])
        source = Image.open(Path(__file__).parent/'assets/goatshot.png').convert('RGBA')
        for width, height, data in pixmaps:
            self.assertEqual(len(data), width*height*4)
            expected = source.resize((width,height), Image.Resampling.LANCZOS)
            rgba = expected.getpixel((width//2,height//2))
            offset = ((height//2)*width+width//2)*4
            self.assertEqual(tuple(data[offset:offset+4]), (rgba[3],*rgba[:3]))
        self.assertEqual(Tray.property(SimpleNamespace(app=SimpleNamespace(settings={'icon':'neon'})),None,None,None,None,'IconName').unpack(), '')
        self.assertTrue(Tray.property(SimpleNamespace(app=SimpleNamespace(settings={'icon':'neon'})),None,None,None,None,'IconPixmap').unpack())

    def test_icon_choice_persists_and_invalid_values_fall_back(self):
        from desktop import validated, save_settings, load_settings
        from branding import ICONS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            for name in ICONS:
                save_settings({'icon': name}, path)
                self.assertEqual(load_settings(path)['icon'], name)
            for invalid in ['../outside', [], None]:
                self.assertEqual(validated({'icon':invalid})['icon'], 'neon')

    def test_tray_choices_have_distinct_pixel_data(self):
        from desktop import tray_pixmaps
        from branding import ICONS
        data = [tray_pixmaps(name)[1][2] for name in ICONS]
        self.assertEqual(len(set(data)), len(ICONS))

    def test_command_line_settings_and_background_do_not_capture(self):
        fake = SimpleNamespace(show_settings=Mock(), activate=Mock(), quit=Mock())
        Goatshot.do_command_line(fake, SimpleNamespace(get_arguments=lambda:['goatshot', '--settings']))
        fake.show_settings.assert_called_once()
        fake.activate.assert_not_called()
        Goatshot.do_command_line(fake, SimpleNamespace(get_arguments=lambda:['goatshot', '--background']))
        fake.activate.assert_not_called()
        Goatshot.do_command_line(fake, SimpleNamespace(get_arguments=lambda:['goatshot', '--quit']))
        fake.quit.assert_called_once()


class InstallTests(unittest.TestCase):
    def test_install_launches_from_path_with_spaces(self):
        import subprocess
        from install import install
        with tempfile.TemporaryDirectory(prefix='goatshot install ') as tmp:
            home = Path(tmp)
            legacy = home/'.local/share/icons/hicolor/scalable/apps/goatshot.svg'
            legacy.parent.mkdir(parents=True)
            legacy.write_text('<svg/>')
            app = install(home)
            self.assertFalse(legacy.exists())
            self.assertEqual((home/'.local/state/goatshot/legacy-goatshot.svg').read_text(), '<svg/>')
            self.assertEqual((home/'.local/share/icons/hicolor/256x256/apps/goatshot.png').read_bytes(),
                             (app/'assets/goatshot.png').read_bytes())
            self.assertTrue((app/'assets/goatshot.png').is_file())
            result = subprocess.run([str(home/'.local/bin/goatshot'), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--background', result.stdout)
            desktop = home/'.local/share/applications/io.github.isaac.Snaplight.desktop'
            self.assertIn('Name=Goatshot', desktop.read_text())
            self.assertFalse((home/'.config/goatshot/settings.json').exists())
            from branding import ICONS
            for label, filename in ICONS.values(): self.assertTrue((app/'assets'/filename).exists())
            config = home/'.config/goatshot/settings.json'
            config.parent.mkdir(parents=True)
            config.write_text('{"icon":"mountain"}')
            install(home)
            self.assertIn('goatshot-mountain.png', desktop.read_text())
            self.assertEqual(config.read_text(), '{"icon":"mountain"}')

if __name__ == '__main__': unittest.main()
