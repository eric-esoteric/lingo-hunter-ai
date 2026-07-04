# lh_language_selector.py — compact scrollable language-picker popup
#
# Why this exists: with ~115 target languages, neither a native tray submenu
# nor a CTkComboBox dropdown can present the list sanely — both render as one
# enormous OS menu column that runs off the top of the screen. This module
# provides the replacement UI both entry points share:
#
#   * the tray menu's "Target language…" item (see lh_tray_menu.py), and
#   * the Settings window's target-language field (see main_app.py),
#
# open the same borderless, always-on-top, theme-styled popup:
#
#   ★ favorites, pinned at the top in their saved (starring) order
#   ──────────── separator
#   every other language, alphabetical
#
# At most ~20 rows are visible at a time (capped further on short screens);
# the mouse wheel scrolls several rows per notch; pressing a letter jumps to
# it. Clicking a row selects that language and closes the popup. The ☆/★ at
# a row's right edge toggles it in/out of favorites in place (the list
# re-sections immediately) without closing. Since v3 the *native* tray
# submenu of favorites is gone entirely — this popup IS the tray quick-
# switch, so favorites live here instead.
#
# Rendering: one tk.Canvas, not per-row widgets. The row list was previously
# ~115 CTk frames+labels, which (a) took ~a second to construct every open
# and (b) needed chunked lazy building to even appear responsive. Drawing
# the same rows as canvas text/rect items is near-instant, gives exact
# control over wheel-scroll speed, and still matches the app's theme because
# every color/font comes from the active THEMES dict. DPI matters here: a
# plain canvas does NOT inherit CustomTkinter's scaling, so every dimension
# and font size below is multiplied by the live scaling factor explicitly —
# the first plain-tk version of this file skipped that and rendered at half
# size on a 2.8K laptop at 200% Windows scaling.
#
# Threading contract: everything here must run on the Tk main thread. The
# tray backend (pystray) therefore marshals its "Target language…" click via
# app.after(0, ...) before calling open_language_selector() — same pattern
# as the tray's "Open"/"Exit" items.

import sys
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk

# All sizes are in LOGICAL (unscaled) pixels — multiplied by the live CTk
# scaling factor before use.
POPUP_WIDTH = 340        # total popup width
ROW_HEIGHT = 34          # per language row
SEP_HEIGHT = 17          # separator row between favorites and the rest
MAX_VISIBLE_ROWS = 20    # hard cap requested by spec; screen may cap further
MIN_VISIBLE_ROWS = 8     # never shrink below this to hug an anchor
PAD = 6                  # inner padding around the list
FONT_PX = 17             # row font height in pixels (≈13pt at 96 DPI)
STAR_ZONE = 44           # right-edge strip (px) where a click means "star"
WHEEL_ROWS = 4           # rows scrolled per wheel notch — deliberately
                         # aggressive; CTkScrollableFrame's default felt
                         # half as fast as it should on a 115-item list

STAR_ON = "★"
STAR_OFF = "☆"
CHECK = "✓"

# Star/check glyphs can be missing from narrow monospace fonts (Consolas in
# the Cyberpunk theme) — same reasoning as main_app's GLYPH_SAFE_FONT,
# duplicated here to avoid an import-time dependency on main_app.
GLYPH_SAFE_FONT = "Segoe UI"

# Module-level handle so a second "open" click closes/replaces the existing
# popup instead of stacking a duplicate on top of it.
_open_popup = None


def _get_scaling(widget) -> float:
    """The live DPI/user scaling factor CTk is rendering at (1.0 = 100%)."""
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:
        pass
    try:
        return float(widget._get_window_scaling())
    except Exception:
        return 1.0


