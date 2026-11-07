#!/usr/bin/env python3
"""A small native screenshot editor with a Lightshot-style interface."""
import argparse
import io
import threading
import math
from pathlib import Path
import sys
import uuid
from datetime import datetime

import cairo
from PIL import Image, ImageOps
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib

from desktop import Tray, SettingsWindow, load_settings
from branding import icon_path, update_desktop_icon

APP_ID = 'io.github.isaac.Snaplight'  # Stable installed application identity.


def load_image(filename):
    # Decode directly, without spawning a desktop image-loader service.
    with Image.open(filename) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(image.tobytes()), GdkPixbuf.Colorspace.RGB,
            True, 8, image.width, image.height, image.width*4)


def rect(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), abs(a[0]-b[0]), abs(a[1]-b[1])


def inside(p, r):
    x, y, w, h = r
    return x <= p[0] <= x+w and y <= p[1] <= y+h


def annotation(cr, item):
    tool, points, color, width, text = item
    cr.save()
    cr.set_source_rgba(*color, 0.35 if tool == 'marker' else 1)
    cr.set_line_width(width * (5 if tool == 'marker' else 1))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    a, b = points[0], points[-1]
    if tool in ('pen', 'marker'):
        cr.move_to(*a)
        if len(points) == 1:
            cr.line_to(a[0]+0.01, a[1])
        for p in points[1:]:
            cr.line_to(*p)
        cr.stroke()
    elif tool in ('line', 'arrow'):
        cr.move_to(*a)
        cr.line_to(*b)
        cr.stroke()
        if tool == 'arrow':
            angle = math.atan2(b[1]-a[1], b[0]-a[0])
            length = max(12, width*4)
            cr.move_to(b[0]-length*math.cos(angle-.45), b[1]-length*math.sin(angle-.45))
            cr.line_to(*b)
            cr.line_to(b[0]-length*math.cos(angle+.45), b[1]-length*math.sin(angle+.45))
            cr.stroke()
    elif tool == 'rectangle':
        cr.rectangle(*rect(a, b))
        cr.stroke()
    elif tool == 'text':
        cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(max(18, width*7))
        for n, line in enumerate(text.splitlines()):
            cr.move_to(a[0], a[1]+n*max(22, width*8))
            cr.show_text(line)
    cr.restore()


def render_crop(pixbuf, selection, items):
    """Render in original screenshot pixels; no UI or dimming enters the export."""
    x, y, w, h = selection
    left, top = max(0, math.floor(x)), max(0, math.floor(y))
    right = min(pixbuf.get_width(), math.ceil(x+w))
    bottom = min(pixbuf.get_height(), math.ceil(y+h))
    if right <= left or bottom <= top:
        raise ValueError('Select an area first.')
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, right-left, bottom-top)
    cr = cairo.Context(surface)
    cr.translate(-left, -top)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    for item in items:
        annotation(cr, item)
    surface.flush()
    return surface


def rounded_rect(cr, x, y, w, h, radius=5):
    radius = min(radius, w/2, h/2)
    cr.new_sub_path()
    for cx, cy, start in [(x+w-radius, y+radius, -math.pi/2),
                           (x+w-radius, y+h-radius, 0),
                           (x+radius, y+h-radius, math.pi/2),
                           (x+radius, y+radius, math.pi)]:
        cr.arc(cx, cy, radius, start, start+math.pi/2)
    cr.close_path()


