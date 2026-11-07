"""KDE tray integration and persistent preferences for Goatshot."""
import json
import math
from functools import lru_cache
from PIL import Image
from pathlib import Path
from branding import ICONS, icon_path, update_desktop_icon
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Dbusmenu', '0.4')
from gi.repository import Gtk, Gdk, Gio, GLib, Dbusmenu

DEFAULTS = {'color': [1.0, 0.0, 0.0], 'width': 3, 'dim': 0.48,
            'save_folder': str(Path.home()/'Pictures'), 'copy_after_save': False,
            'immich_url': '', 'link_expiry_days': 0, 'icon': 'neon'}
CONFIG = Path(GLib.get_user_config_dir())/'goatshot/settings.json'
AUTOSTART = Path(GLib.get_user_config_dir())/'autostart/goatshot.desktop'
LAUNCHER = Path.home()/'.local/bin/goatshot'


def validated(values):
    result = dict(DEFAULTS)
    if not isinstance(values, dict): return result
    color = values.get('color')
    if isinstance(color, list) and len(color) == 3 and all(type(c) in (float, int) and math.isfinite(c) and 0 <= c <= 1 for c in color):
        result['color'] = color
    width = values.get('width')
    if type(width) is int and 1 <= width <= 12: result['width'] = width
    dim = values.get('dim')
    if type(dim) in (int, float) and math.isfinite(dim) and 0 <= dim <= .85: result['dim'] = dim
    folder = values.get('save_folder')
    if isinstance(folder, str) and Path(folder).is_absolute(): result['save_folder'] = folder
    if type(values.get('copy_after_save')) is bool: result['copy_after_save'] = values['copy_after_save']
    url = values.get('immich_url')
    if isinstance(url, str): result['immich_url'] = url
    days = values.get('link_expiry_days')
    if type(days) is int and 0 <= days <= 365: result['link_expiry_days'] = days
    if isinstance(values.get('icon'), str) and values['icon'] in ICONS:
        result['icon'] = values['icon']
    return result


def load_settings(path=CONFIG):
    try: return validated(json.loads(path.read_text()))
    except (OSError, ValueError): return dict(DEFAULTS)


def save_settings(settings, path=CONFIG):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(validated(settings), indent=2)+'\n')
    temporary.replace(path)


