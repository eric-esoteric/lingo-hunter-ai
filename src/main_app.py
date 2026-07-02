# main_app.py — Lingo Hunter AI v1.0
#
# A simplified sibling of Job Hunter AI's main_app.py. Same underlying tech
# stack (CustomTkinter UI, pystray tray icon, pynput/pyperclip hotkey +
# clipboard automation, multi-provider AI failover, multi-theme system) but
# with every job-hunting-specific feature removed: no resume box, no
# filters, no vacancy queue/worker thread, no results window. Settings are
# reduced to exactly the three the user asked to keep: target language,
# hotkey combination, and AI provider/model/key — plus the theme picker,
# which carries over unchanged from Job Hunter AI as a series feature.
#
# Workflow: user types a message anywhere, presses the global hotkey, the
# focused field's text is captured, translated, and pasted back in place.

import os
import sys
import copy
import socket
import threading
import webbrowser

import customtkinter as ctk
from tkinter import messagebox
from PIL import Image

import lh_logging
import lh_version
import lh_storage_manager
import lh_ai_engine
import lh_automation
import lh_notifications
import lh_tray_menu
import lh_autostart

_log = lh_logging.get_logger(__name__)

# Single source of truth for name/version lives in lh_version.py (the build
# scripts read it from there too). Importing it — rather than re-hardcoding a
# literal here — keeps the running app's title bar and the packaged .exe's
# embedded version from silently drifting apart.
APP_NAME = lh_version.APP_NAME
APP_VERSION = lh_version.APP_VERSION

IS_WINDOWS = sys.platform.startswith("win")

# Single-instance signaling port (loopback only). Used so launching the app
# a second time (double-clicking the icon, a desktop shortcut, etc.) while
# it's already running brings the existing window/tray icon to front instead
# of spawning a second instance — the "protection" feature from the
# original app.
_IPC_PORT = 47823

# Command-line flag a Windows-triggered "Start with Windows" launch is
# started with (see lh_autostart.py). When present, the app skips ever
# showing the main window and goes straight to the system tray — the last
# used target language is already whatever's in config.json, so there's
# nothing extra to set for that part.
STARTUP_TRAY_ARG = "--tray"

# ───────────────────────── asset resolution ─────────────────────────────────

def _resolve_asset(name: str):
    candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, name))
    candidates.append(os.path.join(here, "..", name))
    candidates.append(os.path.join(here, "..", "assets", name))
    candidates.append(os.path.join(here, "assets", name))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, name))
        candidates.append(os.path.join(meipass, "assets", name))
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    candidates.append(os.path.join(exe_dir, name))
    candidates.append(os.path.join(exe_dir, "assets", name))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


ICON_PATH = _resolve_asset("icon.ico")
LOGO_PNG_PATH = _resolve_asset("logo.png")

# ───────────────────────── theme system ──────────────────────────────────────
# Restored from Job Hunter AI. Two named themes; the active one persists in
# config.json ("theme" field) and is applied to every registered widget plus
# the toast notification system via lh_notifications.apply_theme().

THEMES = {
    # Theme 1 — "Calm Dark". Matches the original app's actual screenshot
    # (assets/usually.png): a deep muted blue-graphite background, near-
    # black bluish cards/inputs with light bluish-gray body text, a warm
    # terracotta/orange main accent (title, Start button, active checkboxes,
    # success text), and a sand-yellow/mustard secondary accent (the
    # secondary "Open vacancies"-style button). Font: a normal proportional
    # sans-serif, same as the reference screenshot.
    "Calm Dark": {
        "bg": "#1A1F2B",
        "card_bg": "#12161F",
        "input_bg": "#0F1319",
        "secondary_hover": "#1C2230",
        "text": "#C9D1DC",
        "text_muted": "#7C8798",
        "accent": "#D9824F",
        "danger": "#C1503F",
        "gold": "#D9A94E",
        "font_family": "Segoe UI",
    },
    # Theme 2 — "Cyberpunk / Esoteric". Matches the original app's actual
    # screenshot (assets/hotline.png): a near-pure-black background, deep
    # purple/eggplant cards & input fields, vivid magenta/fuchsia as the key
    # attention marker (title, Start button, checkboxes, status bullet),
    # neon cyan as the primary *reading* color for nearly all body/list/
    # label text (not just a muted variant), and bright orange as the
    # secondary button + one icon-button outline. Critically, the reference
    # screenshot renders every single piece of text — title, labels, body
    # copy, button text — in a monospace, terminal-style font, unlike Calm
    # Dark's proportional sans-serif. Consolas is used here as the practical
    # always-available Windows monospace font; Tk will gracefully
    # substitute a system monospace font on platforms where it's absent.
    "Cyberpunk": {
        "bg": "#030305",
        "card_bg": "#1B0F2E",
        "input_bg": "#241239",
        "secondary_hover": "#321A4D",
        "text": "#21E6E6",
        "text_muted": "#5FA8AD",
        "accent": "#F500F0",
        "danger": "#FF4444",
        "gold": "#FF6A13",
        "font_family": "Consolas",
    },
}

# Auto-derive each theme's labeled font tuples (title/section/body) from its
# own font_family, so there's exactly one place (font_family, above) that
# controls which family a theme uses — no more hand-duplicated "Segoe UI"
# literals scattered through the THEMES dict that could drift out of sync.
for _theme_dict in THEMES.values():
    _fam = _theme_dict["font_family"]
    _theme_dict["fonts"] = {
        "title": (_fam, 20, "bold"),
        "section": (_fam, 13, "bold"),
        "body": (_fam, 12),
    }
del _theme_dict, _fam

DEFAULT_THEME_NAME = "Calm Dark"

# Fixed, glyph-safe font for purely-decorative Unicode icon glyphs (the
# settings gear "⚙", toast warning/checkmark icons, etc.) that should NOT
# follow the active theme's font family — Consolas and other narrow
# monospace fonts often lack these symbol glyphs, which would otherwise risk
# silently rendering as a missing-glyph "tofu" box in the Cyberpunk theme.
# Ordinary running text (titles, labels, body copy, button labels) is not
# affected by this and does follow the theme's font_family via _font()
# below.
GLYPH_SAFE_FONT = "Segoe UI"


def _font(theme: dict, size: int, weight: str = None):
    """Builds a (family, size[, weight]) tuple using the active theme's
    font_family. Centralizes what used to be dozens of hardcoded
    ("Segoe UI", N[, "bold"]) literals throughout this file, so every label/
    button actually follows the Cyberpunk theme's monospace font instead of
    silently staying on Segoe UI."""
    family = theme.get("font_family", "Segoe UI")
    return (family, size, weight) if weight else (family, size)

COMMON_LANGUAGES = [
    "English", "Spanish", "French", "German", "Italian", "Portuguese",
    "Russian", "Ukrainian", "Polish", "Turkish", "Arabic", "Hindi",
    "Chinese (Simplified)", "Japanese", "Korean", "Vietnamese", "Dutch",
    "Swedish", "Greek", "Hebrew", "Indonesian", "Thai",
]

# Where to point users for each provider's API key page (or, for local
# providers, the relevant install page). Mirrors the original app's
# show_api_help() pattern, generalized across all supported providers.
PROVIDER_KEY_HELP = {
    "Gemini": ("Google AI Studio", "https://aistudio.google.com/apikey"),
    "OpenAI": ("OpenAI Platform", "https://platform.openai.com/api-keys"),
    "Anthropic": ("Anthropic Console", "https://console.anthropic.com/settings/keys"),
    "DeepSeek": ("DeepSeek Platform", "https://platform.deepseek.com/api_keys"),
    "OpenRouter": ("OpenRouter", "https://openrouter.ai/keys"),
}
PROVIDER_LOCAL_HELP = {
    "Ollama": ("Ollama", "https://ollama.com/download"),
    "LM Studio": ("LM Studio", "https://lmstudio.ai/"),
}


