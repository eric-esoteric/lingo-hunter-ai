# lh_tray_menu.py — system tray icon & quick-switch context menu
#
# Split out of main_app.py so the tray's menu-construction and icon-theming
# logic is modular, independently testable, and doesn't bloat the main
# window class. Talks to lh_storage_manager directly for the favorites list
# and the current target language; talks to the app itself only through the
# small callback interface passed into TrayMenuController, so it never
# imports main_app.py (no circular import) and never touches Tkinter.
#
# Two features live here:
#   1. Quick-switch — a "Target language" submenu on the tray icon that lets
#      the user change the translation target without opening the main
#      window.
#   2. Favorites pinning — starred languages (see lh_storage_manager's
#      favorite_languages helpers) are always rendered last in that submenu,
#      separated from the rest, so the user's most-used languages sit
#      closest to the tray icon itself (which lives at the bottom of the
#      screen) instead of at the top of a potentially long list — less
#      mouse travel to reach the languages actually used most. Within that
#      block they keep their normal starred order (oldest-starred first),
#      unaffected by the reordering of the section as a whole.
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
    "English", "Spanish", "French", "German", "Italian", "Portuguese",
    "Russian", "Ukrainian", "Polish", "Turkish", "Arabic", "Hindi",
    "Chinese (Simplified)", "Japanese", "Korean", "Vietnamese", "Dutch",
    "Swedish", "Greek", "Hebrew", "Indonesian", "Thai",
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
    """

    def __init__(self, app_name, icon_path, get_config, get_theme,
                 on_select_language, on_open, on_exit, languages=None):
        self.app_name = app_name
        self.icon_path = icon_path
        self._get_config = get_config
        self._get_theme = get_theme
        self._on_select_language = on_select_language
        self._on_open = on_open
        self._on_exit = on_exit
        self.languages = list(languages) if languages else list(DEFAULT_LANGUAGES)
        self._icon = None

    # ── menu construction ───────────────────────────────────────────────

    def _ordered_languages(self, config: dict):
        """Splits the language list into (favorites, others):
          - favorites: the user's starred languages, in their saved order,
            always shown even if they aren't in `self.languages` (e.g. a
            custom, freeform target language the user typed once and
            starred).
          - others: everything else, alphabetical, minus whatever is
            already pinned as a favorite.
        The currently active target language is guaranteed to appear
        somewhere in one of the two lists, even if it's a one-off custom
        value, so its checkmark is always visible.
        """
        favorites = lh_storage_manager.get_favorite_languages(config)
        fav_keys = {f.strip().lower() for f in favorites}

        current = (config.get("target_language") or "").strip()

        pool = list(self.languages)
        if current and current.lower() not in {p.lower() for p in pool}:
            pool.append(current)

        others = sorted(
            (lang for lang in pool if lang.strip().lower() not in fav_keys),
            key=str.lower,
        )
        return favorites, others

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

    def build_menu(self):
        """Builds a fresh pystray.Menu from the current config/theme. Called
        both on first start() and every time rebuild() is invoked (language
        change, favorites change, theme change)."""
        config = self._get_config() or {}
        current = (config.get("target_language") or "").strip()
        favorites, others = self._ordered_languages(config)

        # Others (alphabetical) first, favorites last: a native tray/context
        # menu opens anchored to the tray icon, which sits at the bottom of
        # the screen, so items nearer the *end* of the list are physically
        # closer to the cursor when the menu pops open. Putting the starred
        # languages there means less reaching for the ones actually used
        # most, while the block itself still reads top-to-bottom starting
        # with the first-starred favorite, same as before.
        lang_items = [self._make_language_item(l, current, starred=False) for l in others]
        if favorites and others:
            lang_items.append(Menu.SEPARATOR)
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
