#!/usr/bin/env python3
"""
build_linux.py — Lingo Hunter AI build script for Linux (X11), via PyInstaller.

Usage:
    python3 build_linux.py

────────────────────────────────────────────────────────────────────────────
SYSTEM DEPENDENCIES — required on every machine that runs the app:

  Debian / Ubuntu:   sudo apt install xclip python3-tk python3-gi
                     sudo apt install gir1.2-ayatana-appindicator3-0.1
  Fedora / RHEL:     sudo dnf install xclip python3-tkinter python3-gobject
                     sudo dnf install libayatana-appindicator-gtk3
  Arch Linux:        sudo pacman -S xclip tk python-gobject
                     sudo pacman -S libayatana-appindicator

  xclip                  — system clipboard (pyperclip)
  python3-tk             — Tkinter widgets (customtkinter)
  python3-gi              — PyGObject: needed by pystray for the tray icon
  ayatana / appindicator — tray indicator support

NOTE: Lingo Hunter AI requires an X11 (or XWayland-backed) session. It will
refuse to start its hotkey engine under native Wayland — see
lh_automation.enforce_linux_subsystem_guard().
────────────────────────────────────────────────────────────────────────────
"""
import os
import re
import sys
import subprocess
import shutil


_DEPS_NOTICE = """\
┌──────────────────────────────────────────────────────────────────────────┐
│  SYSTEM DEPENDENCIES  (required on every Linux machine running the app)  │
│                                                                          │
│  Debian / Ubuntu:                                                        │
│    sudo apt install xclip python3-tk python3-gi                          │
│    sudo apt install gir1.2-ayatana-appindicator3-0.1                    │
│                                                                          │
│  Fedora / RHEL:                                                          │
│    sudo dnf install xclip python3-tkinter python3-gobject               │
│    sudo dnf install libayatana-appindicator-gtk3                         │
│                                                                          │
│  Arch Linux:                                                             │
│    sudo pacman -S xclip tk python-gobject libayatana-appindicator        │
│                                                                          │
│  xclip        — clipboard (pyperclip)                                   │
│  python3-tk   — Tkinter / customtkinter GUI                              │
│  python3-gi   — PyGObject (pystray: tray icon)                          │
│  appindicator — tray indicator on GNOME / KDE / XFCE                    │
│                                                                          │
│  Native Wayland sessions are not supported — use X11 or XWayland.       │
└──────────────────────────────────────────────────────────────────────────┘"""


def read_app_version(script_dir: str) -> str:
    fallback = "1.0.0"
    version_file = os.path.join(script_dir, "src", "lh_version.py")
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'APP_VERSION\s*=\s*["\']([0-9]+(?:\.[0-9]+)*)["\']', content)
        if match:
            return match.group(1)
    except Exception as exc:
        print(f"[Version]: Could not read lh_version.py ({exc}). Using {fallback}.")
    return fallback


def build() -> None:
    if sys.platform == "win32":
        print("[Error]: build_linux.py is for Linux. Use build_exe.py on Windows.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"[0/4] Working directory: {script_dir}")
    print()
    print(_DEPS_NOTICE)
    print()

    version_str = read_app_version(script_dir)
    app_name = "lingo-hunter-ai"

    print("\n[1/4] Installing build dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--upgrade",
            "pyinstaller", "customtkinter", "pillow", "plyer",
            "pynput", "pystray", "pyperclip",
        ],
        check=True,
    )

    print("\n[2/4] Locating CustomTkinter assets...")
    try:
        import customtkinter
        ctk_path = os.path.dirname(customtkinter.__file__)
        ctk_data_arg = f"{ctk_path}{os.path.pathsep}customtkinter"
    except ImportError:
        print("[Error]: customtkinter not found — pip install customtkinter.")
        return

    main_script = os.path.join("src", "main_app.py")
    if not os.path.exists(main_script):
        print(f"[Error]: Entry point {main_script!r} not found in {script_dir}.")
        return

    logo_file = os.path.join("assets", "logo.png")
    icon_ico = "icon.ico"

    print(f"\n[3/4] Running PyInstaller (v{version_str}, target={app_name})...")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--noconsole",
        f"--name={app_name}",
        f"--add-data={ctk_data_arg}",
        "--paths=src",

        "--collect-submodules=pynput",
        "--hidden-import=pynput.keyboard",
        "--hidden-import=pynput.mouse",
        "--hidden-import=pynput._util.xorg",
        "--hidden-import=pynput.keyboard._xorg",
        "--hidden-import=pynput.mouse._xorg",

        "--hidden-import=pystray",
        "--hidden-import=pystray._gtk",

        "--hidden-import=pyperclip",
    ]

    if os.path.exists(logo_file):
        cmd += [f"--icon={logo_file}", f"--add-data={logo_file}{os.path.pathsep}."]
        print(f"-> Logo/icon: {logo_file}")
    else:
        print(f"[Warning]: {logo_file} not found — using default icon.")

    if os.path.exists(icon_ico):
        cmd.append(f"--add-data={icon_ico}{os.path.pathsep}.")

    cmd.append(main_script)

    print(f"\nCommand: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("\n" + "=" * 50)
        print("BUILD FAILED!")
        print("=" * 50)
        print("STDOUT:")
        print(result.stdout)
        print("\nSTDERR:")
        print(result.stderr)
        print("=" * 50)
        return

    dist_app_dir = os.path.join(script_dir, "dist", app_name)
    src_bin = os.path.join(dist_app_dir, app_name)
    src_internal = os.path.join(dist_app_dir, "_internal")

    parent_dir = os.path.dirname(script_dir)
    target_dir = os.path.join(parent_dir, "Lingo Hunter AI Linux")
    if not os.path.isdir(parent_dir) or not os.access(parent_dir, os.W_OK):
        target_dir = os.path.join(script_dir, "dist_output")

    print(f"\n[4/4] Moving output to: {target_dir}...")
    try:
        os.makedirs(target_dir, exist_ok=True)

        dest_bin = os.path.join(target_dir, app_name)
        if os.path.exists(dest_bin):
            os.remove(dest_bin)
        shutil.move(src_bin, dest_bin)
        os.chmod(dest_bin, 0o755)
        print(f"-> Binary: {dest_bin}")

        dest_internal = os.path.join(target_dir, "_internal")
        if os.path.exists(dest_internal):
            shutil.rmtree(dest_internal)
        shutil.move(src_internal, dest_internal)
        print("-> Dependencies: _internal/")

        print("\n[Cleanup]...")
        shutil.rmtree(os.path.join(script_dir, "build"), ignore_errors=True)
        shutil.rmtree(os.path.join(script_dir, "dist"), ignore_errors=True)
        spec_file = os.path.join(script_dir, f"{app_name}.spec")
        if os.path.exists(spec_file):
            os.remove(spec_file)

        print("\n" + "=" * 50)
        print(f"  BUILD COMPLETE!  Lingo Hunter AI v{version_str}")
        print(f"  Run: {dest_bin}")
        print("=" * 50)
        print()
        print(_DEPS_NOTICE)

    except Exception as exc:
        print(f"\n[Error during move]: {exc}")


if __name__ == "__main__":
    build()