# ───────────────────────── window chrome helpers (Windows) ─────────────────

def force_dark_title_bar(window) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        pass


def _apply_icon_win32(window) -> None:
    if ICON_PATH and os.path.exists(ICON_PATH):
        try:
            window.iconbitmap(ICON_PATH)
        except Exception:
            pass


def center_window(window, width: int, height: int) -> None:
    """Centers `window` on the monitor's *work area* (excludes the taskbar),
    correctly accounting for CustomTkinter's internal DPI scaling.

    Bug this fixes: CTk's .geometry() override scales the width/height
    components of the geometry string by the widget scaling factor, but
    leaves the position (x/y) untouched. The previous implementation
    centered using the *unscaled* width/height against the raw screen size,
    so on any display where Windows scaling != 100% the actual rendered
    window was a different size than what was used to compute x/y —
    producing the reported "everything is off-center" behavior. It also
    ignored the taskbar and multi-monitor work areas entirely.
    """
    window.update_idletasks()
    try:
        scale = window._get_window_scaling()
    except Exception:
        scale = 1.0

    wa_x, wa_y, wa_w, wa_h = lh_notifications.get_work_area(window)
    phys_w = int(width * scale)
    phys_h = int(height * scale)
    x = wa_x + max(0, (wa_w - phys_w) // 2)
    y = wa_y + max(0, (wa_h - phys_h) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def show_api_help(parent, provider_name: str, theme: dict) -> None:
    """Small modal explaining how to get set up with the selected provider,
    with a button linking out to the right website. Generalized version of
    the original app's show_api_help() (which only ever linked to
    aistudio.google.com for Gemini)."""
    win = ctk.CTkToplevel(parent)
    win.title("Setup help")
    win.configure(fg_color=theme["bg"])
    win.geometry("420x240")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()
    _apply_icon_win32(win)
    win.after(50, lambda: force_dark_title_bar(win))
    center_window(win, 420, 240)

    is_local = provider_name in PROVIDER_LOCAL_HELP
    site_name, url = (PROVIDER_LOCAL_HELP if is_local else PROVIDER_KEY_HELP).get(
        provider_name, ("the provider's website", "")
    )

    ctk.CTkLabel(
        win, text=f"{provider_name} setup", font=theme["fonts"]["section"],
        text_color=theme["text"],
    ).pack(anchor="w", padx=20, pady=(20, 8))

    if is_local:
        body = (
            f"{provider_name} runs on your own machine — no API key needed.\n\n"
            f"Install it, make sure it's running, then point the \"Local "
            f"server URL\" field at it (the default is usually correct)."
        )
    else:
        body = (
            f"Create a free API key for {provider_name} on {site_name}, "
            f"then paste it into the \"API key\" field."
        )

    ctk.CTkLabel(
        win, text=body, font=_font(theme, 12), text_color=theme["text_muted"],
        justify="left", wraplength=375,
    ).pack(anchor="w", padx=20, pady=(0, 16), fill="x")

    btn_row = ctk.CTkFrame(win, fg_color="transparent")
    btn_row.pack(fill="x", padx=20, pady=(0, 20), side="bottom")

    ctk.CTkButton(
        btn_row, text="Close", width=100, fg_color=theme["input_bg"],
        hover_color=theme["card_bg"], text_color=theme["text"], command=win.destroy,
    ).pack(side="right")

    if url:
        ctk.CTkButton(
            btn_row, text=f"Open {site_name} ↗", fg_color=theme["accent"],
            hover_color=theme["gold"], text_color="#0B0E14",
            command=lambda: webbrowser.open(url),
        ).pack(side="left")


# ───────────────────────── main app ──────────────────────────────────────────

class LingoHunterApp(ctk.CTk):
    def __init__(self, start_in_tray: bool = False):
        super().__init__()

        self._start_in_tray = start_in_tray
        if start_in_tray:
            # Withdraw immediately, before any UI is built below, so a
            # Windows-triggered "Start with Windows" launch never flashes
            # the main window on screen before it disappears into the tray.
            self.withdraw()

        ctk.set_appearance_mode("dark")

        self.is_active = False
        self._alive = threading.Event()
        self._alive.set()
        self._tray_controller = None
        self._settings_win = None
        self._ipc_socket = None
        # Guards every read/write of self.app_config that can race across the
        # three threads touching it: the Tk main thread (Settings/theme/
        # favorites), the pystray backend thread (tray language quick-switch),
        # and the capture thread (reads the whole config to translate). RLock
        # so a locked mutation that also persists can re-enter via
        # _persist_config() without deadlocking.
        self._config_lock = threading.RLock()

        lh_storage_manager.init_db()
        self.app_config = lh_storage_manager.load_config()

        self.theme_name = self.app_config.get("theme", DEFAULT_THEME_NAME)
        if self.theme_name not in THEMES:
            self.theme_name = DEFAULT_THEME_NAME
        self.theme = THEMES[self.theme_name]
        lh_notifications.apply_theme(self.theme)

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.configure(fg_color=self.theme["bg"])
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        width, height = 460, 400
        center_window(self, width, height)
        self.minsize(width, height)

        _apply_icon_win32(self)
        self.after(50, lambda: force_dark_title_bar(self))

        self.setup_ui()
        self._init_automation()

        # Seamless-start: arm the hotkey automatically on launch — normal
        # window open or silent --tray autostart alike — whenever there's
        # already enough configured (a valid API key / reachable local
        # server) to do so. Removes the "open app, then remember to click
        # Start" step entirely; the user can just press the hotkey. Silent
        # because a missing-key error dialog on every single launch (or, in
        # the tray case, one nobody's looking at) would be worse than just
        # falling back to the ordinary "Paused" state and letting the
        # existing Settings/Start-button flow explain what's missing. Done on a
        # worker thread so the local-server readiness probe never blocks launch.
        self._activate_startup_async()

        if self._start_in_tray:
            # Go straight to "minimized to tray", same end state as clicking
            # the window's close button (on_closing) — last-used target
            # language is already whatever _init_automation/app_config just
            # loaded from config.json, so there's nothing extra to set here.
            self._start_tray_icon()

    # ── UI ───────────────────────────────────────────────────────────────

    def setup_ui(self):
        t = self.theme
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=24, pady=(24, 8))

        if LOGO_PNG_PATH and os.path.exists(LOGO_PNG_PATH):
            try:
                img = Image.open(LOGO_PNG_PATH)
                logo_img = ctk.CTkImage(light_image=img, dark_image=img, size=(40, 40))
                ctk.CTkLabel(self.header_frame, image=logo_img, text="").pack(side="left", padx=(0, 12))
            except Exception:
                pass

        title_box = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)
        self.title_label = ctk.CTkLabel(
            title_box, text="LINGO HUNTER AI", font=t["fonts"]["title"], text_color=t["accent"]
        )
        self.title_label.pack(anchor="w")
        self.subtitle_label = ctk.CTkLabel(
            title_box, text="Instant hotkey translation, anywhere you type",
            font=_font(t, 11), text_color=t["text_muted"]
        )
        self.subtitle_label.pack(anchor="w")

        # Gear icon — deliberately kept on GLYPH_SAFE_FONT (not _font(t, ...))
        # since "⚙" may not exist in Consolas/other monospace fonts and this
        # button never displays any other text.
        self.settings_btn = ctk.CTkButton(
            self.header_frame, text="⚙", width=36, height=36, font=(GLYPH_SAFE_FONT, 16),
            fg_color=t["card_bg"], hover_color=t["input_bg"], text_color=t["text"],
            command=self.open_settings_window,
        )
        self.settings_btn.pack(side="right")

        self.card_frame = ctk.CTkFrame(self, fg_color=t["card_bg"], corner_radius=14)
        self.card_frame.pack(fill="both", expand=True, padx=24, pady=8)
        card = self.card_frame

        self.hotkey_preview_label = ctk.CTkLabel(
            card, text=self._current_hotkey_spec().display(),
            font=_font(t, 22, "bold"), text_color=t["accent"]
        )
        self.hotkey_preview_label.pack(pady=(28, 4))

        self.hint_label = ctk.CTkLabel(
            card, text="Press this in any text field to translate it in place",
            font=_font(t, 11), text_color=t["text_muted"]
        )
        self.hint_label.pack(pady=(0, 18))

        self.target_lang_label = ctk.CTkLabel(
            card, text=f"Target language: {self.app_config.get('target_language', 'English')}",
            font=_font(t, 12), text_color=t["text"]
        )
        self.target_lang_label.pack(pady=(0, 4))

        self.provider_label = ctk.CTkLabel(
            card, text=f"Provider: {self.app_config.get('current_provider', 'Gemini')}",
            font=_font(t, 12), text_color=t["text_muted"]
        )
        self.provider_label.pack(pady=(0, 18))

        self.status_label = ctk.CTkLabel(
            card, text="Paused", font=_font(t, 13, "bold"), text_color=t["text_muted"]
        )
        self.status_label.pack(pady=(0, 14))

        self.toggle_btn = ctk.CTkButton(
            card, text="Start", width=160, height=40, font=_font(t, 14, "bold"),
            fg_color=t["accent"], hover_color=t["gold"], text_color="#0B0E14",
            command=self.toggle_assistant,
        )
        self.toggle_btn.pack(pady=(0, 24))

        self.footer_label = ctk.CTkLabel(
            self, text="Runs in the system tray — closing this window won't stop it.",
            font=_font(t, 10), text_color=t["text_muted"]
        )
        self.footer_label.pack(pady=(0, 12))

    def _current_hotkey_spec(self):
        return lh_automation.HotkeySpec.from_config(self.app_config)

    def _refresh_summary_labels(self):
        self.hotkey_preview_label.configure(text=self._current_hotkey_spec().display())
        self.target_lang_label.configure(
            text=f"Target language: {self.app_config.get('target_language', 'English')}"
        )
        self.provider_label.configure(
            text=f"Provider: {self.app_config.get('current_provider', 'Gemini')}"
        )

    # ── config access (thread-safe) ───────────────────────────────────────

    def _persist_config(self):
        """Serializes app_config to disk under the config lock, so a save
        (which iterates the dict) can never overlap a mutation from another
        thread. Use this everywhere instead of calling
        lh_storage_manager.save_config(self.app_config) directly."""
        with self._config_lock:
            lh_storage_manager.save_config(self.app_config)

    def _config_snapshot(self) -> dict:
        """Returns a deep copy of app_config taken atomically under the lock —
        for consumers on background threads (the capture/translate pipeline)
        that must read a self-consistent view even while the main or tray
        thread is mid-update."""
        with self._config_lock:
            return copy.deepcopy(self.app_config)

    # ── theme ────────────────────────────────────────────────────────────

    def apply_theme(self, theme_name: str, persist: bool = True):
        """Switches the active theme and re-colors every registered widget
        in place (main window + notification toasts). Restores the
        multi-theme system from Job Hunter AI — picking a theme takes effect
        immediately, it isn't gated behind the settings Save button."""
        if theme_name not in THEMES:
            return
        self.theme_name = theme_name
        t = self.theme = THEMES[theme_name]

        self.configure(fg_color=t["bg"])
        self.title_label.configure(text_color=t["accent"], font=t["fonts"]["title"])
        self.subtitle_label.configure(text_color=t["text_muted"], font=_font(t, 11))
        # settings_btn keeps GLYPH_SAFE_FONT — its "⚙" icon is intentionally
        # exempt from the theme's font_family (see setup_ui()).
        self.settings_btn.configure(fg_color=t["card_bg"], hover_color=t["input_bg"], text_color=t["text"])
        self.card_frame.configure(fg_color=t["card_bg"])
        self.hotkey_preview_label.configure(text_color=t["accent"], font=_font(t, 22, "bold"))
        self.hint_label.configure(text_color=t["text_muted"], font=_font(t, 11))
        self.target_lang_label.configure(text_color=t["text"], font=_font(t, 12))
        self.provider_label.configure(text_color=t["text_muted"], font=_font(t, 12))
        self.status_label.configure(font=_font(t, 13, "bold"))
        if getattr(self, "automation_available", True):
            self.toggle_btn.configure(fg_color=t["accent"], hover_color=t["gold"], font=_font(t, 14, "bold"))
        else:
            # Keep the disabled/"Unavailable" look — don't let a theme switch
            # make a broken hotkey engine look clickable again.
            self.toggle_btn.configure(fg_color=t["text_muted"], hover_color=t["text_muted"], font=_font(t, 14, "bold"))
        self.footer_label.configure(text_color=t["text_muted"], font=_font(t, 10))

        # Re-derive the status label's color from current state rather than
        # leaving it in the old theme's accent/danger/muted shade.
        if not getattr(self, "automation_available", True):
            self.update_status("Monitoring disabled — see details below", t["danger"])
        elif self.is_active:
            hk = self._current_hotkey_spec().display()
            self.update_status(f"Listening — press {hk} in any text field", t["accent"])
        else:
            self.update_status("Paused", t["text_muted"])

        # Re-color the dependency-error panel too, if it's currently shown.
        if getattr(self, "diagnostics_frame", None) is not None:
            self.diagnostics_frame.configure(fg_color=t["input_bg"])
            self.diagnostics_title.configure(text_color=t["danger"], font=_font(t, 12, "bold"))
            self.diagnostics_body.configure(text_color=t["text_muted"], font=_font(t, 10))

        lh_notifications.apply_theme(t)

        # Keep the tray icon glyph + menu in lockstep with the newly active
        # theme (see lh_tray_menu.build_tray_icon_image for why the *icon*
        # is the only part of a native tray menu we can actually recolor).
        # No-op if the tray isn't currently running.
        if self._tray_controller is not None:
            self._tray_controller.rebuild()

        if persist:
            self.app_config["theme"] = theme_name
            self._persist_config()

    # ── automation wiring ────────────────────────────────────────────────

    def _init_automation(self):
        # Tracks whether the hotkey/clipboard engine is actually usable on
        # this machine right now. Consulted by the toggle button, the Settings
        # hotkey controls, and toggle_assistant() so the app can't be driven
        # into a state where it *looks* armed but silently does nothing.
        self.automation_available = False
        self.automation = None

        if not lh_automation.AUTOMATION_AVAILABLE:
            self._show_dependency_error(
                "Hotkey capture unavailable — missing dependencies",
                "The 'pynput' and/or 'pyperclip' packages failed to import, so "
                "global hotkeys and clipboard capture are disabled.\n\n"
                "Common causes:\n"
                "• Linux: missing X11 development headers needed at install "
                "time (e.g. 'sudo apt install libx11-dev libxtst-dev "
                "python3-xlib', then reinstall pynput).\n"
                "• Linux (Wayland): pynput/XTest needs an X11 or XWayland "
                "session — a native Wayland session isn't supported.\n"
                "• macOS: pynput installed but not yet granted Accessibility / "
                "Input Monitoring permission in System Settings > Privacy & "
                "Security.\n"
                "• Any OS: pip install failed — check 'pip show pynput "
                "pyperclip'.\n\n"
                f"Fix the underlying issue and restart {APP_NAME}."
            )
            return

        try:
            lh_automation.enforce_linux_subsystem_guard()
            spec = self._current_hotkey_spec()
            self.automation = lh_automation.TranslateCaptureEngine(
                translate_fn=self._translate,
                app_ready_fn=lambda: self.is_active,
                hotkey_spec=spec,
                notify_fn=self._make_notify_fn(),
                capture_success_fn=self._make_success_fn(),
                capture_failure_fn=self._make_failure_fn(),
                busy_fn=self._make_busy_fn(),
            )
            self.automation.start()
            self.automation_available = True
            self._clear_dependency_error()
        except lh_automation.PlatformSecurityException as e:
            self.automation = None
            self._show_dependency_error(
                "Unsupported session (Wayland detected)",
                f"{e}\n\nLog into an X11 (or XWayland-backed) session, or run "
                "under Xorg, then restart the app.",
            )
            messagebox.showerror(APP_NAME, str(e))
        except Exception as e:  # noqa: BLE001
            self.automation = None
            self._show_dependency_error(
                "Hotkey engine failed to start",
                f"{e}\n\nCheck that pynput has permission to monitor input on "
                "this system (Input Monitoring/Accessibility on macOS, an "
                "X11/XWayland session on Linux), and that no other app has "
                "already registered this hotkey combination.",
            )

    def _show_dependency_error(self, title: str, detail: str):
        """Renders a persistent, informative error panel on the main window
        and blocks the Start/Stop control + Settings hotkey editor so the
        user can't drive the app into a state that silently does nothing.
        Idempotent — safe to call again (e.g. on hotkey re-init) to refresh
        the message."""
        self.automation_available = False
        t = self.theme

        self.toggle_btn.configure(state="disabled", text="Unavailable",
                                   fg_color=t["text_muted"], hover_color=t["text_muted"])
        self.update_status("Monitoring disabled — see details below", t["danger"])

        if getattr(self, "diagnostics_frame", None) is None:
            self.diagnostics_frame = ctk.CTkFrame(
                self.card_frame, fg_color=t["input_bg"], corner_radius=10
            )
            self.diagnostics_title = ctk.CTkLabel(
                self.diagnostics_frame, text="", font=_font(t, 12, "bold"),
                text_color=t["danger"], justify="left", anchor="w",
            )
            self.diagnostics_title.pack(anchor="w", padx=14, pady=(12, 4), fill="x")
            self.diagnostics_body = ctk.CTkLabel(
                self.diagnostics_frame, text="", font=_font(t, 10),
                text_color=t["text_muted"], justify="left", anchor="w",
                wraplength=360,
            )
            self.diagnostics_body.pack(anchor="w", padx=14, pady=(0, 12), fill="x")

        self.diagnostics_title.configure(text=f"⚠ {title}")
        self.diagnostics_body.configure(text=detail)
        if not self.diagnostics_frame.winfo_ismapped():
            self.diagnostics_frame.pack(fill="x", padx=18, pady=(0, 18))

    def _clear_dependency_error(self):
        """Hides the diagnostic panel and re-enables controls once automation
        starts successfully (e.g. after set_hotkey/retry paths)."""
        if getattr(self, "diagnostics_frame", None) is not None:
            self.diagnostics_frame.pack_forget()
        t = self.theme
        self.toggle_btn.configure(state="normal", text="Stop" if self.is_active else "Start",
                                   fg_color=t["accent"], hover_color=t["gold"])

    def _translate(self, text: str) -> str:
        # Runs on the background CaptureThread — must not touch Tkinter here.
        # Take a consistent snapshot so a concurrent Settings save or tray
        # language switch can't change the provider/key/target out from under
        # this in-flight translation.
        cfg = self._config_snapshot()
        target = cfg.get("target_language", "English")
        return lh_ai_engine.translate_text(text, target, cfg)

    def _make_notify_fn(self):
        def _notify():
            if self._alive.is_set():
                self.after(0, lambda: self.update_status("Translating…", self.theme["gold"]))
        return _notify

    def _make_success_fn(self):
        def _success(original, translated):
            if not self._alive.is_set():
                return
            def _ui():
                self.update_status("Translated & pasted ✓", self.theme["accent"])
                preview = translated.strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:80] + "…"
                lh_notifications.send_notification(APP_NAME, preview, root=self, is_error=False)
            self.after(0, _ui)
        return _success

    def _make_busy_fn(self):
        def _busy():
            if self._alive.is_set():
                self.after(0, lambda: lh_notifications.send_notification(
                    APP_NAME, "Still translating the previous text — one moment.",
                    root=self, is_error=False,
                ))
        return _busy

    def _make_failure_fn(self):
        def _failure(error_message):
            if not self._alive.is_set():
                return
            def _ui():
                self.update_status("Error — see notification", self.theme["danger"])
                lh_notifications.send_notification(APP_NAME, f"Error: {error_message}", root=self, is_error=True)
            self.after(0, _ui)
        return _failure

    def update_status(self, text, color):
        if not self._alive.is_set():
            return
        try:
            self.status_label.configure(text=text, text_color=color)
        except Exception:
            pass

    # ── start/stop ───────────────────────────────────────────────────────

    def toggle_assistant(self):
        # Defense-in-depth: toggle_btn is disabled while automation_available
        # is False, but guard here too in case it's ever reachable via a
        # keyboard shortcut/programmatic call.
        if not getattr(self, "automation_available", False):
            messagebox.showerror(
                APP_NAME,
                "Hotkey capture isn't available on this system right now — "
                "see the warning panel on the main window for details.",
            )
            return

        if self.is_active:
            self._deactivate()
            return

        self._activate(silent=False)

    def _activation_blocker(self):
        """Checks whether the hotkey engine can be armed right now: automation
        available, AND a usable API key (or a reachable local server, for
        Ollama/LM Studio) for the currently selected provider.

        Returns (ok, error_message). Does NO Tkinter/UI work and reads a config
        snapshot, so it's safe to call from a background thread — which the
        startup path relies on, since the local-server probe (check_local_server)
        must not run on the Tk main thread and freeze launch.
        """
        if not getattr(self, "automation_available", False) or self.automation is None:
            return False, None

        cfg = self._config_snapshot()
        provider = cfg.get("current_provider", "Gemini")
        if lh_storage_manager.is_local_provider(provider):
            base_url = lh_storage_manager.get_local_server_url(cfg, provider)
            ok, msg = lh_ai_engine.check_local_server(provider, base_url)
            if not ok:
                return False, f"{provider} is not reachable: {msg}"
        else:
            api_key = (cfg.get("api_keys") or {}).get(provider, "")
            if not api_key:
                return False, f"No API key set for {provider}. Open Settings to add one."
        return True, None

    def _mark_active(self):
        """Applies the 'armed' state to the UI. Main-thread only."""
        self.is_active = True
        self.toggle_btn.configure(text="Stop")
        hk = self._current_hotkey_spec().display()
        self.update_status(f"Listening — press {hk} in any text field", self.theme["accent"])

    def _activate(self, silent: bool) -> bool:
        """Synchronous arm used by the manual Start button. `silent=False`
        keeps telling the user exactly what's missing; `silent=True` is a quiet
        no-op on failure. For the automatic launch-time arm, use
        _activate_startup_async() instead so the readiness probe never blocks
        the UI thread."""
        ok, err = self._activation_blocker()
        if not ok:
            if not silent and err:
                messagebox.showerror(APP_NAME, err)
            return False
        self._mark_active()
        return True

    def _activate_startup_async(self):
        """Non-blocking automatic activation attempt made right after launch.
        The readiness check can hit the network (local-server probe), which must
        not run on the Tk main thread during startup — so run it on a worker and
        marshal only the resulting UI state change back via after(). Silent by
        design: a missing-key dialog on every launch (or, in --tray autostart,
        one nobody's looking at) would be worse than quietly falling back to
        'Paused' and letting the Start button explain what's missing."""
        def worker():
            try:
                ok, _err = self._activation_blocker()
            except Exception:
                _log.exception("startup activation check failed")
                return
            if ok and self._alive.is_set():
                self.after(0, self._mark_active)
        threading.Thread(target=worker, daemon=True, name="StartupActivate").start()

    def _deactivate(self):
        self.is_active = False
        self.toggle_btn.configure(text="Start")
        self.update_status("Paused", self.theme["text_muted"])

    # ── settings window ──────────────────────────────────────────────────

    def open_settings_window(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        t = self.theme
        win = ctk.CTkToplevel(self)
        self._settings_win = win
        win.title(f"{APP_NAME} — Settings")
        win.configure(fg_color=t["bg"])
        WIN_W, WIN_H = 840, 760
        win.geometry(f"{WIN_W}x{WIN_H}")
        win.minsize(WIN_W, WIN_H)
        win.transient(self)
        win.grab_set()
        _apply_icon_win32(win)
        win.after(50, lambda: force_dark_title_bar(win))
        center_window(win, WIN_W, WIN_H)

        # ── Live theming for this window ────────────────────────────────────
        # Old-version parity fix: previously, picking a new theme from inside
        # Settings only re-colored the *main* window — this window kept its
        # original colors until closed and reopened. Every themed widget
        # below is registered here; the theme picker's command re-applies
        # all of them the instant a new theme is picked, so Settings updates
        # in lockstep with the main window instead of lagging behind it.
        _themed_widgets = []

        def _register(widget, font_spec=None, **theme_map):
            """Registers `widget` for live re-theming. `theme_map` handles
            colors as before (kwarg -> theme dict key, or a literal "#..."
            string passed through unchanged). `font_spec`, new in this
            round, handles fonts the same live way colors already worked:
            pass a role name string ("title"/"section"/"body") to follow
            theme["fonts"][role], or a (size,) / (size, weight) tuple to
            build a font from the theme's current font_family via _font().
            This is what makes the Cyberpunk theme's monospace font actually
            take effect on every registered Settings-window widget, both at
            first open and on a live theme switch — previously this
            mechanism only ever re-applied colors."""
            def _apply(theme_dict):
                kwargs = {}
                for kwarg, key in theme_map.items():
                    kwargs[kwarg] = key if (isinstance(key, str) and key.startswith("#")) else theme_dict[key]
                if font_spec is not None:
                    if isinstance(font_spec, str):
                        kwargs["font"] = theme_dict["fonts"][font_spec]
                    else:
                        kwargs["font"] = _font(theme_dict, *font_spec)
                try:
                    widget.configure(**kwargs)
                except Exception:
                    pass
            _themed_widgets.append(_apply)
            _apply(t)
            return widget

        def _recolor_settings(theme_dict):
            try:
                win.configure(fg_color=theme_dict["bg"])
            except Exception:
                pass
            for apply_fn in _themed_widgets:
                apply_fn(theme_dict)

        # ── Shared spacing scale for this window ────────────────────────────
        # One set of numbers every card/row below builds from, instead of
        # each section inventing its own padding — this (plus every control
        # below stretching with fill="x" instead of a hardcoded pixel width
        # that didn't match the real column width) is what was actually
        # making the old layout look crooked: some rows spanned the full
        # column, others sat at a fixed 320px that didn't line up with it.
        CARD_PAD = 16
        SECTION_GAP = 14
        ROW_GAP = 8

        def _card(parent, last=False):
            """A section container: an elevated panel (card_bg, the same
            tone the main window's card uses against its bg) that every
            control in the section is packed into with fill="x", so each
            row's left AND right edges line up with every other row's."""
            outer = _register(ctk.CTkFrame(parent, corner_radius=10), fg_color="card_bg")
            outer.pack(fill="x", pady=(0, 0 if last else SECTION_GAP))
            inner = ctk.CTkFrame(outer, fg_color="transparent")
            inner.pack(fill="x", padx=CARD_PAD, pady=CARD_PAD)
            return inner

        def _card_header(card, text):
            lbl = _register(ctk.CTkLabel(card, text=text, anchor="w"),
                             font_spec="section", text_color="text")
            lbl.pack(fill="x", pady=(0, ROW_GAP))
            return lbl

        # ── Fixed footer (Save/Cancel) — packed BEFORE the content area so
        # it always reserves its space at the bottom and is never pushed out
        # of view or hidden behind scrolling, regardless of how much content
        # the two columns above end up holding. ─────────────────────────────
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=20, pady=(8, 16))

        # ── Content area: a fixed two-column layout, no scrolling at all. ───
        content = ctk.CTkFrame(win, fg_color="transparent")
        content.pack(side="top", fill="both", expand=True, padx=20, pady=(20, 4))
        content.grid_columnconfigure(0, weight=1, uniform="col")
        content.grid_columnconfigure(1, weight=1, uniform="col")

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # ══════════════════════ LEFT COLUMN ═════════════════════════════════

        # ── Card: target language + favorites ───────────────────────────────
        lang_card = _card(left)
        _card_header(lang_card, "Target language")

        lang_var = ctk.StringVar(value=self.app_config.get("target_language", "English"))
        lang_combo = _register(
            ctk.CTkComboBox(lang_card, values=COMMON_LANGUAGES, variable=lang_var),
            fg_color="input_bg", button_color="accent", dropdown_fg_color="input_bg",
        )
        lang_combo.pack(fill="x", pady=(0, ROW_GAP))

        # ── Favorites ("starring") — grouped directly under the language
        # combo so starred languages sit visually adjacent to the primary
        # list, per the feature spec. A star toggle affects whatever
        # language is currently typed/selected in the combo above; the chip
        # row below it lists everything currently starred and lets the user
        # jump to one (click the chip) or unstar it (click the ×). Favorites
        # persist immediately on toggle — same "no Save button needed"
        # behavior as the theme picker — and also push a live update to the
        # tray's pinned quick-switch list if the tray is running.
        star_btn = _register(
            ctk.CTkButton(
                lang_card, text="☆ Star this language", height=28,
                command=lambda: _toggle_current_favorite(),
            ),
            font_spec=(11,), fg_color="input_bg", hover_color="secondary_hover", text_color="gold",
        )
        star_btn.pack(fill="x", pady=(0, SECTION_GAP))

        favorites_title = _register(
            ctk.CTkLabel(lang_card, text="Favorite languages", anchor="w"),
            font_spec=(11, "bold"), text_color="text_muted",
        )
        favorites_title.pack(fill="x", pady=(0, ROW_GAP))

        # Fixed-height *scrollable* frame rather than a plain, ever-growing
        # one. Bug this fixes: with a plain frame, starring more than ~4
        # languages (3+ rows at 2-per-row) made this block taller than the
        # settings window has room for — since the content area doesn't
        # scroll (see the comment above `content`, below), everything below
        # it (Theme, Hotkey, Startup) got shoved down and partly/fully off
        # the bottom of the fixed-size window. Capping the height here and
        # letting *this one section* scroll internally means the rest of
        # the left column always stays put and visible, no matter how many
        # languages get starred. The fixed height is tuned to exactly fit
        # two rows (4 chips) without a scrollbar — the common case — and
        # only kicks in a scrollbar past that.
        #
        # CTkScrollableFrame's own `height=` argument turned out to not be a
        # reliable hard cap on every CTk build (it grew to fit its content
        # instead of scrolling once a few languages were starred, which is
        # exactly what pushed Theme/Hotkey off the bottom of the window in
        # the screenshot). `favorites_wrap` below is a plain frame with
        # pack_propagate(False), which *is* a hard, version-independent cap:
        # no matter what the scrollable frame inside wants to be, this outer
        # frame refuses to grow past the height given here.
        favorites_wrap = ctk.CTkFrame(lang_card, fg_color="transparent", height=84)
        favorites_wrap.pack(fill="x")
        favorites_wrap.pack_propagate(False)
        favorites_chip_frame = _register(
            ctk.CTkScrollableFrame(favorites_wrap, corner_radius=8),
            fg_color="input_bg",
        )
        favorites_chip_frame.pack(fill="both", expand=True)

        def _refresh_star_button():
            is_fav = lh_storage_manager.is_favorite_language(self.app_config, lang_var.get())
            star_btn.configure(text=("★ Starred — click to unstar" if is_fav else "☆ Star this language"))

        def _notify_tray_favorites_changed():
            if getattr(self, "_tray_controller", None) is not None:
                self._tray_controller.rebuild()

        def _select_language(lang):
            lang_var.set(lang)

        def _rebuild_favorite_chips():
            for child in favorites_chip_frame.winfo_children():
                child.destroy()

            favs = lh_storage_manager.get_favorite_languages(self.app_config)
            if not favs:
                empty_lbl = _register(
                    ctk.CTkLabel(favorites_chip_frame, text="No favorites yet — star a language above.", anchor="w"),
                    font_spec=(10,), text_color="text_muted",
                )
                empty_lbl.pack(fill="x")
                _refresh_star_button()
                return

            per_row = 2
            chip_row = ctk.CTkFrame(favorites_chip_frame, fg_color="transparent")
            chip_row.pack(fill="x")
            for col in range(per_row):
                chip_row.grid_columnconfigure(col, weight=1, uniform="chip")

            for i, lang in enumerate(favs):
                cell = ctk.CTkFrame(chip_row, fg_color="transparent")
                cell.grid_columnconfigure(0, weight=1)
                cell.grid(
                    row=i // per_row, column=i % per_row, sticky="ew",
                    padx=(0, 6) if i % per_row == 0 else (0, 0), pady=(0, 6),
                )

                chip = _register(
                    ctk.CTkButton(
                        cell, text=f"{lh_tray_menu.STAR} {lang}", height=26,
                        command=lambda l=lang: _select_language(l),
                    ),
                    font_spec=(10,), fg_color="input_bg", hover_color="secondary_hover", text_color="text",
                )
                chip.grid(row=0, column=0, sticky="ew")
                remove_btn = _register(
                    ctk.CTkButton(
                        cell, text="×", width=22, height=26,
                        command=lambda l=lang: _remove_favorite(l),
                    ),
                    font_spec=(11, "bold"), fg_color="input_bg", hover_color="danger", text_color="text_muted",
                )
                remove_btn.grid(row=0, column=1, padx=(2, 0))

            _refresh_star_button()

        def _toggle_current_favorite():
            lang = lang_var.get().strip()
            if not lang:
                return
            with self._config_lock:
                lh_storage_manager.toggle_favorite_language(self.app_config, lang)
                self._persist_config()
            _rebuild_favorite_chips()
            _notify_tray_favorites_changed()

        def _remove_favorite(lang):
            with self._config_lock:
                lh_storage_manager.remove_favorite_language(self.app_config, lang)
                self._persist_config()
            _rebuild_favorite_chips()
            _notify_tray_favorites_changed()

        lang_var.trace_add("write", lambda *_: _refresh_star_button())
        _rebuild_favorite_chips()

        # ── Card: appearance + startup ───────────────────────────────────────
        # The autostart checkbox is grouped in the same card as the theme
        # picker — same "flip it, takes effect immediately, no Save button
        # needed" kind of control — rather than living in its own section
        # far down the column below the hotkey editor, where it was easy to
        # miss (and, with enough starred languages, could get pushed off the
        # bottom of the window entirely — see the favorites_chip_frame
        # comment above). Its initial state is read from the registry itself
        # (lh_autostart is the source of truth), not from app_config, so it
        # stays honest if the entry was removed by something other than this
        # app.
        autostart_supported = lh_autostart.is_supported()
        autostart_var = ctk.BooleanVar(
            value=lh_autostart.is_enabled(APP_NAME) if autostart_supported else False
        )

        def _on_autostart_toggle():
            enabled = autostart_var.get()
            if not lh_autostart.set_enabled(APP_NAME, enabled):
                # Revert the checkbox and say so, rather than let the UI
                # silently disagree with what actually happened (e.g. a
                # locked-down machine policy blocking the registry write).
                autostart_var.set(not enabled)
                messagebox.showerror(
                    APP_NAME,
                    "Couldn't update the Windows startup setting. Your system "
                    "may be blocking changes to this app's startup entry.",
                )
                return
            self.app_config["launch_at_startup"] = autostart_var.get()
            self._persist_config()

        appearance_card = _card(left)
        _card_header(appearance_card, "Theme")

        theme_var = ctk.StringVar(value=self.theme_name)
        theme_picker = _register(
            ctk.CTkSegmentedButton(
                appearance_card, values=list(THEMES.keys()), variable=theme_var,
                command=lambda name: (self.apply_theme(name), _recolor_settings(self.theme)),
            ),
            selected_color="accent", selected_hover_color="gold", unselected_color="input_bg",
        )
        theme_picker.pack(fill="x", pady=(0, SECTION_GAP))

        # Moved to its own full-width, left-anchored row instead of sharing
        # a row with "Theme" over on the right. Three different attempts at
        # pinning it to the row's right edge (a fixed width, a measured
        # width, a pack_propagate(False) wrapper) all still left it
        # rendering out of place on at least one real setup — evidently
        # something about how this specific checkbox lays out doesn't behave
        # the same way here as in every environment this was tested against.
        # Rather than keep chasing pixel-perfect right-alignment against a
        # widget that won't cooperate, giving it its own row removes the
        # problem instead of patching around it: left-anchored, it starts at
        # the exact same margin as every other label in this card (Theme,
        # Hotkey combination, AI provider, ...), so there's nothing for it
        # to be "out of line" with, in any theme, on any machine.
        autostart_cb = _register(
            ctk.CTkCheckBox(
                appearance_card, text="Start with Windows", variable=autostart_var,
                command=_on_autostart_toggle,
                state="normal" if autostart_supported else "disabled",
            ),
            font_spec=(11,), fg_color="accent", text_color="text",
        )
        autostart_cb.pack(anchor="w", pady=(0, ROW_GAP))

        autostart_help = _register(
            ctk.CTkLabel(
                appearance_card,
                text=(
                    "“Start with Windows” opens straight to the tray on login, "
                    "last language active."
                    if autostart_supported else
                    "“Start with Windows” isn't available on this platform."
                ),
                justify="left", anchor="w", wraplength=320,
            ),
            font_spec=(10,), text_color="text_muted",
        )
        autostart_help.pack(fill="x")

        # ── Card: hotkey ─────────────────────────────────────────────────────
        hotkey_card = _card(left, last=True)
        _card_header(hotkey_card, "Hotkey combination")

        hk = self._current_hotkey_spec()
        hk_row = ctk.CTkFrame(hotkey_card, fg_color="transparent")
        hk_row.pack(fill="x", pady=(0, ROW_GAP))
        hk_row.grid_columnconfigure(0, weight=1, uniform="hk")
        hk_row.grid_columnconfigure(1, weight=1, uniform="hk")
        hk_row.grid_columnconfigure(2, weight=1, uniform="hk")

        mod1_var = ctk.StringVar(value=hk.mod1.capitalize())
        mod2_var = ctk.StringVar(value=hk.mod2.capitalize())
        key_var = ctk.StringVar(value=hk.key)

        hotkey_disabled = not getattr(self, "automation_available", False)
        mod1_menu = _register(
            ctk.CTkOptionMenu(
                hk_row, values=["Ctrl", "Alt", "Shift", "Win"], variable=mod1_var,
                state="disabled" if hotkey_disabled else "normal",
            ),
            fg_color="input_bg", button_color="accent",
        )
        mod1_menu.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        mod2_menu = _register(
            ctk.CTkOptionMenu(
                hk_row, values=["None", "Ctrl", "Alt", "Shift", "Win"], variable=mod2_var,
                state="disabled" if hotkey_disabled else "normal",
            ),
            fg_color="input_bg", button_color="accent",
        )
        mod2_menu.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
        key_menu = _register(
            ctk.CTkOptionMenu(
                hk_row, values=letters, variable=key_var,
                state="disabled" if hotkey_disabled else "normal",
            ),
            fg_color="input_bg", button_color="accent",
        )
        key_menu.grid(row=0, column=2, sticky="ew")

        hk_preview = _register(ctk.CTkLabel(hotkey_card, text=hk.display(), anchor="w"),
                                font_spec=(14, "bold"), text_color="accent")
        hk_preview.pack(fill="x", pady=(0, ROW_GAP))

        def _refresh_hk_preview(*_):
            try:
                spec = lh_automation.HotkeySpec(mod1_var.get(), mod2_var.get(), key_var.get())
                hk_preview.configure(text=spec.display())
            except Exception:
                pass

        mod1_var.trace_add("write", _refresh_hk_preview)
        mod2_var.trace_add("write", _refresh_hk_preview)
        key_var.trace_add("write", _refresh_hk_preview)

        hk_help_text = (
            "Needs at least one of Ctrl, Alt, or Shift so it doesn't hijack "
            "every press of that key system-wide. Win alone isn't accepted "
            "(some OSes/window managers won't reliably register it)."
            if not hotkey_disabled else
            "Hotkey capture is unavailable on this system — see the warning "
            "panel on the main window for details. This combo is saved but "
            "won't take effect until the underlying issue is fixed."
        )
        hk_help = _register(
            ctk.CTkLabel(hotkey_card, text=hk_help_text, justify="left", anchor="w", wraplength=320),
            font_spec=(10,), text_color="text_muted",
        )
        hk_help.pack(fill="x")

        # ══════════════════════ RIGHT COLUMN ════════════════════════════════

        # ── Card: AI provider + key/URL ──────────────────────────────────────
        provider_card = _card(right)
        _card_header(provider_card, "AI provider")

        provider_var = ctk.StringVar(value=self.app_config.get("current_provider", "Gemini"))
        provider_menu = _register(
            ctk.CTkOptionMenu(provider_card, values=lh_ai_engine.PROVIDER_ORDER, variable=provider_var),
            fg_color="input_bg", button_color="accent",
        )
        provider_menu.pack(fill="x", pady=(0, SECTION_GAP))

        # Trying to reserve "just enough" width for the label so the "?"
        # sits close behind it (first a guessed constant, then a runtime
        # font measurement) kept landing wrong in practice — Consolas/Segoe
        # UI metrics on the user's actual machine didn't match either
        # estimate, so a visible gap kept reappearing. Anchoring the button
        # to the row's *right* edge instead sidesteps the guessing game
        # entirely: the exact same side="right" trick already made
        # autostart_cb immune to the theme-switch jump (see above), and it
        # works here for the same reason — a widget's position no longer
        # depends on a sibling's font-dependent text width at all. The "?"
        # now reads as "help, over on this side of the row" rather than
        # "stuck an odd distance after the label."
        key_row = ctk.CTkFrame(provider_card, fg_color="transparent")
        key_label = _register(ctk.CTkLabel(key_row, text="API key", anchor="w"),
                               font_spec=(12,), text_color="text_muted")
        key_label.pack(side="left")
        # The "?" hint button — the API-key-help link from the original app,
        # generalized across providers (fix: this was previously missing).
        help_btn = _register(
            ctk.CTkButton(
                key_row, text="?", width=22, height=22,
                command=lambda: show_api_help(win, provider_var.get(), self.theme),
            ),
            font_spec=(11, "bold"), fg_color="input_bg", hover_color="secondary_hover", text_color="accent",
        )
        help_btn.pack(side="right")

        key_entry = _register(ctk.CTkEntry(provider_card, show="*"), fg_color="input_bg")

        local_url_row = ctk.CTkFrame(provider_card, fg_color="transparent")
        local_url_label = _register(ctk.CTkLabel(local_url_row, text="Local server URL", anchor="w"),
                                     font_spec=(12,), text_color="text_muted")
        local_url_label.pack(side="left")
        local_help_btn = _register(
            ctk.CTkButton(
                local_url_row, text="?", width=22, height=22,
                command=lambda: show_api_help(win, provider_var.get(), self.theme),
            ),
            font_spec=(11, "bold"), fg_color="input_bg", hover_color="secondary_hover", text_color="accent",
        )
        local_help_btn.pack(side="right")
        local_url_entry = _register(ctk.CTkEntry(provider_card), fg_color="input_bg")

        # ── Card: models ─────────────────────────────────────────────────────
        models_card = _card(right)
        models_title = _register(
            ctk.CTkLabel(models_card, text="Models to use (in failover order)", anchor="w"),
            font_spec="section", text_color="text",
        )
        models_frame = ctk.CTkFrame(models_card, fg_color="transparent")

        model_check_vars = {}

        def _rebuild_models(provider_name):
            for child in models_frame.winfo_children():
                child.destroy()
            model_check_vars.clear()

            is_local = lh_storage_manager.is_local_provider(provider_name)
            if is_local:
                key_row.pack_forget()
                key_entry.pack_forget()
                local_url_row.pack(fill="x", pady=(0, ROW_GAP))
                local_url_entry.pack(fill="x", pady=(0, SECTION_GAP))
                local_url_entry.delete(0, "end")
                local_url_entry.insert(0, lh_storage_manager.get_local_server_url(self.app_config, provider_name))
            else:
                local_url_row.pack_forget()
                local_url_entry.pack_forget()
                key_row.pack(fill="x", pady=(0, ROW_GAP))
                key_entry.pack(fill="x", pady=(0, SECTION_GAP))
                key_entry.delete(0, "end")
                key_entry.insert(0, (self.app_config.get("api_keys") or {}).get(provider_name, ""))

            models_title.pack(fill="x", pady=(0, ROW_GAP))
            models_frame.pack(fill="x")

            active = set((self.app_config.get("active_models") or {}).get(provider_name) or [])
            all_models = lh_ai_engine.ALL_PROVIDERS_MODELS.get(provider_name, [])
            if not active and all_models:
                active = {all_models[0]}
            for model_name in all_models:
                var = ctk.BooleanVar(value=model_name in active)
                model_check_vars[model_name] = var
                cb = _register(
                    ctk.CTkCheckBox(models_frame, text=model_name, variable=var),
                    font_spec=(12,), fg_color="accent", text_color="text",
                )
                cb.pack(anchor="w", pady=2)

        provider_var.trace_add("write", lambda *_: _rebuild_models(provider_var.get()))
        _rebuild_models(provider_var.get())

        # ── Card: translation style ─────────────────────────────────────────
        # Two options: "expressive" (default — profanity/slang translated
        # faithfully, and Gemini's own safety thresholds relaxed just enough
        # to stop false-positive blocks on ordinary crude language) vs.
        # "standard" (defers entirely to the AI provider's own default,
        # more conservative behavior — for users who'd rather have that).
        style_card = _card(right, last=True)
        _card_header(style_card, "Translation style")

        mode_label_by_key = lh_ai_engine.TRANSLATION_MODE_LABELS
        mode_key_by_label = {v: k for k, v in mode_label_by_key.items()}
        current_mode_key = self.app_config.get("translation_mode", lh_ai_engine.DEFAULT_TRANSLATION_MODE)
        mode_var = ctk.StringVar(
            value=mode_label_by_key.get(current_mode_key, mode_label_by_key[lh_ai_engine.DEFAULT_TRANSLATION_MODE])
        )
        mode_menu = _register(
            ctk.CTkOptionMenu(
                style_card,
                values=[mode_label_by_key[m] for m in lh_ai_engine.TRANSLATION_MODES],
                variable=mode_var,
            ),
            fg_color="input_bg", button_color="accent",
        )
        mode_menu.pack(fill="x", pady=(0, ROW_GAP))

        mode_help = _register(
            ctk.CTkLabel(
                style_card,
                text=(
                    "Expressive translates profanity and slang faithfully instead "
                    "of softening it. Standard defers to the AI provider's own, "
                    "more conservative default rendering."
                ),
                justify="left", anchor="w", wraplength=320,
            ),
            font_spec=(10,), text_color="text_muted",
        )
        mode_help.pack(fill="x")

        # ── Save / Cancel — always visible, in the fixed footer ─────────────

        def _save_and_close():
            mod1, mod2, key = mod1_var.get(), mod2_var.get(), key_var.get()
            if not lh_automation.HotkeySpec.has_required_modifier(mod1, mod2):
                messagebox.showerror(
                    APP_NAME,
                    "A hotkey needs at least one modifier key: Ctrl, Alt, or Shift.\n"
                    "Win alone isn't allowed — it can't reliably be blocked from "
                    "reaching every other press of that key system-wide.",
                )
                return

            provider_name = provider_var.get()
            selected_models = [m for m, v in model_check_vars.items() if v.get()]
            if not selected_models:
                messagebox.showerror(APP_NAME, "Select at least one model for the chosen provider.")
                return

            new_spec = lh_automation.HotkeySpec(mod1, mod2, key)
            # Apply the whole settings delta and persist atomically, so a
            # concurrent tray language switch can't interleave with a
            # half-updated config or a save that's iterating it.
            with self._config_lock:
                self.app_config["target_language"] = lang_var.get().strip() or "English"
                self.app_config["current_provider"] = provider_name
                self.app_config.setdefault("active_models", {})[provider_name] = selected_models
                self.app_config["translation_mode"] = mode_key_by_label.get(
                    mode_var.get(), lh_ai_engine.DEFAULT_TRANSLATION_MODE
                )

                if lh_storage_manager.is_local_provider(provider_name):
                    self.app_config.setdefault("local_servers", {})[provider_name] = local_url_entry.get().strip()
                else:
                    self.app_config.setdefault("api_keys", {})[provider_name] = key_entry.get().strip()

                self.app_config["hotkey"] = new_spec.to_dict()
                self.app_config["theme"] = self.theme_name

                self._persist_config()
            self._refresh_summary_labels()

            if self.automation is not None:
                try:
                    self.automation.set_hotkey(new_spec)
                except Exception as e:  # noqa: BLE001
                    messagebox.showwarning(APP_NAME, f"Saved, but could not hot-swap the hotkey: {e}")

            win.destroy()
            self._settings_win = None

        def _cancel():
            win.destroy()
            self._settings_win = None

        cancel_btn = _register(
            ctk.CTkButton(footer, text="Cancel", width=120, height=36, command=_cancel),
            fg_color="input_bg", hover_color="card_bg", text_color="text",
        )
        cancel_btn.pack(side="right", padx=(8, 0))
        save_btn = _register(
            ctk.CTkButton(footer, text="Save", width=120, height=36, text_color="#0B0E14", command=_save_and_close),
            fg_color="accent", hover_color="gold",
        )
        save_btn.pack(side="right")

        win.protocol("WM_DELETE_WINDOW", _cancel)

    # ── system tray ──────────────────────────────────────────────────────

    def on_closing(self):
        self.withdraw()
        self._start_tray_icon()

    def _start_tray_icon(self):
        if self._tray_controller is not None and self._tray_controller.is_running:
            return

        if self._tray_controller is None:
            self._tray_controller = lh_tray_menu.TrayMenuController(
                app_name=APP_NAME,
                icon_path=ICON_PATH or LOGO_PNG_PATH,
                get_config=lambda: self.app_config,
                get_theme=lambda: self.theme,
                on_select_language=self._set_target_language_from_tray,
                on_open=lambda: self.after(0, self._restore_from_tray),
                on_exit=lambda: self.after(0, self._do_exit),
                languages=COMMON_LANGUAGES,
            )

        if not self._tray_controller.start():
            # No tray support installed — minimize behavior is lost; fall
            # back to actually closing rather than vanishing silently.
            self._do_exit()

    def _set_target_language_from_tray(self, language: str):
        """Fired from the tray's "Target language" quick-switch. Runs on the
        tray backend's own thread (not the Tk main thread), so config
        mutation + the (thread-safe) disk save happen here directly, and
        only the actual Tkinter widget update is marshaled onto the main
        thread via self.after()."""
        with self._config_lock:
            self.app_config["target_language"] = language
            lh_storage_manager.save_config(self.app_config)
        self.after(0, self._refresh_summary_labels)

    def _restore_from_tray(self):
        # Guard: this is reached both from the tray menu's "Open" click and
        # from a second app instance signaling over the loopback socket. In
        # either case it must be a no-op other than bringing the window to
        # front — never spawn a second window or re-enter this repeatedly.
        if self._tray_controller is not None:
            self._tray_controller.stop()
        self._bring_to_front()

    def _bring_to_front(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _do_exit(self):
        _log.info("exit requested — shutting down")
        self._alive.clear()
        self.is_active = False
        if self._tray_controller is not None:
            self._tray_controller.stop()
        if getattr(self, "automation", None) is not None:
            try:
                self.automation.stop()
            except Exception:
                _log.exception("automation.stop() failed during exit")
        if self._ipc_socket is not None:
            try:
                self._ipc_socket.close()
            except Exception:
                pass
        # Graceful shutdown: let mainloop() return so Tk tears down and logging
        # handlers flush, instead of the previous immediate os._exit(0) that
        # skipped all of that. A daemon safety-net timer still guarantees the
        # process dies even if a native backend thread (pystray/GTK) wedges —
        # and because it's a daemon it never *delays* a clean exit: if the
        # quit()/destroy() below let the interpreter unwind normally, the
        # process is already gone before the timer could fire.
        forcer = threading.Timer(2.0, lambda: os._exit(0))
        forcer.daemon = True
        forcer.start()
        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # ── single-instance IPC listener ─────────────────────────────────────

    def start_ipc_listener(self, srv_socket: socket.socket):
        """Accepts connections on the already-bound single-instance socket
        (see main()) and brings the window to front whenever a second
        instance signals that it tried to start. This is the "protection"
        the original app had: relaunching while already running restores
        the existing window/tray icon instead of opening a new one."""
        self._ipc_socket = srv_socket

        def _serve():
            srv_socket.settimeout(1.0)
            while self._alive.is_set():
                try:
                    conn, _addr = srv_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._alive.is_set():
                        _log.exception("IPC listener accept() failed")
                    break
                try:
                    conn.recv(64)
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                self.after(0, self._restore_from_tray)
            try:
                srv_socket.close()
            except Exception:
                pass

        threading.Thread(target=_serve, daemon=True, name="IPCListener").start()


def _bind_single_instance_socket():
    """Atomically claims the single-instance lock by binding a fixed
    loopback port. Only one process can ever hold this bind, so it's race-
    free across two instances starting at the same moment (unlike a
    check-then-act lock file)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", _IPC_PORT))
        srv.listen(5)
        return srv
    except OSError:
        try:
            srv.close()
        except Exception:
            pass
        return None


def _signal_running_instance() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", _IPC_PORT), timeout=0.5) as c:
            c.sendall(b"restore")
        return True
    except Exception:
        return False


def main():
    srv = _bind_single_instance_socket()
    if srv is None:
        # Another instance already holds the lock — signal it to restore
        # itself from the tray and exit without opening a new window.
        _signal_running_instance()
        print(f"{APP_NAME} is already running — bringing it to the foreground.")
        return

    start_in_tray = STARTUP_TRAY_ARG in sys.argv[1:]

    app = LingoHunterApp(start_in_tray=start_in_tray)
    app.start_ipc_listener(srv)
    app.mainloop()


if __name__ == "__main__":
    main()
