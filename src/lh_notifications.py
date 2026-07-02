# lh_notifications.py — in-app toast notifications (no external dependencies)
#
# Reused near-verbatim from Job Hunter AI's jh_notifications.py. Only the
# visible app name and comments were translated/rebranded; the animation,
# DPI-scaling, and thread-safety logic are unchanged since they're entirely
# generic and have nothing to do with job-hunting vs. translation.
import threading

_toast_ref = [None]                     # the single active toast; a new one replaces the old
_notification_lock = threading.Lock()   # guards _toast_ref mutations from concurrent threads

# ── Logical sizes (at 100% scale / 96 DPI) ──────────────────────────────────
# Fix: the toast was correctly positioned but its text was reported as
# "incredibly small" — the box grew to fit larger text below.
_W_LOG      = 430     # toast width
_H_LOG      = 116     # toast height
_MARGIN_LOG = 8        # margin from screen edge
_LIFT_LOG   = 72       # how far below the final position the animation starts
_BAR_LOG    = 4        # width of the colored left accent bar
_PAD_X_LOG  = 16       # horizontal content padding
_PAD_Y_LOG  = 13       # vertical content padding

# ── Logical font point sizes (at 100% scale) ────────────────────────────────
# These run through the same `sc` DPI scaling as the rest of the toast (see
# F_ICON/F_TITLE/F_BODY/F_CLOSE below). A prior round bumped these up from
# tiny unscaled values; per user feedback on a real build they ended up too
# big in practice, so they're trimmed back down a notch here.
_FONT_ICON_LOG  = 15
_FONT_TITLE_LOG = 12
_FONT_BODY_LOG  = 11
_FONT_CLOSE_LOG = 15

# ── Colors ───────────────────────────────────────────────────────────────────
BG          = "#111622"
BORDER      = "#1D2535"
TITLE       = "#E9EDF0"
BODY        = "#B0BAC6"
MUTED       = "#6B778A"
CYAN        = "#00D8C6"
RED         = "#D24B4B"
FONT_FAMILY = "Segoe UI"

# Fixed, glyph-safe font for the toast's icon glyphs only ("⚠" warning /
# "✓" checkmark). These are deliberately NOT theme-linked: narrow monospace
# fonts (e.g. the Cyberpunk theme's "Consolas") often lack these Unicode
# symbol glyphs, which risks a silent missing-glyph ("tofu") render if the
# icon followed FONT_FAMILY like the title/body/close-button text does.
ICON_FONT_FAMILY = "Segoe UI"

APP_NAME = "Lingo Hunter AI"


def apply_theme(theme_dict: dict) -> None:
    """Sync toast colors and font family from the active theme dict."""
    global BG, BORDER, TITLE, BODY, MUTED, CYAN, RED, FONT_FAMILY
    BG          = theme_dict.get("card_bg",         BG)
    BORDER      = theme_dict.get("secondary_hover", BORDER)
    TITLE       = theme_dict.get("text",            TITLE)
    BODY        = theme_dict.get("text",            BODY)
    MUTED       = theme_dict.get("text_muted",      MUTED)
    CYAN        = theme_dict.get("accent",          CYAN)
    RED         = theme_dict.get("danger",          RED)
    # Read font_family directly off the theme dict (set by main_app.py's
    # THEMES table — "Segoe UI" for Calm Dark, "Consolas" for Cyberpunk) so
    # toast title/body/close-button text actually switches font along with
    # the rest of the app when the user picks Cyberpunk. Falls back to the
    # older fonts["section"][0] lookup for compatibility, then to whatever
    # FONT_FAMILY already was.
    if "font_family" in theme_dict:
        FONT_FAMILY = theme_dict["font_family"]
    else:
        fonts = theme_dict.get("fonts", {})
        if fonts:
            FONT_FAMILY = fonts.get("section", (FONT_FAMILY,))[0]


def _get_scale(root) -> float:
    """
    Returns the DPI scale factor — same method used by the main window.
    Priority: CTk method -> Windows API -> 1.0.
    """
    try:
        return root._get_window_scaling()
    except Exception:
        pass
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0
    except Exception:
        return 1.0


