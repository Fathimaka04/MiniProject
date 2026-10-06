"""
GazeAssist — create desktop shortcuts so nobody needs a terminal.

Run once (by whoever installs GazeAssist on the patient's computer):

    python setup_shortcuts.py               desktop icons only
    python setup_shortcuts.py --autostart   ...and start GazeAssist when Windows logs in
    python setup_shortcuts.py --remove      remove everything this script created

What it creates
  Desktop\\GazeAssist.lnk            starts the dashboard + gaze app with no console
                                    windows (pythonw run_all.py --background)
  Desktop\\GazeAssist Dashboard.url  opens the caregiver dashboard in the browser
  Startup\\GazeAssist.lnk            (with --autostart) same as the desktop icon,
                                    run automatically at Windows login

Nothing else on the system is changed. Windows only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "assets" / "gazeassist.ico"
APP_NAME = "GazeAssist"
DASHBOARD_URL = "http://127.0.0.1:8000/"


def windows_folder(name: str) -> Path:
    """Real Desktop/Startup folder (handles OneDrive-redirected desktops)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def find_pythonw() -> Path:
    """pythonw.exe of the gaze app's venv (no console window)."""
    for venv in (ROOT / ".venv", ROOT / "gazeassist" / ".venv"):
        exe = venv / "Scripts" / "pythonw.exe"
        if exe.exists():
            return exe
    exe = Path(sys.executable).with_name("pythonw.exe")
    if exe.exists():
        return exe
    sys.exit("Could not find pythonw.exe — create the gaze app's virtual environment first (miniproject\\.venv).")


def make_icon() -> str:
    """Draw a simple eye icon (needs Pillow, which the gaze app already has)."""
    if ICON.exists():
        return str(ICON)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return r"%SystemRoot%\System32\shell32.dll,22"
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Rounded square with a blue -> teal vertical gradient.
    gradient = Image.new("RGBA", (size, size))
    top, bottom = (59, 130, 246), (20, 184, 166)
    for y in range(size):
        t = y / (size - 1)
        colour = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        ImageDraw.Draw(gradient).line([(0, y), (size, y)], fill=colour)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8, 8, size - 8, size - 8], radius=56, fill=255)
    img.paste(gradient, (0, 0), mask)
    # Eye: white almond, navy iris, white glint.
    draw.ellipse([36, 78, 220, 178], fill=(255, 255, 255, 255))
    draw.ellipse([92, 92, 164, 164], fill=(15, 23, 42, 255))
    draw.ellipse([132, 104, 150, 122], fill=(255, 255, 255, 255))
    ICON.parent.mkdir(exist_ok=True)
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return str(ICON)


def create_shortcut(path: Path, target: Path, arguments: str, workdir: Path, icon: str, description: str) -> None:
    """Make a .lnk with Windows Script Host (values passed via env vars: no quoting problems)."""
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:GA_LNK);"
        "$s.TargetPath = $env:GA_TARGET; $s.Arguments = $env:GA_ARGS;"
        "$s.WorkingDirectory = $env:GA_WD; $s.IconLocation = $env:GA_ICON;"
        "$s.Description = $env:GA_DESC; $s.WindowStyle = 1; $s.Save()"
    )
    env = dict(os.environ, GA_LNK=str(path), GA_TARGET=str(target), GA_ARGS=arguments,
               GA_WD=str(workdir), GA_ICON=icon, GA_DESC=description)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)


def create_url_shortcut(path: Path, url: str, icon: str) -> None:
    icon_lines = f"IconFile={icon}\nIconIndex=0\n" if icon.lower().endswith(".ico") else ""
    path.write_text(f"[InternetShortcut]\nURL={url}\n{icon_lines}", encoding="utf-8")


def main() -> int:
    if os.name != "nt":
        print("setup_shortcuts.py is for Windows. On other systems run:  python run_all.py")
        return 1
    parser = argparse.ArgumentParser(description="Create GazeAssist desktop shortcuts.")
    parser.add_argument("--autostart", action="store_true", help="also start GazeAssist when Windows logs in")
    parser.add_argument("--remove", action="store_true", help="remove the shortcuts this script created")
    args = parser.parse_args()

    desktop = windows_folder("Desktop")
    startup = windows_folder("Startup")
    app_lnk = desktop / f"{APP_NAME}.lnk"
    dash_url = desktop / f"{APP_NAME} Dashboard.url"
    startup_lnk = startup / f"{APP_NAME}.lnk"

    if args.remove:
        for path in (app_lnk, dash_url, startup_lnk):
            if path.exists():
                path.unlink()
                print(f"removed  {path}")
        return 0

    pythonw = find_pythonw()
    icon = make_icon()
    arguments = f'"{ROOT / "run_all.py"}" --background'
    description = "Start GazeAssist (caregiver dashboard + eye-gaze app)"

    create_shortcut(app_lnk, pythonw, arguments, ROOT, icon, description)
    print(f"created  {app_lnk}")
    create_url_shortcut(dash_url, DASHBOARD_URL, icon)
    print(f"created  {dash_url}")

    if args.autostart:
        create_shortcut(startup_lnk, pythonw, arguments, ROOT, icon, description)
        print(f"created  {startup_lnk}  (starts at Windows login)")
    elif startup_lnk.exists():
        print(f"note: auto-start is still on ({startup_lnk}); remove it with --remove or delete that file")

    print("\nDone. Double-click 'GazeAssist' on the desktop to start everything.")
    print(f"Logs (if something goes wrong): {ROOT / 'logs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
