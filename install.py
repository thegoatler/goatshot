#!/usr/bin/env python3
"""Install Goatshot for the current user, without administrator permissions."""
from pathlib import Path
import shlex
import shutil

SOURCE = Path(__file__).resolve().parent
FILES = ('goatshot.py', 'desktop.py', 'uploads.py', 'branding.py')


def install(home=None):
    home = Path(home or Path.home())
    prefix = home/'.local'
    app = prefix/'share/goatshot'
    commands = prefix/'bin'
    entries = prefix/'share/applications'
    for folder in (app/'assets', commands, entries): folder.mkdir(parents=True, exist_ok=True)
    for name in FILES: shutil.copy2(SOURCE/name, app/name)
    for asset in (SOURCE/'assets').glob('*.png'):
        shutil.copy2(asset, app/'assets'/asset.name)
    theme = prefix/'share/icons/hicolor'
    themed_icon = theme/'256x256/apps/goatshot.png'
    themed_icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE/'assets/goatshot.png', themed_icon)
    legacy_icon = theme/'scalable/apps/goatshot.svg'
    if legacy_icon.exists():
        backup = home/'.local/state/goatshot/legacy-goatshot.svg'
        backup.parent.mkdir(parents=True, exist_ok=True)
        number = 1
        while backup.exists():
            backup = backup.with_name(f'legacy-goatshot-{number}.svg')
            number += 1
        legacy_icon.rename(backup)
    launcher = commands/'goatshot'
    launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(app/'goatshot.py'))+' "$@"\n')
    launcher.chmod(0o755)
    restart = commands/'goatshot-restart'
    restart.write_text('''#!/bin/sh
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/goatshot"
mkdir -p "$state_dir" || exit 1
'''+shlex.quote(str(launcher))+''' --quit >/dev/null 2>&1 || :
sleep 0.3
setsid --fork '''+shlex.quote(str(launcher))+''' --background </dev/null >>"$state_dir/goatshot.log" 2>&1
''')
    restart.chmod(0o755)
    # Stable identity preserves shortcuts from the app's initial Snaplight name.
    entry = entries/'io.github.isaac.Snaplight.desktop'
    entry.write_text('''[Desktop Entry]
Type=Application
Name=Goatshot
GenericName=Screenshot Tool
Comment=Select, annotate, copy, save and share screenshots
Exec="'''+str(launcher).replace('\\','\\\\').replace('"','\\"')+'''"
Icon='''+str(app/'assets/goatshot.png')+'''
Terminal=false
StartupNotify=false
Categories=Graphics;
Keywords=screenshot;capture;goatshot;thegoatler;
Actions=Settings;

[Desktop Action Settings]
Name=Settings
Exec="'''+str(launcher).replace('\\','\\\\').replace('"','\\"')+'''" --settings
''')
    import json
    from branding import ICONS
    config = home/'.config/goatshot/settings.json'
    try: selected = json.loads(config.read_text()).get('icon', 'neon')
    except (OSError, ValueError, AttributeError): selected = 'neon'
    # Use installed assets in launchers, rather than the source checkout.
    if not isinstance(selected, str): selected = 'neon'
    selected_name = ICONS.get(selected, ICONS['neon'])[1]
    icon = app/'assets'/selected_name
    entry.write_text(entry.read_text().replace(str(app/'assets/goatshot.png'), str(icon)))
    shutil.copy2(icon, themed_icon)
    return app


if __name__ == '__main__':
    print('Installed Goatshot in', install())
    print('Launch ~/.local/bin/goatshot, or restart with ~/.local/bin/goatshot-restart')
