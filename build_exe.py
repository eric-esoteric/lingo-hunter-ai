"""
build_exe.py — Windows build script for Lingo Hunter AI, via PyInstaller.

Adapted from Job Hunter AI's build_exe.py. Simplified: no self-healing
import-rename step (this codebase's module names — lh_storage_manager,
lh_ai_engine, lh_automation, lh_notifications — don't collide with any
common system package names, so that workaround isn't needed here).

Usage:
    python build_exe.py
"""
import os
import re
import sys
import subprocess
import shutil

APP_NAME = "Lingo Hunter AI"


def read_app_version(script_dir):
    """Reads APP_VERSION from src/lh_version.py without importing the module
    (so we don't drag in GUI deps during the build step)."""
    fallback_str = "1.0.0"
    version_file = os.path.join(script_dir, "src", "lh_version.py")
    version_str = fallback_str
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'APP_VERSION\s*=\s*["\']([0-9]+(?:\.[0-9]+)*)["\']', content)
        if match:
            version_str = match.group(1)
    except Exception as e:
        print(f"[Version]: Could not read lh_version.py ({e}). Falling back to {fallback_str}.")

    parts = []
    for chunk in version_str.split("."):
        chunk = chunk.strip()
        parts.append(int(chunk) if chunk.isdigit() else 0)
    while len(parts) < 4:
        parts.append(0)
    return version_str, tuple(parts[:4])


def generate_version_file(script_dir):
    """Generates a Windows VERSIONINFO file for PyInstaller's --version-file,
    so the built .exe shows real version metadata in Properties -> Details."""
    version_str, v = read_app_version(script_dir)
    out_path = os.path.join(script_dir, "version_info.txt")

    content = f"""# UTF-8
# Auto-generated version file. Do not edit by hand —
# source of truth: src/lh_version.py (APP_VERSION constant).
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v[0]}, {v[1]}, {v[2]}, {v[3]}),
    prodvers=({v[0]}, {v[1]}, {v[2]}, {v[3]}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
__STRING_TABLE__
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    string_table = (
        "        StringTable(\n"
        "          u'040904b0',\n"
        "          [\n"
        f"            StringStruct(u'CompanyName', u'{APP_NAME}'),\n"
        f"            StringStruct(u'FileDescription', u'{APP_NAME} - instant hotkey translation'),\n"
        f"            StringStruct(u'FileVersion', u'{version_str}'),\n"
        "            StringStruct(u'InternalName', u'LingoHunterAI'),\n"
        f"            StringStruct(u'LegalCopyright', u'(c) {APP_NAME}'),\n"
        f"            StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),\n"
        f"            StringStruct(u'ProductName', u'{APP_NAME}'),\n"
        f"            StringStruct(u'ProductVersion', u'{version_str}')\n"
        "          ]\n"
        "        )\n"
    )
    content = content.replace("__STRING_TABLE__", string_table.rstrip("\n"))

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Version]: Generated version_info.txt with version {version_str} -> {v}")
        return out_path
    except Exception as e:
        print(f"[Version]: Could not write version_info.txt: {e}")
        return None


def try_run_inno_setup(script_dir):
    """Attempts to run the Inno Setup compiler (ISCC.exe) to build an
    installer, if it's available on this machine."""
    iscc_path = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if not iscc_path:
        for candidate in [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
            r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
            r"C:\Program Files\Inno Setup 5\ISCC.exe",
        ]:
            if os.path.exists(candidate):
                iscc_path = candidate
                break

    if not iscc_path:
        print("\n[Inno Setup]: ISCC.exe not found in PATH or standard install folders.")
        print("              Install Inno Setup (https://jrsoftware.org/isinfo.php) or")
        print("              add ISCC.exe to PATH to have the installer build automatically.")
        return

    iss_file = os.path.join(script_dir, "installer.iss")
    if not os.path.exists(iss_file):
        print(f"\n[Inno Setup]: installer.iss not found in {script_dir}. Skipping.")
        return

    print(f"\n[Inno Setup]: Compiler found: {iscc_path}")
    print(f"[Inno Setup]: Compiling installer from {iss_file}...")
    result = subprocess.run([iscc_path, iss_file], capture_output=True, text=True, cwd=script_dir)

    if result.returncode == 0:
        setup_exe = os.path.join(script_dir, "LingoHunterAI_Setup.exe")
        print("[Inno Setup]: Installer built successfully!")
        if os.path.exists(setup_exe):
            print(f"              File: {setup_exe}")
    else:
        print("[Inno Setup]: Installer compilation FAILED!")
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)


