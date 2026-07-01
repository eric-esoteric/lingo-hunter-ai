# lh_autostart.py — "Start with Windows" autostart management.
#
# Uses the per-user HKCU\Software\Microsoft\Windows\CurrentVersion\Run key
# rather than HKLM. That means enabling/disabling never needs admin
# elevation and only affects the current Windows user — consistent with how
# the app itself is a per-user install (installer.iss uses {autopf}).
#
# The registry is treated as the single source of truth: is_enabled() always
# reads it back rather than trusting whatever was last saved in config.json.
# That way, if a user manually removes the Run entry (Task Manager's Startup
# tab, msconfig, a cleanup tool, a fresh Windows profile, etc.), the Settings
# checkbox reflects reality the next time it's opened instead of silently
# disagreeing with it.
#
# The registered launch command always appends the --tray flag (see
# main_app.py's _STARTUP_TRAY_ARG) so a Windows-triggered launch skips the
# main window entirely and goes straight to the system tray, with whatever
# target language was last used already active (it's just what's in
# config.json — nothing extra to wire up there).

import os
import sys

IS_WINDOWS = sys.platform.startswith("win")

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_ARG = "--tray"

if IS_WINDOWS:
    try:
        import winreg
    except ImportError:  # pragma: no cover - stdlib on Windows; absent elsewhere
        winreg = None
        IS_WINDOWS = False
else:  # pragma: no cover - exercised on non-Windows CI/dev only
    winreg = None


def is_supported() -> bool:
    """False on any non-Windows platform (or if winreg is somehow missing) —
    callers should hide/disable the checkbox entirely in that case rather
    than let it silently no-op."""
    return IS_WINDOWS and winreg is not None


def _launch_command() -> str:
    """Builds the command line stored in the Run key.

    Frozen (PyInstaller) build: sys.executable *is* "Lingo Hunter AI.exe",
    so it's launched directly with --tray.

    Unfrozen (running from source): re-invoke the same Python interpreter
    against this app's entry script, so "Start with Windows" also works out
    of a dev checkout, not just the packaged installer.
    """
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" {_STARTUP_ARG}'
    script = os.path.abspath(sys.argv[0])
    return f'"{sys.executable}" "{script}" {_STARTUP_ARG}'


def is_enabled(value_name: str) -> bool:
    """Reads back whether `value_name` currently has a Run-key entry at all
    (any value) — the existence of the entry is what matters, not its exact
    command line, since a user/other tool could have edited it."""
    if not is_supported():
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(value_name: str, enabled: bool) -> bool:
    """Adds or removes the Run-key entry for `value_name`.

    Returns True if the registry write succeeded, False otherwise (e.g. a
    locked-down machine policy blocking HKCU\\...\\Run writes) — callers
    should surface a failure to the user rather than assume the checkbox
    state took effect.
    """
    if not is_supported():
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
