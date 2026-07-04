# lh_tray_menu.py — system tray icon & quick-switch context menu
#
# Split out of main_app.py so the tray's menu-construction and icon-theming
# logic is modular, independently testable, and doesn't bloat the main
# window class. Talks to lh_storage_manager directly for the favorites list
# and the current target language; talks to the app itself only through the
# small callback interface passed into TrayMenuController, so it never
# imports main_app.py (no circular import) and never touches Tkinter.
#
# The quick-switch feature lives here: a "Target language (…)" item on the
# tray icon that lets the user change the translation target without opening
# the main window. Since v3 it is a single native menu item that opens the
# custom, theme-styled scrollable picker from lh_language_selector.py —
# favorites pinned at its top, the full ~115-language catalog below, per-row
# star toggles. The old *native* submenu of favorites is gone on purpose:
# a native OS-styled submenu sitting next to the custom-styled picker looked
# like two different apps (and the full list used to climb off the top of
# the screen as a native menu). The native submenu remains only as a
# fallback for callers that don't wire up on_open_selector.
#
# A note on "theming" a native tray menu: pystray delegates the actual menu
# rendering to the OS (Windows' native popup menu, macOS' NSMenu, or
# GTK/AppIndicator on Linux). None of those backends expose a way to paint
# custom background/text colors into the menu itself — that's the OS's
# Dark/Light setting, not something an app can override per-item. What IS
# under our control is the tray *icon* image, so build_tray_icon_image()
# regenerates that glyph from the app's current theme dict (its "accent" and
# "bg" colors) whenever the theme changes, keeping at least the icon in
# lockstep with the in-app Calm Dark / Cyberpunk theme rather than looking
# like a leftover from a different theme.

import threading

from PIL import Image, ImageDraw

import lh_storage_manager

try:
    import pystray
    from pystray import Menu, MenuItem
    TRAY_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional system package
    pystray = None
    Menu = None
    MenuItem = None
    TRAY_AVAILABLE = False

# Duplicated (not imported) from main_app.COMMON_LANGUAGES on purpose: this
# keeps lh_tray_menu.py free of any import-time dependency on main_app.py,
# so it stays usable/testable standalone. The caller (main_app.py) always
# passes its own COMMON_LANGUAGES in via TrayMenuController(languages=...),
# so this list is only ever the fallback.
DEFAULT_LANGUAGES = [
    "Afrikaans", "Albanian", "Amharic", "Arabic", "Armenian", "Assamese",
    "Azerbaijani", "Bashkir", "Basque", "Belarusian", "Bengali", "Bosnian",
    "Bulgarian", "Burmese", "Catalan", "Cebuano", "Chechen",
    "Chinese (Simplified)", "Chinese (Traditional)", "Chuvash", "Croatian",
    "Czech", "Danish", "Dari", "Dutch", "English", "Esperanto", "Estonian",
    "Filipino (Tagalog)", "Finnish", "French", "Galician", "Georgian",
    "German", "Greek", "Gujarati", "Haitian Creole", "Hausa", "Hebrew",
    "Hindi", "Hmong", "Hungarian", "Icelandic", "Igbo", "Indonesian",
    "Irish", "Italian", "Japanese", "Javanese", "Kannada", "Kazakh",
    "Khmer", "Kinyarwanda", "Korean", "Kurdish (Kurmanji)",
    "Kurdish (Sorani)", "Kyrgyz", "Lao", "Latin", "Latvian", "Lithuanian",
    "Luxembourgish", "Macedonian", "Malagasy", "Malay", "Malayalam",
    "Maltese", "Maori", "Marathi", "Mongolian", "Nepali", "Norwegian",
    "Odia", "Oromo", "Ossetian", "Pashto", "Persian (Farsi)", "Polish",
    "Portuguese", "Portuguese (Brazilian)", "Punjabi", "Quechua",
    "Romanian", "Russian", "Scottish Gaelic", "Serbian", "Shona", "Sindhi",
    "Sinhala", "Slovak", "Slovenian", "Somali", "Spanish", "Sundanese",
    "Swahili", "Swedish", "Tajik", "Tamil", "Tatar", "Telugu", "Thai",
    "Tibetan", "Tigrinya", "Turkish", "Turkmen", "Ukrainian", "Urdu",
    "Uyghur", "Uzbek", "Vietnamese", "Welsh", "Xhosa", "Yiddish", "Yoruba",
    "Zulu",
]

STAR = "★"  # prefix marking a pinned/favorite language in the tray menu


def build_tray_icon_image(theme: dict, icon_path: str = None, size: int = 64):
    """Returns the PIL image used for the tray icon glyph.

    If a real branded icon asset exists on disk it's used as-is (it already
    carries the app's dark look and is what most users expect the tray icon
    to be). Otherwise a small themed monogram is generated on the fly from
    the *current* theme's "bg" and "accent" colors, so even the fallback
    icon visually matches whichever theme (Calm Dark / Cyberpunk / future
    additions) is active — this is what gets regenerated on every theme
    change via TrayMenuController.rebuild().
    """
    if icon_path:
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception:
            pass

    theme = theme or {}
    bg = theme.get("bg", "#1A1F2B")
    accent = theme.get("accent", "#D9824F")

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=bg)
    pad = size // 5
    draw.ellipse((pad, pad, size - pad, size - pad), fill=accent)
    return img