def icon(cr, name, x, y, active=False, danger=False):
    """Consistent 18px outline glyphs with rounded joins and optical spacing."""
    cr.save()
    cr.translate(x, y)
    cr.set_source_rgb(*((.39, .24, .72) if active else (.77, .24, .29) if danger else (.25, .28, .34)))
    cr.set_line_width(1.65)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if name == 'pen':
        cr.move_to(3, 11.5); cr.line_to(11.8, 2.7)
        cr.curve_to(13.6, .9, 17.1, 4.4, 15.3, 6.2)
        cr.line_to(6.5, 15); cr.line_to(2, 16); cr.close_path(); cr.stroke()
        cr.move_to(10.5, 4); cr.line_to(14, 7.5); cr.stroke()
    elif name == 'line':
        cr.move_to(4, 14); cr.line_to(14, 4); cr.stroke()
        for px, py in [(3, 15), (15, 3)]:
            cr.arc(px, py, 1.3, 0, math.tau); cr.fill()
    elif name == 'arrow':
        cr.move_to(3, 15); cr.line_to(15, 3)
        cr.move_to(7, 3); cr.line_to(15, 3); cr.line_to(15, 11); cr.stroke()
    elif name == 'rectangle':
        rounded_rect(cr, 2, 3, 14, 12, 2); cr.stroke()
    elif name == 'marker':
        cr.move_to(4, 10); cr.line_to(10.5, 2); cr.line_to(16, 6.5)
        cr.line_to(9.5, 14.5); cr.close_path(); cr.stroke()
        cr.move_to(4, 10); cr.line_to(9.5, 14.5)
        cr.move_to(5, 12); cr.line_to(3, 15); cr.line_to(6, 15); cr.line_to(7.5, 13); cr.stroke()
        cr.move_to(2, 17); cr.line_to(15, 17); cr.stroke()
    elif name == 'text':
        cr.move_to(3, 5); cr.line_to(3, 3); cr.line_to(15, 3); cr.line_to(15, 5)
        cr.move_to(9, 3); cr.line_to(9, 15); cr.move_to(6, 15); cr.line_to(12, 15); cr.stroke()
    elif name == 'undo':
        cr.move_to(6, 3); cr.line_to(2, 7); cr.line_to(6, 11)
        cr.move_to(2, 7); cr.line_to(10.5, 7)
        cr.curve_to(18, 7, 18, 15.5, 10.5, 15.5); cr.line_to(8, 15.5); cr.stroke()
    elif name == 'copy':
        rounded_rect(cr, 6, 6, 10, 10, 2); cr.stroke()
        cr.move_to(11, 3); cr.line_to(11, 2); cr.line_to(4, 2)
        cr.curve_to(2, 2, 2, 2, 2, 4); cr.line_to(2, 11); cr.line_to(3, 11); cr.stroke()
    elif name == 'upload':
        cr.move_to(9, 12); cr.line_to(9, 2)
        cr.move_to(5.5, 5.5); cr.line_to(9, 2); cr.line_to(12.5, 5.5)
        cr.move_to(2, 11); cr.line_to(2, 14); cr.curve_to(2, 16, 2, 16, 4, 16)
        cr.line_to(14, 16); cr.curve_to(16, 16, 16, 16, 16, 14); cr.line_to(16, 11); cr.stroke()
    elif name == 'save':
        cr.move_to(9, 2); cr.line_to(9, 11)
        cr.move_to(5.5, 7.5); cr.line_to(9, 11); cr.line_to(12.5, 7.5)
        cr.move_to(2, 11); cr.line_to(2, 14); cr.curve_to(2, 16, 2, 16, 4, 16)
        cr.line_to(14, 16); cr.curve_to(16, 16, 16, 16, 16, 14); cr.line_to(16, 11); cr.stroke()
    elif name == 'close':
        cr.move_to(4.5, 4.5); cr.line_to(13.5, 13.5)
        cr.move_to(13.5, 4.5); cr.line_to(4.5, 13.5); cr.stroke()
    elif name == 'select':
        for px, py, dx, dy in [(2,2,1,1),(16,2,-1,1),(16,16,-1,-1),(2,16,1,-1)]:
            cr.move_to(px, py+dy*4); cr.line_to(px, py); cr.line_to(px+dx*4, py)
        cr.stroke()
    cr.restore()