def install_and_compile():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"[0/4] Build working directory: {script_dir}")

    print("\n[1/4] Installing/upgrading build tools...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--upgrade",
        "pyinstaller", "customtkinter", "pillow", "plyer",
        "pynput", "pystray", "pyperclip",
    ], check=True)

    print("\n[2/4] Locating CustomTkinter assets...")
    try:
        import customtkinter
        ctk_path = os.path.dirname(customtkinter.__file__)
        ctk_data_arg = f"{ctk_path}{os.path.pathsep}customtkinter"
    except ImportError:
        print("[Error]: Could not import customtkinter for the build.")
        return

    main_script = os.path.join("src", "main_app.py")
    icon_file = "icon.ico"
    logo_file = os.path.join("assets", "logo.png")

    if not os.path.exists(main_script):
        print(f"[Error]: Entry point {main_script} not found in {script_dir}!")
        return

    print("\n[3/4] Running PyInstaller...")
    version_file_path = generate_version_file(script_dir)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        f"--add-data={ctk_data_arg}",
        "--paths=src",
    ]

    if os.path.exists(icon_file):
        cmd.append(f"--icon={icon_file}")
        cmd.append(f"--add-data={icon_file}{os.path.pathsep}.")
    else:
        print("[Warning]: icon.ico not found in project root. Build will use the default icon.")

    if os.path.exists(logo_file):
        cmd.append(f"--add-data={logo_file}{os.path.pathsep}.")
    else:
        print("[Warning]: assets/logo.png not found.")

    if version_file_path and os.path.exists(version_file_path):
        cmd.append(f"--version-file={version_file_path}")

    cmd += [
        "--hidden-import=pynput.keyboard",
        "--hidden-import=pynput.mouse",
        "--hidden-import=pynput._util.win32",
        "--hidden-import=pystray",
        "--hidden-import=pystray._win32",
        "--hidden-import=pyperclip",
        "--collect-submodules=pynput",
    ]

    cmd.append(main_script)

    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("\n" + "=" * 50)
        print("PYINSTALLER BUILD FAILED!")
        print("=" * 50)
        print("STDOUT:")
        print(result.stdout)
        print("\nSTDERR:")
        print(result.stderr)
        print("=" * 50)
        return

    dist_app_dir = os.path.join(script_dir, "dist", "main_app")
    src_exe = os.path.join(dist_app_dir, "main_app.exe")
    src_internal = os.path.join(dist_app_dir, "_internal")

    parent_dir = os.path.dirname(script_dir)
    target_dir = os.path.join(parent_dir, APP_NAME)
    if not os.path.exists(target_dir):
        target_dir = script_dir

    print(f"\n[4/4] Moving build output to: {target_dir}...")
    try:
        dest_exe = os.path.join(target_dir, f"{APP_NAME}.exe")
        if os.path.exists(dest_exe):
            os.remove(dest_exe)
        shutil.move(src_exe, dest_exe)
        print(f"-> {APP_NAME}.exe moved and renamed.")

        dest_internal = os.path.join(target_dir, "_internal")
        if os.path.exists(dest_internal):
            shutil.rmtree(dest_internal)
        shutil.move(src_internal, dest_internal)
        print("-> _internal/ dependencies folder moved.")

        print("\n[Cleanup]: Removing temporary build folders...")
        shutil.rmtree(os.path.join(script_dir, "build"), ignore_errors=True)
        shutil.rmtree(os.path.join(script_dir, "dist"), ignore_errors=True)
        spec_file = os.path.join(script_dir, "main_app.spec")
        if os.path.exists(spec_file):
            os.remove(spec_file)
        vinfo = os.path.join(script_dir, "version_info.txt")
        if os.path.exists(vinfo):
            os.remove(vinfo)

        print("\n" + "=" * 50)
        print(" BUILD COMPLETE!")
        print(f" Output folder: {target_dir}")
        print("=" * 50)

        try_run_inno_setup(script_dir)

    except Exception as err:
        print(f"\n[Error during move/cleanup]: {err}")


if __name__ == "__main__":
    install_and_compile()