class TrayMenuController:
    """Owns the pystray.Icon instance and (re)builds its menu/icon on
    demand.

    Callback interface — all are plain zero-argument callables:
      get_config()          -> dict   the live app_config (read, not owned)
      get_theme()            -> dict   the currently active theme dict
      on_select_language(lang: str)    fired when a tray language item is
                                        picked; the caller is responsible for
                                        persisting it and updating any open
                                        UI (this class never touches config
                                        itself beyond reading it back via
                                        get_config()).
      on_open()                        fired when "Open" is clicked
      on_exit()                        fired when "Exit" is clicked
      on_open_selector()               fired when "Select language…" is
                                        clicked; the caller must marshal to
                                        the Tk main thread (e.g. via
                                        app.after) and open the scrollable
                                        picker from lh_language_selector.py.
                                        Optional — if not provided, the item
                                        simply isn't shown.
    """

    def __init__(self, app_name, icon_path, get_config, get_theme,
                 on_select_language, on_open, on_exit, languages=None,
                 on_open_selector=None):
        self.app_name = app_name
        self.icon_path = icon_path
        self._get_config = get_config
        self._get_theme = get_theme
        self._on_select_language = on_select_language
        self._on_open = on_open
        self._on_exit = on_exit
        self._on_open_selector = on_open_selector
        self.languages = list(languages) if languages else list(DEFAULT_LANGUAGES)
        self._icon = None

    # ── menu construction ───────────────────────────────────────────────

    def _ordered_languages(self, config: dict):
        """Returns (favorites, extras) for the tray submenu.

        favorites: the user's starred languages, in their saved order,
          always shown even if they aren't in `self.languages` (e.g. a
          custom, freeform target language the user typed once and starred).
        extras: at most one entry — the currently active target language
          when it is NOT starred, so its radio checkmark always has
          somewhere to appear. The rest of the ~115-language catalog is
          deliberately NOT menu-listed anymore; it lives behind the
          "Select language…" picker (lh_language_selector.py) instead of
          scrolling off the top of the screen as a native menu.
        """
        favorites = lh_storage_manager.get_favorite_languages(config)
        fav_keys = {f.strip().lower() for f in favorites}

        current = (config.get("target_language") or "").strip()
        extras = [current] if current and current.lower() not in fav_keys else []
        return favorites, extras

    def _make_language_item(self, language: str, current: str, starred: bool):
        label = f"{STAR} {language}" if starred else language
        current_key = current.strip().lower()

        def _checked(item, _lang=language):
            return _lang.strip().lower() == current_key

        return MenuItem(
            label,
            self._make_select_handler(language),
            checked=_checked,
            radio=True,
        )

    def _make_select_handler(self, language: str):
        def _handler():
            if self._on_select_language:
                self._on_select_language(language)
            self.rebuild()
        return _handler

    def _make_selector_handler(self):
        """Fires the "Select language…" callback. Runs on the pystray
        backend thread — the callback itself is responsible for hopping to
        the Tk main thread before touching any widgets."""
        def _handler():
            if self._on_open_selector:
                self._on_open_selector()
        return _handler

    def build_menu(self):
        """Builds a fresh pystray.Menu from the current config/theme. Called
        both on first start() and every time rebuild() is invoked (language
        change, favorites change, theme change)."""
        config = self._get_config() or {}
        current = (config.get("target_language") or "").strip()

        if self._on_open_selector:
            # One native item, showing the current target for at-a-glance
            # feedback; clicking it opens the custom themed picker
            # (favorites pinned at its top) right at the cursor. No native
            # favorites submenu anymore — see module docstring.
            label = f"Target language ({current})" if current else "Target language…"
            language_menu = MenuItem(label, self._make_selector_handler())
        else:
            # Fallback for callers without a selector: the old native
            # submenu — favorites last (closest to the tray icon at the
            # bottom of the screen), plus the unstarred current language.
            favorites, extras = self._ordered_languages(config)
            lang_items = [self._make_language_item(l, current, starred=False) for l in extras]
            lang_items.extend(self._make_language_item(l, current, starred=True) for l in favorites)
            language_menu = MenuItem("Target language", Menu(*lang_items))

        return Menu(
            MenuItem("Open", self._on_open, default=True),
            language_menu,
            Menu.SEPARATOR,
            MenuItem("Exit", self._on_exit),
        )

    # ── icon lifecycle ──────────────────────────────────────────────────

    def start(self) -> bool:
        """Starts the tray icon on a background thread. Returns False (and
        does nothing else) if pystray isn't importable on this system —
        callers should fall back to a plain exit in that case, same as
        before this module existed."""
        if not TRAY_AVAILABLE:
            return False
        if self._icon is not None:
            return True

        image = build_tray_icon_image(self._get_theme(), self.icon_path)
        self._icon = pystray.Icon(self.app_name, image, self.app_name, self.build_menu())
        threading.Thread(target=self._icon.run, daemon=True, name="TrayIcon").start()
        return True

    def rebuild(self):
        """Refreshes both the menu (language list/checkmarks/favorites) and
        the icon image (theme colors) on the already-running tray icon.
        Safe to call whether or not the tray is currently running."""
        if self._icon is None:
            return
        try:
            self._icon.menu = self.build_menu()
            self._icon.icon = build_tray_icon_image(self._get_theme(), self.icon_path)
            self._icon.update_menu()
        except Exception:
            pass

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    @property
    def is_running(self) -> bool:
        return self._icon is not None