def get_work_area(window):
    """
    Returns (x, y, width, height) in physical pixels of the *work area* (the
    monitor's usable region, excluding the taskbar) of the monitor nearest to
    `window`. Falls back to the raw screen size on non-Windows platforms or
    if the Win32 lookup fails for any reason.

    This single helper fixes two related bugs: naive use of
    winfo_screenwidth()/winfo_screenheight() (a) ignores the taskbar, so
    "centered" windows visually drift, and (b) on multi-monitor Windows
    setups can report the combined virtual-desktop size instead of the
    monitor actually showing the window, so "bottom-right" placement can
    land on the wrong monitor or off-screen. Querying the work area of the
    monitor nearest the window (or the toast's owner window) sidesteps both.
    """
    try:
        import ctypes
        import platform as _platform
        if _platform.system() == "Windows":
            window.update_idletasks()

            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class _MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                            ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

            MONITOR_DEFAULTTONEAREST = 2
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    try:
        return (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    except Exception:
        return (0, 0, 1920, 1080)


def _play_sound(is_error: bool) -> None:
    """Quiet Windows system sound, played on a background thread."""
    try:
        import winsound
        # MB_ICONEXCLAMATION(48) — warning, softer
        # MB_ICONASTERISK(64)    — info, standard notification sound
        winsound.MessageBeep(48 if is_error else 64)
    except Exception:
        pass


def _build_toast_safe(root, message: str, is_error: bool = False, on_click=None) -> None:
    """Builds a Telegram-style toast in the bottom-right corner of the screen (thread-safe)."""
    import tkinter as tk

    # ── Atomically destroy the previous toast before creating a new one ───────
    # _notification_lock guards _toast_ref against concurrent mutations from
    # multiple worker threads queuing toasts at the same time.
    with _notification_lock:
        if _toast_ref[0] is not None:
            try:
                if _toast_ref[0].winfo_exists():
                    _toast_ref[0].destroy()
            except Exception:
                pass
            finally:
                _toast_ref[0] = None

    # ── DPI scale — single source of truth for all sizes ─────────────────────
    sc = _get_scale(root)

    # Physical sizes: logical x scale
    W      = int(_W_LOG      * sc)
    H      = int(_H_LOG      * sc)
    MARGIN = int(_MARGIN_LOG * sc)
    LIFT   = int(_LIFT_LOG   * sc)
    BAR    = int(_BAR_LOG    * sc)
    PX     = int(_PAD_X_LOG  * sc)
    PY     = int(_PAD_Y_LOG  * sc)

    # Font point sizes, scaled like every other size in this toast (this is
    # the actual font-size fix — previously these were hardcoded, unscaled
    # point values).
    F_ICON  = max(_FONT_ICON_LOG,  int(round(_FONT_ICON_LOG  * sc)))
    F_TITLE = max(_FONT_TITLE_LOG, int(round(_FONT_TITLE_LOG * sc)))
    F_BODY  = max(_FONT_BODY_LOG,  int(round(_FONT_BODY_LOG  * sc)))
    F_CLOSE = max(_FONT_CLOSE_LOG, int(round(_FONT_CLOSE_LOG * sc)))

    # Position: work area (excludes taskbar) of the monitor showing `root`,
    # in physical pixels — not the raw (and on multi-monitor setups,
    # sometimes virtual-desktop-wide) screen size.
    wa_x, wa_y, wa_w, wa_h = get_work_area(root)
    final_x = wa_x + wa_w - W - MARGIN
    final_y = wa_y + wa_h - H - MARGIN
    start_y = final_y + LIFT

    accent   = RED  if is_error else CYAN
    icon_chr = "⚠"  if is_error else "✓"

    # ── Build window ─────────────────────────────────────────────────────────
    toast = tk.Toplevel(root)
    _toast_ref[0] = toast
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
    toast.configure(bg=BORDER)   # BORDER shown as a 1px frame via padx/pady
    toast.resizable(False, False)
    toast.geometry(f"{W}x{H}+{final_x}+{start_y}")

    # ── Close/click handlers — defined before building widgets ──────────────
    def _close():
        if _toast_ref[0] is toast:
            _toast_ref[0] = None
        try:
            toast.destroy()
        except Exception:
            pass

    def _handle_click(event=None):
        _close()
        if on_click is not None:
            try:
                root.after(0, on_click)
            except Exception:
                try:
                    on_click()
                except Exception:
                    pass

    # ── Inner container, 1px smaller on every side ──────────────────────────
    _body_cursor = "hand2" if on_click is not None else ""
    outer = tk.Frame(toast, bg=BG, cursor=_body_cursor)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    if on_click is not None:
        outer.bind("<Button-1>", _handle_click)

    # Left accent bar
    tk.Frame(outer, bg=accent, width=BAR).pack(side="left", fill="y")

    # Content area (padding scaled)
    body_frame = tk.Frame(outer, bg=BG, padx=PX, pady=PY, cursor=_body_cursor)
    body_frame.pack(side="left", fill="both", expand=True)
    if on_click is not None:
        body_frame.bind("<Button-1>", _handle_click)

    # ── Header row ───────────────────────────────────────────────────────────
    head = tk.Frame(body_frame, bg=BG, cursor=_body_cursor)
    head.pack(fill="x")
    if on_click is not None:
        head.bind("<Button-1>", _handle_click)

    tk.Label(
        head, text=icon_chr, bg=BG, fg=accent,
        font=(ICON_FONT_FAMILY, F_ICON, "bold")
    ).pack(side="left", padx=(0, int(7 * sc)))

    tk.Label(
        head, text=APP_NAME, bg=BG, fg=TITLE,
        font=(FONT_FAMILY, F_TITLE, "bold")
    ).pack(side="left")

    close_btn = tk.Label(
        head, text="×", bg=BG, fg=MUTED,
        font=(FONT_FAMILY, F_CLOSE), cursor="hand2"
    )
    close_btn.pack(side="right", padx=(int(6 * sc), 0))
    close_btn.bind("<Button-1>", lambda e: _close())

    # ── Message text ──────────────────────────────────────────────────────────
    msg_lbl = tk.Label(
        body_frame, text=message, bg=BG, fg=BODY,
        font=(FONT_FAMILY, F_BODY), anchor="w", justify="left",
        wraplength=W - int(70 * sc),            # wraplength in physical px
        cursor=_body_cursor,
    )
    msg_lbl.pack(fill="x", pady=(int(5 * sc), 0))
    if on_click is not None:
        msg_lbl.bind("<Button-1>", _handle_click)

    # ── Animation: quick ease-out slide bottom-to-top (Telegram-style) ──────
    def _slide(cur_y: int):
        if not toast.winfo_exists():
            return
        dist = cur_y - final_y
        if dist <= 2:
            toast.geometry(f"{W}x{H}+{final_x}+{final_y}")
            # Per user feedback the toast was sticking around too long —
            # dwell time cut from 5000ms to 2200ms before the fade-out starts.
            toast.after(2200, lambda: _fade_out_instance(1.0))
            return
        step  = max(4, dist // 2)
        new_y = cur_y - step
        toast.geometry(f"{W}x{H}+{final_x}+{new_y}")
        toast.after(10, lambda: _slide(new_y))

    # ── Animation: smooth fade-out (instance-bound, isolated from global state) ──
    # Captures `toast` directly so _fade_out_instance isn't affected by later
    # mutations of _toast_ref[0] caused by a concurrent second notification.
    def _fade_out_instance(alpha: float = 1.0):
        try:
            exists = toast.winfo_exists()
        except Exception:
            return
        if not exists:
            return
        alpha -= 0.2     # bigger step + shorter interval below -> roughly 2-3x faster fade
        if alpha <= 0.0:
            try:
                toast.destroy()
            except Exception:
                pass
            # Only nullify the global reference if it still points to this exact instance.
            if _toast_ref[0] is toast:
                _toast_ref[0] = None
            return
        try:
            toast.attributes("-alpha", alpha)
            toast.after(15, lambda: _fade_out_instance(alpha))
        except Exception:
            pass

    # Sound on a background thread (doesn't block the UI)
    threading.Thread(target=_play_sound, args=(is_error,), daemon=True).start()
    _slide(start_y)


def send_notification(title: str, message: str, root=None, on_click=None, is_error: bool = None) -> None:
    """
    Shows a notification.
    root -> embedded toast with correct DPI scaling.
    No root -> system notification via plyer / win10toast (fallback).
    on_click -> called when the toast body is clicked (embedded toast only).
    is_error -> explicit success/failure flag controlling the toast's icon,
    accent color, and beep. Callers should always pass this explicitly:
    for a success toast `message` is the translated text itself, and that
    text can legitimately contain the word "error" (e.g. translating a
    sentence about a bug report) — substring-sniffing the message for
    "error" would then flag a successful translation as a failure. Falls
    back to the old substring heuristic only if a caller omits it.
    """
    if is_error is None:
        is_error = ("error" in message.lower() or "error" in title.lower())

    if root is not None:
        try:
            root.after(0, lambda: _build_toast_safe(root, message, is_error, on_click))
            return
        except Exception:
            pass

    # ── Fallback to system notifications ─────────────────────────────────────
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name=APP_NAME, timeout=5)
        return
    except Exception:
        pass
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, message, duration=5, threaded=True)
    except Exception:
        pass