def set_autostart(enabled, path=AUTOSTART):
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[Desktop Entry]\nType=Application\nName=Goatshot\n'
                        f'Exec={LAUNCHER} --background\nIcon=goatshot\nTerminal=false\n')
    else:
        path.unlink(missing_ok=True)


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Goatshot settings')
        self.app = app
        self.set_default_size(440, 340)
        self.set_resizable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_border_width(20)
        self.add(box)
        heading = Gtk.Label(label='Goatshot', xalign=0)
        heading.set_markup('<span size="large" weight="bold">Goatshot settings</span>')
        box.pack_start(heading, False, False, 0)
        grid = Gtk.Grid(column_spacing=20, row_spacing=14)
        box.pack_start(grid, True, True, 0)
        def row(n, label, control):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, n, 1, 1)
            control.set_hexpand(True); grid.attach(control, 1, n, 1, 1)
        prefs = app.settings
        self.icon_choice = Gtk.ComboBoxText()
        for key, (label, filename) in ICONS.items(): self.icon_choice.append(key, label)
        self.icon_choice.set_active_id(prefs['icon'])
        row(8, 'App icon', self.icon_choice)
        self.color = Gtk.ColorButton()
        self.color.set_rgba(Gdk.RGBA(*prefs['color'], 1))
        row(0, 'Default drawing color', self.color)
        self.width = Gtk.SpinButton.new_with_range(1, 12, 1)
        self.width.set_value(prefs['width']); row(1, 'Line thickness', self.width)
        self.dim = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 85, 1)
        self.dim.set_value(prefs['dim']*100); self.dim.set_digits(0)
        row(2, 'Background dimming (%)', self.dim)
        self.folder = Gtk.FileChooserButton(title='Screenshot folder', action=Gtk.FileChooserAction.SELECT_FOLDER)
        if Path(prefs['save_folder']).is_dir(): self.folder.set_filename(prefs['save_folder'])
        row(3, 'Save screenshots in', self.folder)
        self.immich_url = Gtk.Entry()
        self.immich_url.set_text(prefs['immich_url'])
        self.immich_url.set_placeholder_text('https://photos.example.com')
        row(4, 'Immich server', self.immich_url)
        self.api_key = Gtk.Entry()
        self.api_key.set_visibility(False)
        self.api_key.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.api_key.set_placeholder_text('Leave blank to keep saved key')
        row(5, 'Immich API key', self.api_key)
        self.expiry = Gtk.SpinButton.new_with_range(0, 365, 1)
        self.expiry.set_value(prefs['link_expiry_days'])
        row(6, 'Link expiry (days, 0 = never)', self.expiry)
        help_label = Gtk.Label(label='API key: asset.upload, sharedLink.create; asset.update for duplicates', xalign=0)
        help_label.set_line_wrap(True)
        grid.attach(help_label, 0, 7, 2, 1)
        self.copy = Gtk.CheckButton(label='Copy to clipboard after saving')
        self.copy.set_active(prefs['copy_after_save']); box.pack_start(self.copy, False, False, 0)
        self.autostart = Gtk.CheckButton(label='Start in the system tray when I log in')
        self.autostart.set_active(AUTOSTART.exists()); box.pack_start(self.autostart, False, False, 0)
        label = Gtk.Label(label='Capture shortcut: Print Screen · Change it in KDE System Settings', xalign=0)
        label.set_line_wrap(True); box.pack_start(label, False, False, 0)
        buttons = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
        buttons.set_layout(Gtk.ButtonBoxStyle.END)
        cancel = Gtk.Button(label='Cancel'); cancel.connect('clicked', lambda *_: self.destroy())
        save = Gtk.Button(label='Save'); save.connect('clicked', self.save)
        buttons.add(cancel); buttons.add(save); box.pack_start(buttons, False, False, 0)
        self.connect('destroy', lambda *_: setattr(app, 'settings_window', None))
        self.show_all()

    def save(self, *_):
        c = self.color.get_rgba()
        settings = {'color': [c.red, c.green, c.blue], 'width': self.width.get_value_as_int(),
                    'dim': self.dim.get_value()/100, 'save_folder': self.folder.get_filename() or self.app.settings['save_folder'],
                    'copy_after_save': self.copy.get_active(),
                    'immich_url': self.immich_url.get_text().strip(),
                    'link_expiry_days': self.expiry.get_value_as_int(),
                    'icon': self.icon_choice.get_active_id() or 'neon'}
        try:
            from uploads import server_url, store_key
            if settings['immich_url']:
                settings['immich_url'] = server_url(settings['immich_url'])
            elif self.api_key.get_text().strip():
                raise ValueError('Enter an Immich server URL before saving an API key.')
            if self.api_key.get_text().strip():
                store_key(settings['immich_url'], self.api_key.get_text())
            set_autostart(self.autostart.get_active())
            save_settings(settings)
        except Exception as e:
            self.app.error('Could not save settings', str(e), self); return
        self.app.settings = validated(settings)
        try:
            self.app.refresh_icon()
        except Exception as e:
            self.app.error('Settings saved, but the icon could not refresh', str(e), self)
            return
        self.destroy()


SNI_XML = '''<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="IconThemePath" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="OverlayIconName" type="s" access="read"/>
<property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionIconName" type="s" access="read"/>
<property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionMovieName" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>'''


