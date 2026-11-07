"""Bundled icon choices and desktop-entry updates."""
from pathlib import Path
import shutil

ICONS = {'neon': ('Neon', 'goatshot.png'),
         'aperture': ('Aperture', 'goatshot-aperture.png'),
         'mountain': ('Mountain Goat', 'goatshot-mountain.png')}


def icon_path(name='neon'):
    return Path(__file__).resolve().parent/'assets'/ICONS.get(name, ICONS['neon'])[1]


def update_desktop_icon(name, home=None):
    home = Path(home or Path.home())
    path = icon_path(name)
    entry = home/'.local/share/applications/io.github.isaac.Snaplight.desktop'
    if entry.is_file():
        text = entry.read_text()
        entry.write_text('\n'.join('Icon='+str(path) if line.startswith('Icon=') else line
                                    for line in text.splitlines())+'\n')
    themed = home/'.local/share/icons/hicolor/256x256/apps/goatshot.png'
    if themed.parent.is_dir(): shutil.copy2(path, themed)