def _cursor_pos(master):
    """Current mouse position in physical screen coordinates.

    On Windows this deliberately uses Win32 GetCursorPos instead of Tk's
    winfo_pointerx/y: when the popup is opened from the tray the main window
    is typically *withdrawn*, and querying the pointer through an unmapped
    Tk window can return bogus coordinates (e.g. -1/-1) — which is exactly
    what made the first version of this popup open in the far corner of the
    screen instead of next to the tray menu the user just clicked."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            import ctypes.wintypes
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return int(pt.x), int(pt.y)
        except Exception:
            pass
    try:
        x, y = master.winfo_pointerx(), master.winfo_pointery()
        if x > -1 and y > -1:
            return x, y
    except Exception:
        pass
    return None


def _work_area(window):
    """(x, y, w, h) of the usable monitor region in physical pixels —
    excludes the taskbar on Windows so the popup never hides behind it."""
    try:
        import lh_notifications
        return lh_notifications.get_work_area(window)
    except Exception:
        try:
            return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
        except Exception:
            return 0, 0, 1920, 1080


def open_language_selector(master, theme, languages, current, favorites,
                           on_select, on_toggle_favorite, anchor=None,
                           prefer_above=False):
    """Opens (or re-opens) the language selector popup.

    master             Tk widget to parent the Toplevel to (app root or the
                       Settings window).
    theme              active theme dict (THEMES[...] from main_app).
    languages          full list of language names to offer.
    current            currently selected target language ("" if none).
    favorites          starred languages, in saved order — rendered as the
                       pinned top section.
    on_select(lang)    fired when a language row is clicked; the popup
                       closes itself right after.
    on_toggle_favorite(lang) -> bool, toggles favorite state and returns the
                       new state; the popup re-sections its list in place,
                       staying open.
    anchor             (x, y) PHYSICAL screen coords to open at; defaults to
                       the current mouse pointer position.
    prefer_above       True when opening from the tray (bottom of screen):
                       the popup extends upward from the anchor.
    """
    global _open_popup
    if _open_popup is not None:
        try:
            _open_popup.close()
        except Exception:
            pass
        _open_popup = None

    _open_popup = _LanguageSelectorPopup(
        master, theme or {}, list(languages or []), (current or "").strip(),
        list(favorites or []), on_select, on_toggle_favorite, anchor,
        prefer_above,
    )
    return _open_popup


def build_entries(languages, favorites):
    """Sectioned row model: favorites first (saved order), a separator, then
    every non-starred language alphabetically. Pure function, unit-tested
    separately from the canvas rendering."""
    favs = [f for f in favorites if f and f.strip()]
    fav_keys = {f.strip().lower() for f in favs}
    main = [l for l in languages if l.strip().lower() not in fav_keys]
    entries = [("fav", f) for f in favs]
    if favs and main:
        entries.append(("sep", None))
    entries.extend(("lang", l) for l in main)
    return entries


class _LanguageSelectorPopup:
    def __init__(self, master, theme, languages, current, favorites,
                 on_select, on_toggle_favorite, anchor, prefer_above):
        self._on_select = on_select
        self._on_toggle_favorite = on_toggle_favorite
        self._closed = False

        # Guarantee the current (possibly custom/freeform) language is
        # offered, mirroring the old tray-menu behavior.
        pool = list(languages)
        known = {l.lower() for l in pool} | {f.lower() for f in favorites}
        if current and current.lower() not in known:
            pool.append(current)
        pool.sort(key=str.lower)
        self._languages = pool
        self._favorites = list(favorites)
        self._current_key = current.lower()

        # ── theme ────────────────────────────────────────────────────────
        self._card_bg = theme.get("card_bg", "#12161F")
        self._row_hover = theme.get("secondary_hover", "#1C2230")
        self._row_current = theme.get("input_bg", "#0F1319")
        self._fg = theme.get("text", "#C9D1DC")
        self._fg_muted = theme.get("text_muted", "#7C8798")
        self._accent = theme.get("accent", "#D9824F")
        self._gold = theme.get("gold", "#D9A94E")
        family = theme.get("font_family", "Segoe UI")

        # ── scaled metrics (a plain canvas doesn't auto-scale like CTk) ──
        s = self._scale = _get_scaling(master)
        self._row_h = max(1, round(ROW_HEIGHT * s))
        self._sep_h = max(1, round(SEP_HEIGHT * s))
        self._pad = round(PAD * s)
        self._star_zone = round(STAR_ZONE * s)
        # Negative tk font size = height in PIXELS (points would be scaled
        # by the OS DPI a second time on top of our explicit scaling).
        px = -max(8, round(FONT_PX * s))
        self._font = tkfont.Font(family=family, size=px)
        self._font_bold = tkfont.Font(family=family, size=px, weight="bold")
        self._star_font = tkfont.Font(family=GLYPH_SAFE_FONT, size=px)

        # ── geometry (physical pixels) ───────────────────────────────────
        wa_x, wa_y, wa_w, wa_h = _work_area(master)

        if anchor is None:
            anchor = _cursor_pos(master)
        if anchor is None:
            # Last resort: bottom-right of the work area — that's where the
            # tray lives on a default Windows setup.
            anchor = (wa_x + wa_w - 40, wa_y + wa_h - 8)
        ax, ay = int(anchor[0]), int(anchor[1])

        self._entries = build_entries(self._languages, self._favorites)
        n_lang_rows = sum(1 for kind, _ in self._entries if kind != "sep")
        has_sep = any(kind == "sep" for kind, _ in self._entries)

        visible = min(MAX_VISIBLE_ROWS, n_lang_rows,
                      max(MIN_VISIBLE_ROWS, int(wa_h * 0.8) // self._row_h))
        # Shrink (down to MIN_VISIBLE_ROWS) so the popup actually fits on
        # the anchor's side rather than being clamped far away from it —
        # "right next to what you clicked" beats "20 rows but elsewhere".
        chrome = 2 * self._pad + 2  # padding + border
        if prefer_above:
            fit = (ay - wa_y - chrome) // self._row_h
        else:
            fit = (wa_y + wa_h - ay - chrome) // self._row_h
        if fit >= MIN_VISIBLE_ROWS:
            visible = min(visible, int(fit))
        visible = max(1, min(visible, n_lang_rows))
        self._visible = visible

        total_h = visible * self._row_h + chrome + (self._sep_h if has_sep else 0)
        total_w = round(POPUP_WIDTH * s)

        if prefer_above and ay - total_h >= wa_y:
            y = ay - total_h
        elif ay + total_h > wa_y + wa_h:
            y = max(wa_y, wa_y + wa_h - total_h)
        else:
            y = ay
        x = max(wa_x, min(ax, wa_x + wa_w - total_w))
        self._geometry = f"{total_w}x{total_h}+{int(x)}+{int(y)}"

        # ── window: tk shell + CTk border/scrollbar + canvas rows ───────
        top = tk.Toplevel(master)
        self._top = top
        top.withdraw()  # size/position first, then show — avoids flicker
        top.overrideredirect(True)
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.configure(bg=self._card_bg)

        border = ctk.CTkFrame(top, corner_radius=0, border_width=1,
                              border_color=self._accent, fg_color=self._card_bg)
        border.pack(fill="both", expand=True)

        body = tk.Frame(border, bg=self._card_bg)
        body.pack(fill="both", expand=True, padx=self._pad, pady=self._pad)

        self._canvas = tk.Canvas(
            body, bg=self._card_bg, highlightthickness=0, bd=0,
            yscrollincrement=self._row_h,  # wheel/scrollbar step = one row
        )
        self._scrollbar = ctk.CTkScrollbar(body, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.configure(cursor="hand2")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._redraw()
        top.geometry(self._geometry)

        # ── input plumbing ──────────────────────────────────────────────
        # bind_all so the wheel works anywhere over the popup; undone in
        # close(). WHEEL_ROWS per notch — roughly 2× the speed the previous
        # CTkScrollableFrame-based version scrolled at.
        top.bind_all("<MouseWheel>", self._on_wheel, add="+")
        top.bind_all("<Button-4>", self._on_wheel, add="+")
        top.bind_all("<Button-5>", self._on_wheel, add="+")
        top.bind("<Escape>", lambda _e: self.close())
        top.bind("<Key>", self._on_key)
        # Click-outside / focus-loss closes the popup. FocusOut also fires
        # on focus moves *within* the toplevel, so re-check after idle
        # whether focus truly left the popup before closing.
        top.bind("<FocusOut>", lambda _e: top.after(60, self._check_focus))

        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", lambda _e: self._set_hover(None))

        top.deiconify()
        top.lift()
        top.focus_force()
        # Re-assert position + topmost shortly after mapping: some Windows
        # setups nudge freshly-mapped overrideredirect windows.
        top.after(80, self._reassert)

    # ── canvas rendering ────────────────────────────────────────────────

    def _redraw(self):
        """Full repaint of the row list. Cheap enough (a few hundred canvas
        items) to run on every favorites change; scroll position survives
        via the fraction captured by the caller when needed."""
        c = self._canvas
        c.delete("all")
        fav_keys = {f.strip().lower() for f in self._favorites}
        w = round(POPUP_WIDTH * self._scale) - 2 * self._pad - round(14 * self._scale)
        text_x = round(12 * self._scale)
        star_x = w - round(18 * self._scale)

        self._rows = []  # (y0, y1, lang) for hit-testing; sep rows excluded
        # Hover highlight: one rect, moved around under the pointer. Drawn
        # first so every text item naturally stacks above it.
        self._hover_rect = c.create_rectangle(0, 0, 0, 0, width=0, fill="",
                                              tags=("hover",))
        y = 0
        for kind, lang in self._entries:
            if kind == "sep":
                mid = y + self._sep_h // 2
                c.create_line(text_x, mid, w - text_x, mid,
                              fill=self._fg_muted, width=1)
                y += self._sep_h
                continue

            is_current = lang.lower() == self._current_key
            if is_current:
                c.create_rectangle(0, y, w, y + self._row_h, width=0,
                                   fill=self._row_current, tags=("bg",))
            mid = y + self._row_h // 2
            c.create_text(
                text_x, mid, anchor="w",
                text=(f"{CHECK} {lang}" if is_current else lang),
                font=(self._font_bold if is_current else self._font),
                fill=(self._accent if is_current else self._fg),
            )
            starred = lang.strip().lower() in fav_keys
            c.create_text(
                star_x, mid, anchor="center",
                text=(STAR_ON if starred else STAR_OFF),
                font=self._star_font,
                fill=(self._gold if starred else self._fg_muted),
            )
            self._rows.append((y, y + self._row_h, lang))
            y += self._row_h

        c.tag_lower("bg")
        c.tag_lower("hover")
        c.configure(scrollregion=(0, 0, w, y))

    def _row_at(self, screen_y):
        cy = self._canvas.canvasy(screen_y)
        for y0, y1, lang in self._rows:
            if y0 <= cy < y1:
                return y0, y1, lang
        return None

    def _set_hover(self, row):
        try:
            if row is None:
                self._canvas.coords(self._hover_rect, 0, 0, 0, 0)
                self._canvas.itemconfigure(self._hover_rect, fill="")
            else:
                y0, y1, _lang = row
                w = self._canvas.winfo_width()
                self._canvas.coords(self._hover_rect, 0, y0, w, y1)
                self._canvas.itemconfigure(self._hover_rect, fill=self._row_hover)
        except Exception:
            pass

    def _reassert(self):
        if self._closed:
            return
        try:
            self._top.geometry(self._geometry)
            self._top.attributes("-topmost", True)
            self._top.lift()
        except Exception:
            pass

    # ── event handlers ──────────────────────────────────────────────────

    def _on_motion(self, event):
        self._set_hover(self._row_at(event.y))

    def _on_click(self, event):
        row = self._row_at(event.y)
        if row is None:
            return
        _y0, _y1, lang = row
        if event.x >= self._canvas.winfo_width() - self._star_zone:
            self._toggle_star(lang)
        else:
            self._select(lang)

    def _on_wheel(self, event):
        if self._closed:
            return
        if getattr(event, "num", None) == 4:      # X11 wheel up
            notches = 1
        elif getattr(event, "num", None) == 5:    # X11 wheel down
            notches = -1
        else:                                     # Windows/macOS
            notches = int(event.delta / 120) or (1 if event.delta > 0 else -1)
        try:
            self._canvas.yview_scroll(-notches * WHEEL_ROWS, "units")
        except Exception:
            pass

    def _on_key(self, event):
        """Type-to-jump: pressing a letter scrolls to the first language in
        the alphabetical (non-favorites) section starting with it."""
        ch = (event.char or "").lower()
        if not ch.isalpha():
            return
        y = 0
        for kind, lang in self._entries:
            h = self._sep_h if kind == "sep" else self._row_h
            if kind == "lang" and lang.lower().startswith(ch):
                _x0, _y0, _x1, total = self._canvas.bbox("all") or (0, 0, 0, 1)
                try:
                    self._canvas.yview_moveto(y / max(1, total))
                except Exception:
                    pass
                return
            y += h

    def _check_focus(self):
        if self._closed:
            return
        try:
            focused = self._top.focus_get()
        except Exception:
            focused = None
        if focused is None or not str(focused).startswith(str(self._top)):
            self.close()

    def _select(self, lang):
        self.close()
        if self._on_select:
            self._on_select(lang)

    def _toggle_star(self, lang):
        if not self._on_toggle_favorite:
            return
        try:
            now_fav = bool(self._on_toggle_favorite(lang))
        except Exception:
            return
        # Mirror the change locally and re-section the list in place,
        # keeping the current scroll position.
        key = lang.strip().lower()
        self._favorites = [f for f in self._favorites if f.strip().lower() != key]
        if now_fav:
            self._favorites.append(lang)
        self._entries = build_entries(self._languages, self._favorites)
        try:
            frac = self._canvas.yview()[0]
        except Exception:
            frac = 0.0
        self._redraw()
        try:
            self._canvas.yview_moveto(frac)
        except Exception:
            pass

    # ── lifecycle ───────────────────────────────────────────────────────

    def close(self):
        global _open_popup
        if self._closed:
            return
        self._closed = True
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self._top.unbind_all(seq)
            except Exception:
                pass
        try:
            self._top.destroy()
        except Exception:
            pass
        if _open_popup is self:
            _open_popup = None