class Editor(Gtk.ApplicationWindow):
    def __init__(self, app, pixbuf, preview=False):
        super().__init__(application=app, title='Goatshot')
        self.app = app
        self.pixbuf = pixbuf
        self.selection = None
        self.items, self.redo_items = [], []
        self.current = None
        self.tool = 'select'
        self.color = tuple(app.settings['color'])
        self.width = app.settings['width']
        self.dim = app.settings['dim']
        self.drag = None
        self.hover = None
        self.buttons = []
        self.scale, self.ox, self.oy = 1., 0., 0.
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_default_size(1100, 720)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_can_focus(True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                               Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.canvas.connect('draw', self.draw)
        self.canvas.connect('button-press-event', self.press)
        self.canvas.connect('button-release-event', self.release)
        self.canvas.connect('motion-notify-event', self.motion)
        self.canvas.connect('scroll-event', self.scroll)
        self.connect('key-press-event', self.key)
        self.connect('delete-event', lambda *_: (self.finish(), True)[1])
        self.add(self.canvas)
        if not preview:
            self.fullscreen()
        self.show_all()
        self.canvas.grab_focus()

    def transform(self):
        a = self.canvas.get_allocation()
        self.scale = min(a.width / self.pixbuf.get_width(), a.height / self.pixbuf.get_height())
        self.ox = (a.width-self.pixbuf.get_width()*self.scale)/2
        self.oy = (a.height-self.pixbuf.get_height()*self.scale)/2

    def point(self, event):
        return (max(0, min(self.pixbuf.get_width(), (event.x-self.ox)/self.scale)),
                max(0, min(self.pixbuf.get_height(), (event.y-self.oy)/self.scale)))

    def view_rect(self):
        x, y, w, h = self.selection
        return self.ox+x*self.scale, self.oy+y*self.scale, w*self.scale, h*self.scale

    def toolbar(self, cr):
        self.buttons = []
        x, y, w, h = self.view_rect()
        a = self.canvas.get_allocation()
        # Lightshot's drawing tools live immediately to the right of the selection.
        tx = x+w+4 if x+w+34 < a.width else max(2, x-34)
        ty = max(2, min(y, a.height-232))
        tools = [('pen', 'Pencil (P)'), ('line', 'Line (L)'), ('arrow', 'Arrow (A)'),
                 ('rectangle', 'Rectangle (R)'), ('marker', 'Marker (H)'), ('text', 'Text (T)'),
                 ('color', 'Color'), ('undo', 'Undo (Ctrl+Z)')]
        self.panel(cr, tx, ty, 30, len(tools)*28+4)
        for n, (name, tip) in enumerate(tools):
            self.button(cr, name, tip, tx+2, ty+2+n*28)
        # Keep the compact action strip below the lower-right corner.
        bw = 5*28+4
        bx = max(2, min(x+w-bw, a.width-bw-2))
        by = y+h+4 if y+h+34 < a.height else max(2, y+h-34)
        self.panel(cr, bx, by, bw, 32)
        for n, (name, tip) in enumerate([('upload', 'Upload to Immich and copy share link'),
                                       ('select', 'Move or resize selection (V)'),
                                       ('copy', 'Copy (Ctrl+C)'), ('save', 'Save (Ctrl+S)'),
                                       ('close', 'Close (Esc)')]):
            self.button(cr, name, tip, bx+2+n*28, by+2)

    @staticmethod
    def panel(cr, x, y, w, h):
        # Soft layered shadow, warm white surface, and a fine cool-gray edge.
        for offset, alpha in [(3, .06), (2, .08), (1, .10)]:
            cr.set_source_rgba(0, 0, 0, alpha)
            rounded_rect(cr, x-offset/2, y+offset, w+offset, h+offset/2, 6)
            cr.fill()
        cr.set_source_rgb(.975, .978, .985)
        rounded_rect(cr, x, y, w, h, 6); cr.fill_preserve()
        cr.set_source_rgb(.79, .81, .85); cr.set_line_width(1); cr.stroke()

    def button(self, cr, name, tip, x, y):
        bounds = (x, y, 26, 26)
        self.buttons.append((name, bounds, tip))
        active = self.tool == name
        hovered = self.hover == name
        if active or hovered:
            cr.set_source_rgb(*((.89, .85, .98) if active else (.99, .90, .91) if name == 'close' else (.91, .93, .96)))
            rounded_rect(cr, x+1, y+1, 24, 24, 4); cr.fill()
        if name == 'color':
            cr.set_source_rgba(0, 0, 0, .12)
            rounded_rect(cr, x+5, y+5, 16, 16, 4); cr.fill()
            cr.set_source_rgb(*self.color)
            rounded_rect(cr, x+6, y+6, 14, 14, 3); cr.fill()
        else:
            icon(cr, name, x+4, y+4, active, hovered and name == 'close')

    def draw(self, widget, cr):
        self.transform()
        a = widget.get_allocation()
        cr.set_source_rgb(.06, .06, .06); cr.paint()
        cr.save(); cr.translate(self.ox, self.oy); cr.scale(self.scale, self.scale)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0); cr.paint()
        cr.restore()
        if self.selection:
            x, y, w, h = self.view_rect()
            cr.save()
            cr.rectangle(0, 0, a.width, a.height); cr.rectangle(x, y, w, h)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.set_source_rgba(0, 0, 0, self.dim); cr.fill(); cr.restore()
            cr.save(); cr.rectangle(x, y, w, h); cr.clip()
            cr.translate(self.ox, self.oy); cr.scale(self.scale, self.scale)
            for item in self.items:
                annotation(cr, item)
            if self.current:
                annotation(cr, self.current)
            cr.restore()
            cr.set_line_width(1)
            cr.set_source_rgb(0, 0, 0); cr.rectangle(x+.5, y+.5, w, h); cr.stroke()
            cr.set_dash([3, 3]); cr.set_source_rgb(1, 1, 1)
            cr.rectangle(x+.5, y+.5, w, h); cr.stroke(); cr.set_dash([])
            for hx, hy in self.handles():
                cr.set_source_rgb(1, 1, 1); cr.rectangle(hx-2.5, hy-2.5, 5, 5); cr.fill_preserve()
                cr.set_source_rgb(.35, .35, .35); cr.stroke()
            label = f'{round(self.selection[2])} × {round(self.selection[3])}'
            cr.select_font_face('Sans'); cr.set_font_size(11)
            ext = cr.text_extents(label)
            ly = y-7 if y > 24 else y+17
            cr.set_source_rgba(0, 0, 0, .55); cr.rectangle(x, ly-13, ext.width+10, 18); cr.fill()
            cr.set_source_rgb(1, 1, 1); cr.move_to(x+5, ly); cr.show_text(label)
            if self.drag not in ('select', 'resize', 'move'):
                self.toolbar(cr)
            else:
                self.buttons = []
        else:
            cr.set_source_rgba(0, 0, 0, self.dim); cr.paint()
            cr.select_font_face('Sans'); cr.set_font_size(16)
            message = 'Select area'
            ext = cr.text_extents(message)
            cr.set_source_rgb(1, 1, 1); cr.move_to((a.width-ext.width)/2, a.height/2); cr.show_text(message)
            cr.set_font_size(11); cr.move_to((a.width-150)/2, a.height/2+24); cr.show_text('Esc to cancel · Ctrl+A for all')
        return False

    def handles(self):
        x, y, w, h = self.view_rect()
        return [(x, y), (x+w/2, y), (x+w, y), (x+w, y+h/2),
                (x+w, y+h), (x+w/2, y+h), (x, y+h), (x, y+h/2)]

    def hit_handle(self, event):
        if not self.selection:
            return None
        for n, (x, y) in enumerate(self.handles()):
            if abs(event.x-x) < 7 and abs(event.y-y) < 7:
                return n
        return None

    def press(self, widget, event):
        self.commit_text()
        if event.button == 3:
            self.tool = 'select'; self.canvas.queue_draw(); return True
        if event.button != 1:
            return False
        for name, bounds, _ in self.buttons:
            if inside((event.x, event.y), bounds):
                self.action(name); return True
        p = self.point(event)
        handle = self.hit_handle(event)
        if handle is not None:
            self.drag = 'resize'; self.handle_index = handle; self.original = self.selection
        elif not self.selection or not inside(p, self.selection):
            self.start = p; self.selection = (*p, 0, 0); self.drag = 'select'
            self.items.clear(); self.redo_items.clear(); self.tool = 'select'
        elif self.tool == 'select':
            self.drag = 'move'; self.start = p; self.original = self.selection
        elif self.tool == 'text':
            self.add_text(p)
        else:
            self.drag = 'draw'
            self.current = (self.tool, [p], self.color, self.width, '')
        self.canvas.queue_draw()
        return True

    def motion(self, widget, event):
        p = self.point(event)
        if self.drag == 'select':
            self.selection = rect(self.start, p)
        elif self.drag == 'move':
            x, y, w, h = self.original
            self.selection = (max(0, min(self.pixbuf.get_width()-w, x+p[0]-self.start[0])),
                              max(0, min(self.pixbuf.get_height()-h, y+p[1]-self.start[1])), w, h)
        elif self.drag == 'resize':
            x, y, w, h = self.original
            x2, y2 = x+w, y+h
            n = self.handle_index
            if n in (0, 6, 7): x = p[0]
            if n in (2, 3, 4): x2 = p[0]
            if n in (0, 1, 2): y = p[1]
            if n in (4, 5, 6): y2 = p[1]
            self.selection = rect((x, y), (x2, y2))
        elif self.drag == 'draw' and self.current:
            points = self.current[1]
            if self.tool in ('pen', 'marker') or len(points) == 1:
                points.append(p)
            else:
                points[-1] = p
        else:
            self.hover = None
            tip = ''
            for name, bounds, label in self.buttons:
                if inside((event.x, event.y), bounds):
                    self.hover = name; tip = label; break
            self.canvas.set_tooltip_text(tip or None)
            win = self.canvas.get_window()
            if win:
                cursor = 'pointer' if self.hover else ('move' if self.selection and inside(p, self.selection) and self.tool == 'select' else 'crosshair')
                win.set_cursor(Gdk.Cursor.new_from_name(win.get_display(), cursor))
        self.canvas.queue_draw()
        return True

    def release(self, widget, event):
        if event.button != 1:
            return False
        if self.current and self.current[0] != 'text':
            self.items.append(self.current); self.current = None; self.redo_items.clear()
        self.drag = None
        if self.selection and (self.selection[2] < 2 or self.selection[3] < 2):
            self.selection = None
        self.canvas.queue_draw()
        return True

    def scroll(self, widget, event):
        self.width = max(1, min(12, self.width + (1 if event.direction == Gdk.ScrollDirection.UP else -1)))
        return True

    def action(self, name):
        if name == 'close': self.finish()
        elif name == 'undo':
            if self.items: self.redo_items.append(self.items.pop())
        elif name == 'redo':
            if self.redo_items: self.items.append(self.redo_items.pop())
        elif name == 'copy': self.copy()
        elif name == 'save': self.save()
        elif name == 'upload': self.upload()
        elif name == 'color':
            dialog = Gtk.ColorChooserDialog(title='Color', transient_for=self)
            dialog.set_rgba(Gdk.RGBA(*self.color, 1))
            if dialog.run() == Gtk.ResponseType.OK:
                c = dialog.get_rgba(); self.color = (c.red, c.green, c.blue)
            dialog.destroy()
        else: self.tool = name
        self.canvas.queue_draw()

    def add_text(self, p):
        self.current = ('text', [p], self.color, self.width, '')
        self.canvas.grab_focus()

    def commit_text(self):
        if self.current and self.current[0] == 'text':
            if self.current[4]:
                self.items.append(self.current)
                self.redo_items.clear()
            self.current = None

    def key(self, widget, event):
        key = Gdk.keyval_name(event.keyval).lower()
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if self.current and self.current[0] == 'text':
            if key == 'escape':
                self.current = None
            elif key in ('return', 'kp_enter'):
                self.commit_text()
            elif key == 'backspace':
                self.current = (*self.current[:4], self.current[4][:-1])
            elif ctrl:
                self.commit_text()
            else:
                char = Gdk.keyval_to_unicode(event.keyval)
                if char and chr(char).isprintable():
                    self.current = (*self.current[:4], self.current[4]+chr(char))
            if not ctrl:
                self.canvas.queue_draw()
                return True
        if key == 'escape': self.finish()
        elif ctrl and key == 'a':
            self.selection = (0, 0, self.pixbuf.get_width(), self.pixbuf.get_height())
        elif ctrl and key == 'c': self.copy()
        elif ctrl and key == 's': self.save()
        elif ctrl and key == 'z': self.action('redo' if shift else 'undo')
        elif ctrl and key == 'y': self.action('redo')
        elif key in ('return', 'kp_enter'): self.copy()
        elif key in ('delete', 'backspace'):
            self.selection = None; self.items.clear(); self.redo_items.clear()
        elif key in ('left', 'right', 'up', 'down') and self.selection:
            x, y, w, h = self.selection
            dx, dy = {'left':(-1,0),'right':(1,0),'up':(0,-1),'down':(0,1)}[key]
            if shift:
                w = max(2, min(self.pixbuf.get_width()-x, w+dx))
                h = max(2, min(self.pixbuf.get_height()-y, h+dy))
            else:
                x = max(0, min(self.pixbuf.get_width()-w, x+dx))
                y = max(0, min(self.pixbuf.get_height()-h, y+dy))
            self.selection = (x, y, w, h)
        elif key in {'p','l','a','r','h','t','v'} and not ctrl:
            self.tool = {'p':'pen','l':'line','a':'arrow','r':'rectangle','h':'marker','t':'text','v':'select'}[key]
        self.canvas.queue_draw()
        return True

    def copy(self):
        self.commit_text()
        if not self.selection: return
        surface = render_crop(self.pixbuf, self.selection, self.items)
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_image(pixbuf)
        clipboard.set_can_store(None)
        # The application stays alive to serve the image on Wayland.
        self.app.clipboard_image = pixbuf
        self.finish()

    def upload(self):
        self.commit_text()
        if not self.selection: return
        from uploads import lookup_key, upload_and_share, UploadError, validate_slug, server_url
        options = Gtk.Dialog(title='Upload screenshot', transient_for=self, modal=True)
        options.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Upload', Gtk.ResponseType.OK)
        options.set_default_response(Gtk.ResponseType.OK)
        content = options.get_content_area()
        content.set_border_width(18); content.set_spacing(12)
        try:
            base = server_url(self.app.settings['immich_url'])
        except ValueError as e:
            options.destroy()
            self.app.error('Configure Immich first', str(e), self)
            return
        automatic = Gtk.Label(label='Automatic short link (example): '+base+'/s/abc123', xalign=0)
        content.add(automatic)
        vanity = Gtk.CheckButton(label='Custom vanity URL')
        vanity.set_active(False)
        content.add(vanity)
        custom = Gtk.Entry()
        custom.set_placeholder_text('e.g. project-preview')
        custom.set_sensitive(False)
        custom.set_activates_default(True)
        content.add(custom)
        preview = Gtk.Label(label='', xalign=0)
        preview.set_selectable(True)
        content.add(preview)
        def update_preview(*_):
            custom.set_sensitive(vanity.get_active())
            automatic.set_visible(not vanity.get_active())
            preview.set_text(base+'/s/'+custom.get_text() if vanity.get_active() else '')
        vanity.connect('toggled', update_preview)
        custom.connect('changed', update_preview)
        options.show_all()
        custom_slug = None
        while True:
            if options.run() != Gtk.ResponseType.OK:
                options.destroy(); return
            if not vanity.get_active(): break
            try:
                custom_slug = validate_slug(custom.get_text().strip())
            except ValueError as e:
                self.app.error('Choose a vanity URL', str(e), options)
            else: break
        options.destroy()
        output = io.BytesIO()
        render_crop(self.pixbuf, self.selection, self.items).write_to_png(output)
        png = output.getvalue()
        settings = dict(self.app.settings)
        progress = Gtk.Dialog(title='Upload screenshot', transient_for=self, modal=True)
        progress.set_deletable(False)
        content = progress.get_content_area()
        content.set_border_width(24); content.set_spacing(12)
        spinner = Gtk.Spinner(); spinner.start(); content.add(spinner)
        content.add(Gtk.Label(label='Uploading to Immich and creating a share link…'))
        progress.show_all()
        def complete(link, error):
            progress.destroy()
            if error:
                self.app.error('Upload failed', error, self)
            else:
                self.finish()
                self.app.show_share_link(link)
            return GLib.SOURCE_REMOVE
        def work():
            try:
                key = lookup_key(settings['immich_url'])
                link = upload_and_share(png, settings['immich_url'], key, settings['link_expiry_days'], custom_slug=custom_slug)
            except Exception as e:
                message = str(e) if isinstance(e, (UploadError, ValueError)) else 'Upload failed. Check your desktop keyring and Immich settings.'
                GLib.idle_add(complete, None, message)
            else:
                GLib.idle_add(complete, link, None)
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        self.commit_text()
        if not self.selection: return
        dialog = Gtk.FileChooserDialog(title='Save screenshot', transient_for=self,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Save', Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        folder = Path(self.app.settings['save_folder'])
        if folder.is_dir(): dialog.set_current_folder(str(folder))
        dialog.set_current_name('Screenshot '+datetime.now().strftime('%Y-%m-%d %H-%M-%S')+'.png')
        f = Gtk.FileFilter(); f.set_name('PNG image'); f.add_pattern('*.png'); dialog.add_filter(f)
        if dialog.run() == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if not filename.lower().endswith('.png'): filename += '.png'
            try:
                render_crop(self.pixbuf, self.selection, self.items).write_to_png(filename)
            except Exception as e:
                self.app.error('Could not save screenshot', str(e), self)
            else:
                dialog.destroy()
                if self.app.settings['copy_after_save']: self.copy()
                else: self.finish()
                return
        dialog.destroy()

    def finish(self):
        self.app.editor = None
        self.destroy()


class Goatshot(Gtk.Application):
    def __init__(self, image=None, preview=False, demo=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.image, self.preview, self.demo = image, preview, demo
        self.editor = None
        self.bus = None
        self.pending = False
        self.subscription = None
        self.timeout = None
        self.clipboard_image = None
        self.settings = load_settings()
        self.settings_window = None
        self.tray = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        GLib.set_application_name('Goatshot')
        self.refresh_icon()
        try:
            self.tray = Tray(self)
        except Exception as e:
            print('Could not create Goatshot tray icon:', e, file=sys.stderr)

    def refresh_icon(self):
        selected = self.settings.get('icon', 'neon')
        pixbuf = load_image(str(icon_path(selected)))
        Gtk.Window.set_default_icon(pixbuf)
        for window in self.get_windows(): window.set_icon(pixbuf)
        update_desktop_icon(selected)
        if self.tray: self.tray.refresh()

    def do_shutdown(self):
        if self.tray: self.tray.close()
        self.cleanup()
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, command_line):
        args = argument_parser().parse_args(command_line.get_arguments()[1:])
        if args.quit:
            self.quit()
        elif args.settings:
            self.show_settings()
        elif not args.background:
            self.image, self.preview, self.demo = args.image, args.windowed, args.demo
            self.activate()
        return 0

    def show_settings(self):
        if self.settings_window:
            self.settings_window.present()
        else:
            self.settings_window = SettingsWindow(self)


    def do_activate(self):
        if self.editor:
            self.editor.present(); return
        if self.pending: return
        if self.image:
            self.open_image(self.image); return
        self.capture()

    def show_share_link(self, link):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(link, -1)
        self.clipboard_image = None
        dialog = Gtk.Dialog(title='Screenshot uploaded', modal=False)
        dialog.set_application(self)
        dialog.set_default_size(520, 120)
        content = dialog.get_content_area(); content.set_border_width(18); content.set_spacing(12)
        content.add(Gtk.Label(label='Immich share link copied to clipboard.', xalign=0))
        entry = Gtk.Entry(); entry.set_text(link); entry.set_editable(False)
        content.add(entry)
        dialog.add_button('Copy link', 1)
        dialog.add_button('Open link', 2)
        dialog.add_button('Close', Gtk.ResponseType.CLOSE)
        def response(window, code):
            if code == 1:
                clipboard.set_text(link, -1)
            elif code == 2:
                Gtk.show_uri_on_window(window, link, Gdk.CURRENT_TIME)
            else:
                window.destroy()
        dialog.connect('response', response)
        dialog.show_all(); entry.select_region(0, -1)

    def open_image(self, filename):
        try:
            pixbuf = load_image(filename)
            self.editor = Editor(self, pixbuf, self.preview)
            if self.demo:
                self.editor.selection = (pixbuf.get_width()*.2, pixbuf.get_height()*.2,
                                         pixbuf.get_width()*.55, pixbuf.get_height()*.55)
                self.editor.tool = 'arrow'
        except Exception as e:
            self.error('Could not open screenshot', str(e))

    def capture(self):
        self.pending = True
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            token = 'goatshot_'+uuid.uuid4().hex
            sender = self.bus.get_unique_name()[1:].replace('.', '_')
            self.request_path = '/org/freedesktop/portal/desktop/request/'+sender+'/'+token
            self.subscription = self.bus.signal_subscribe(
                'org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request', 'Response',
                None, None, Gio.DBusSignalFlags.NONE, self.response)
            options = {'handle_token': GLib.Variant('s', token),
                       'interactive': GLib.Variant('b', False), 'modal': GLib.Variant('b', False)}
            self.bus.call('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                          'org.freedesktop.portal.Screenshot', 'Screenshot',
                          GLib.Variant('(sa{sv})', ('', options)), GLib.VariantType.new('(o)'),
                          Gio.DBusCallFlags.NONE, 120000, None, self.requested)
            self.timeout = GLib.timeout_add_seconds(120, self.timed_out)
        except Exception as e:
            self.cleanup(); self.error('Could not request a screenshot', str(e))

    def requested(self, bus, result):
        try:
            path, = bus.call_finish(result).unpack()
            if self.pending: self.request_path = path
        except Exception as e:
            self.cleanup(); self.error('Could not request a screenshot', str(e))

    def response(self, bus, sender, path, iface, signal, parameters):
        if not self.pending or path != self.request_path: return
        code, results = parameters.unpack()
        self.cleanup()
        if code == 1: return  # User cancelled; no error dialog.
        if code != 0:
            self.error('Screenshot failed', 'KDE could not provide a screenshot. Please try again.'); return
        uri = results.get('uri')
        if not uri:
            self.error('Screenshot failed', 'The screenshot service returned no image.'); return
        filename = Gio.File.new_for_uri(uri).get_path()
        if not filename:
            self.error('Screenshot failed', 'The screenshot service returned an unsupported image location.'); return
        self.open_image(filename)

    def cleanup(self):
        self.pending = False
        if self.subscription is not None:
            self.bus.signal_unsubscribe(self.subscription); self.subscription = None
        if self.timeout is not None:
            GLib.source_remove(self.timeout); self.timeout = None

    def timed_out(self):
        self.timeout = None
        self.bus.call('org.freedesktop.portal.Desktop', self.request_path,
                      'org.freedesktop.portal.Request', 'Close', None, None,
                      Gio.DBusCallFlags.NONE, 5000, None, None)
        self.cleanup()
        self.error('Screenshot timed out', 'Try again and approve the screenshot permission prompt.')
        return GLib.SOURCE_REMOVE

    def error(self, title, message, parent=None):
        print(title+': '+message, file=sys.stderr)
        dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                                   message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE,
                                   text=title)
        dialog.format_secondary_text(message); dialog.run(); dialog.destroy()


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', help='Edit an existing image instead of capturing the desktop')
    parser.add_argument('--windowed', action='store_true', help='Open a window instead of fullscreen')
    parser.add_argument('--settings', action='store_true', help='Open settings')
    parser.add_argument('--background', action='store_true', help='Start in the system tray without capturing')
    parser.add_argument('--quit', action='store_true', help='Quit the running application')
    parser.add_argument('--demo', action='store_true', help=argparse.SUPPRESS)
    return parser


def main():
    argument_parser().parse_args()
    GLib.set_prgname('goatshot')
    if not Gdk.Display.get_default():
        print('Goatshot needs access to your desktop. Launch it from the application menu or a desktop terminal.', file=sys.stderr)
        return 1
    return Goatshot().run(sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