@lru_cache(maxsize=3)
def tray_pixmaps(icon_name='neon'):
    # StatusNotifierItem requires network-order ARGB bytes, not RGBA or native Cairo pixels.
    result = []
    with Image.open(icon_path(icon_name)) as original:
        source = original.convert('RGBA')
        for size in (22, 32, 48, 64):
            rgba = source.resize((size, size), Image.Resampling.LANCZOS).tobytes()
            argb = bytearray(len(rgba))
            argb[0::4], argb[1::4], argb[2::4], argb[3::4] = rgba[3::4], rgba[0::4], rgba[1::4], rgba[2::4]
            result.append((size, size, bytes(argb)))
    return result


class Tray:
    """Export a native StatusNotifierItem and a DBusMenu, supported by KDE Wayland."""
    def __init__(self, app):
        self.app = app
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.server = Dbusmenu.Server.new('/MenuBar')
        root = Dbusmenu.Menuitem.new()
        for label, action in [('Take screenshot', app.activate), ('Settings…', app.show_settings), ('Quit', app.quit)]:
            item = Dbusmenu.Menuitem.new()
            item.property_set('label', label)
            item.connect('item-activated', self.menu_action, action)
            root.child_append(item)
        self.server.set_root(root)
        self.root = root
        info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self.object_id = self.bus.register_object('/StatusNotifierItem', info, self.method, self.property, None)
        self.watch_id = Gio.bus_watch_name_on_connection(
            self.bus, 'org.kde.StatusNotifierWatcher', Gio.BusNameWatcherFlags.NONE, self.register, None)

    @staticmethod
    def menu_action(item, timestamp, action):
        # Let KDE dismiss its menu before asking for a screenshot.
        def run(): action(); return GLib.SOURCE_REMOVE
        GLib.timeout_add(180, run)

    def register(self, connection, name, owner):
        connection.call('org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
                        'org.kde.StatusNotifierWatcher', 'RegisterStatusNotifierItem',
                        GLib.Variant('(s)', ('/StatusNotifierItem',)), None,
                        Gio.DBusCallFlags.NONE, 5000, None, self.registered)

    @staticmethod
    def registered(bus, result):
        try:
            bus.call_finish(result)
            # Force hosts to refresh previously cached icons after an app upgrade.
            bus.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'NewIcon', None)
            bus.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'NewToolTip', None)
        except GLib.Error as e: print('Could not register Goatshot tray icon:', e, file=__import__('sys').stderr)

    def property(self, connection, sender, path, interface, name):
        selected = self.app.settings.get('icon', 'neon')
        values = {'Category': ('s', 'ApplicationStatus'), 'Id': ('s', 'goatshot'),
                  'Title': ('s', 'Goatshot'), 'Status': ('s', 'Active'), 'WindowId': ('i', 0),
                  'IconName': ('s', ''),
                  'IconThemePath': ('s', ''),
                  'IconPixmap': ('a(iiay)', tray_pixmaps(selected)), 'OverlayIconName': ('s', ''),
                  'OverlayIconPixmap': ('a(iiay)', []), 'AttentionIconName': ('s', ''),
                  'AttentionIconPixmap': ('a(iiay)', []), 'AttentionMovieName': ('s', ''),
                  'ToolTip': ('(sa(iiay)ss)', ('', tray_pixmaps(selected), 'Goatshot', 'Take a screenshot or open settings')),
                  'ItemIsMenu': ('b', False), 'Menu': ('o', '/MenuBar')}
        if name in values: return GLib.Variant(*values[name])
        return None

    def method(self, connection, sender, path, interface, method, parameters, invocation):
        invocation.return_value(None)
        if method == 'Activate': self.menu_action(None, 0, self.app.activate)
        elif method in ('SecondaryActivate', 'ContextMenu'): self.menu_action(None, 0, self.app.show_settings)

    def refresh(self):
        for signal in ('NewIcon', 'NewToolTip'):
            self.bus.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', signal, None)

    def close(self):
        Gio.bus_unwatch_name(self.watch_id)
        self.bus.unregister_object(self.object_id)
