# lh_automation.py — global hotkey + generic capture/translate/paste pipeline
#
# Ported from Job Hunter AI's jh_automation.py. Keeps the cross-platform
# hotkey infrastructure verbatim in spirit (Win32 RegisterHotKey thread for
# Windows, pynput hardware-keycode listener for Linux, Wayland zero-trust
# guard) since the user confirmed Windows + Linux support.
#
# Removed entirely vs. the original: the browser-process allow-list
# (_BROWSER_PROCESS_NAMES), active-window browser detection, the Ctrl+L
# address-bar URL capture step, the MD5-URL-fallback, and the browser-
# viewport refocus click. None of that makes sense once capture targets
# "any focused input field in any application."
#
# Added vs. the original: a paste-back step. After the AI returns a
# translation, the engine writes it to the clipboard, re-selects the field
# (Ctrl+A) and pastes (Ctrl+V), then restores the user's original clipboard
# contents.

import os
import sys
import time
import uuid
import threading
import platform

import lh_logging

_log = lh_logging.get_logger(__name__)

try:
    import pyperclip
    from pynput import keyboard
    from pynput.keyboard import Key, KeyCode
    AUTOMATION_AVAILABLE = True
except Exception as _import_err:
    AUTOMATION_AVAILABLE = False
    pyperclip = None
    keyboard = None
    Key = None
    KeyCode = None
    _log.warning("pynput/pyperclip unavailable; hotkey capture disabled: %s", _import_err)

IS_WINDOWS = platform.system() == "Windows"
IS_DARWIN = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


# ───────────────────────── hardware keycode tables ──────────────────────────
# Layout-independent A-Z hardware keycodes, used so the hotkey still fires
# correctly on non-QWERTY / non-Latin keyboard layouts (matches the physical
# key, not the character it currently produces).

_LINUX_KEY_VK = {
    "A": 38, "B": 56, "C": 54, "D": 40, "E": 26, "F": 41, "G": 42, "H": 43,
    "I": 31, "J": 44, "K": 45, "L": 46, "M": 58, "N": 57, "O": 32, "P": 33,
    "Q": 24, "R": 27, "S": 39, "T": 28, "U": 30, "V": 55, "W": 25, "X": 53,
    "Y": 29, "Z": 52,
}

_DARWIN_KEY_VK = {
    "A": 0, "B": 11, "C": 8, "D": 2, "E": 14, "F": 3, "G": 5, "H": 4,
    "I": 34, "J": 38, "K": 40, "L": 37, "M": 46, "N": 45, "O": 31, "P": 35,
    "Q": 12, "R": 15, "S": 1, "T": 17, "U": 32, "V": 9, "W": 13, "X": 7,
    "Y": 16, "Z": 6,
}


def _make_capture_key(darwin_vk, win32_vk, linux_vk):
    """Build the platform-correct KeyCode used for synthesizing Ctrl+A /
    Ctrl+C / Ctrl+V regardless of the user's current keyboard layout."""
    if not AUTOMATION_AVAILABLE:
        return None
    if IS_DARWIN:
        return KeyCode.from_vk(darwin_vk)
    if IS_WINDOWS:
        return KeyCode.from_vk(win32_vk)
    return KeyCode.from_vk(linux_vk)


if AUTOMATION_AVAILABLE:
    _KEY_A = _make_capture_key(_DARWIN_KEY_VK["A"], 0x41, _LINUX_KEY_VK["A"])
    _KEY_C = _make_capture_key(_DARWIN_KEY_VK["C"], 0x43, _LINUX_KEY_VK["C"])
    _KEY_V = _make_capture_key(_DARWIN_KEY_VK["V"], 0x56, _LINUX_KEY_VK["V"])
else:
    _KEY_A = _KEY_C = _KEY_V = None


def _ctrl_key():
    return Key.cmd if IS_DARWIN else Key.ctrl


# ───────────────────────── modifier classification ──────────────────────────

if AUTOMATION_AVAILABLE:
    _CTRL_KEYS = frozenset({Key.ctrl, Key.ctrl_l, Key.ctrl_r})
    _ALT_KEYS = frozenset({Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr})
    _SHIFT_KEYS = frozenset({Key.shift, Key.shift_l, Key.shift_r})
    _WIN_KEYS = frozenset({Key.cmd, Key.cmd_l, Key.cmd_r})
else:
    _CTRL_KEYS = _ALT_KEYS = _SHIFT_KEYS = _WIN_KEYS = frozenset()


def _mod_name_for_key(key):
    if key in _CTRL_KEYS:
        return "ctrl"
    if key in _ALT_KEYS:
        return "alt"
    if key in _SHIFT_KEYS:
        return "shift"
    if key in _WIN_KEYS:
        return "win"
    return None


def _key_set(*keys):
    return frozenset(k for k in keys if k is not None)


# ───────────────────────── HotkeySpec ───────────────────────────────────────

class HotkeySpec:
    """Describes a global hotkey as (mod1, mod2, key). mod2 may be "none".
    Carries the same no-bare-letter-hotkey safety invariant as the original:
    a hotkey with zero modifiers would hijack every press of that letter
    across the OS, so it's auto-corrected to Ctrl+Alt."""

    __slots__ = ("mod1", "mod2", "key")

    _VALID_MODS = ("ctrl", "alt", "shift", "win", "none")

    # Only these count as a "real" system modifier for hotkey-safety purposes.
    # "win" is deliberately excluded even though it's a valid *secondary*
    # modifier to combine with one of these — on its own it's unreliable as a
    # guard (many window managers/OSes intercept bare Win-chord combos before
    # apps ever see them, and some don't support registering it at all), so a
    # hotkey consisting of Win alone (or Win+Win/none) is treated the same as
    # having no modifier: it risks hijacking every press of that key.
    _SAFE_MODS = frozenset({"ctrl", "alt", "shift"})

    def __init__(self, mod1: str = "ctrl", mod2: str = "shift", key: str = "L"):
        mod1 = (mod1 or "ctrl").strip().lower()
        mod2 = (mod2 or "none").strip().lower()
        key = (key or "L").strip().upper()

        if mod1 not in self._VALID_MODS:
            mod1 = "ctrl"
        if mod2 not in self._VALID_MODS:
            mod2 = "none"
        if len(key) != 1 or not key.isalpha():
            key = "L"

        if not self.has_required_modifier(mod1, mod2):
            print("[HotkeySpec] Refusing hotkey without a Ctrl/Alt/Shift modifier "
                  "(Win alone is not enough); falling back to Ctrl+Alt.")
            mod1, mod2 = "ctrl", "alt"

        self.mod1 = mod1
        self.mod2 = mod2
        self.key = key

    @classmethod
    def has_required_modifier(cls, mod1: str, mod2: str) -> bool:
        """True if at least one of mod1/mod2 is Ctrl, Alt, or Shift. Used both
        by the constructor's own safety fallback and by callers (Settings UI,
        storage manager) that need to validate a combo *before* constructing
        a HotkeySpec, e.g. to show a specific error instead of a silent
        auto-correction."""
        mods = {(mod1 or "").strip().lower(), (mod2 or "").strip().lower()}
        return bool(mods & cls._SAFE_MODS)

    @classmethod
    def default(cls):
        return cls("ctrl", "shift", "L")

    @classmethod
    def from_dict(cls, d: dict):
        d = d or {}
        return cls(d.get("mod1", "ctrl"), d.get("mod2", "shift"), d.get("key", "L"))

    @classmethod
    def from_config(cls, config: dict):
        hk = (config or {}).get("hotkey")
        if isinstance(hk, dict):
            return cls.from_dict(hk)
        if isinstance(hk, str):
            return cls._from_legacy_string(hk)
        return cls.default()

    @classmethod
    def _from_legacy_string(cls, s: str):
        # legacy pynput-style string, e.g. "<ctrl>+<shift>+x"
        parts = [p.strip("<>").lower() for p in s.split("+") if p.strip()]
        mods = [p for p in parts if p in ("ctrl", "alt", "shift", "win", "cmd")]
        keys = [p for p in parts if p not in ("ctrl", "alt", "shift", "win", "cmd")]
        mods = ["win" if m == "cmd" else m for m in mods]
        mod1 = mods[0] if len(mods) >= 1 else "ctrl"
        mod2 = mods[1] if len(mods) >= 2 else "none"
        key = keys[0].upper() if keys else "L"
        return cls(mod1, mod2, key)

    def to_dict(self) -> dict:
        return {"mod1": self.mod1, "mod2": self.mod2, "key": self.key}

    def display(self) -> str:
        names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        parts = [names[self.mod1]]
        if self.mod2 != "none":
            parts.append(names[self.mod2])
        parts.append(self.key)
        return " + ".join(parts)

    def required_mods(self) -> frozenset:
        return frozenset(m for m in (self.mod1, self.mod2) if m != "none")

    def win32_vk(self) -> int:
        return ord(self.key)

    def win32_mod_flags(self) -> int:
        MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
        flags = 0
        for m in self.required_mods():
            if m == "alt":
                flags |= MOD_ALT
            elif m == "ctrl":
                flags |= MOD_CONTROL
            elif m == "shift":
                flags |= MOD_SHIFT
            elif m == "win":
                flags |= MOD_WIN
        return flags

    def pynput_vk(self) -> int:
        if IS_DARWIN:
            return _DARWIN_KEY_VK.get(self.key, 0)
        if IS_WINDOWS:
            return ord(self.key)
        return _LINUX_KEY_VK.get(self.key, 0)


# ───────────────────────── platform security guard ──────────────────────────

class PlatformSecurityException(RuntimeError):
    pass


class ContentCaptureError(Exception):
    pass


def enforce_linux_subsystem_guard():
    """pynput/XTest does not work natively under Wayland — global hotkeys
    and synthetic keystrokes will silently fail or behave unpredictably.
    Refuse to start rather than offer a broken feature."""
    if not IS_LINUX:
        return
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    if session_type == "wayland" or wayland_display:
        raise PlatformSecurityException(
            "Lingo Hunter AI needs global hotkeys and synthetic keystrokes, which "
            "are not supported under native Wayland sessions. Please log into an "
            "X11 session (or XWayland-backed session), or run under Xorg, and "
            "try again."
        )


# ───────────────────────── Windows hotkey thread ────────────────────────────

class _Win32HotkeyThread(threading.Thread):
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    HOTKEY_ID = 1

    def __init__(self, spec: HotkeySpec, fire_fn):
        super().__init__(daemon=True, name="Win32HotkeyThread")
        self.spec = spec
        self.fire_fn = fire_fn
        self._thread_id = None
        self._registered = False
        self._stop_flag = threading.Event()

    def run(self):
        # `import ctypes` alone does NOT attach the `wintypes` submodule to the
        # ctypes module — `ctypes.wintypes.MSG` is an AttributeError until
        # something explicitly imports ctypes.wintypes. It previously only
        # "worked" because pynput's Windows backend imports it as a side
        # effect; import it directly here so this thread never depends on that.
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        mod_flags = self.spec.win32_mod_flags() | self.MOD_NOREPEAT
        vk = self.spec.win32_vk()
        ok = user32.RegisterHotKey(None, self.HOTKEY_ID, mod_flags, vk)
        self._registered = bool(ok)
        if not ok:
            _log.error("RegisterHotKey failed for %s (may already be in use by "
                       "another app).", self.spec.display())

        msg = ctypes.wintypes.MSG()
        while not self._stop_flag.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == self.WM_HOTKEY:
                try:
                    self.fire_fn()
                except Exception:  # noqa: BLE001
                    _log.exception("hotkey callback error")

        if self._registered:
            try:
                user32.UnregisterHotKey(None, self.HOTKEY_ID)
            except Exception:
                _log.exception("UnregisterHotKey failed")

    def stop(self):
        self._stop_flag.set()
        if self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        self.join(timeout=1.0)


# ───────────────────────── pynput hotkey engine (macOS/Linux) ──────────────

class _PynputHotkeyEngine:
    def __init__(self, spec: HotkeySpec, fire_fn):
        self.spec = spec
        self.fire_fn = fire_fn
        self._pressed_mods = set()
        self._main_fired = False
        self._listener = None
        self._target_vk = spec.pynput_vk()
        self._required_mods = spec.required_mods()

    def start(self):
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _on_press(self, key):
        mod = _mod_name_for_key(key)
        if mod:
            self._pressed_mods.add(mod)
            return
        vk = getattr(key, "vk", None)
        if vk == self._target_vk and self._pressed_mods >= self._required_mods and not self._main_fired:
            self._main_fired = True
            try:
                self.fire_fn()
            except Exception:  # noqa: BLE001
                _log.exception("hotkey callback error")

    def _on_release(self, key):
        mod = _mod_name_for_key(key)
        if mod:
            self._pressed_mods.discard(mod)
            return
        vk = getattr(key, "vk", None)
        if vk == self._target_vk:
            self._main_fired = False


# ───────────────────────── clipboard helpers ────────────────────────────────

def wait_for_clipboard_change(old_value, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current = pyperclip.paste()
        except Exception:
            current = None
        if current is not None and current != old_value:
            return current
        time.sleep(0.01)
    return None


def _clipboard_write(text: str, retries: int = 3):
    for attempt in range(retries):
        try:
            pyperclip.copy(text)
            return
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.05)


def _is_valid_capture(text) -> bool:
    return isinstance(text, str) and text.strip() != ""


# "Blind input" guard: a captured selection this large is almost never a
# single chat message the user meant to translate — it's far more likely an
# accidental Ctrl+A in a document/editor/browser tab. Processing it anyway
# would burn provider tokens on content nobody asked to translate AND, worse,
# silently overwrite the active window's real content with a translation of
# text the user didn't intend to select. Guarded as a hard cap rather than a
# soft warning so the paste-back step never runs in this case.
MAX_CAPTURE_CHARS = 5000


# ───────────────────────── capture/translate/paste engine ──────────────────

class TranslateCaptureEngine:
    """Orchestrates: hotkey press -> capture focused field's text -> translate
    via translate_fn -> paste translation back -> restore clipboard.

    Unlike the original BrowserCaptureEngine (which pushed captured items
    onto a queue for a background worker), this engine runs the whole
    pipeline synchronously inside the capture thread, since translation is a
    single immediate action per hotkey press with no need for queuing."""

    def __init__(self, translate_fn, app_ready_fn, hotkey_spec=None,
                 notify_fn=None, capture_success_fn=None, capture_failure_fn=None,
                 busy_fn=None, max_capture_chars: int = MAX_CAPTURE_CHARS):
        self.translate_fn = translate_fn
        self.app_ready_fn = app_ready_fn
        self.spec = hotkey_spec or HotkeySpec.default()
        self.notify_fn = notify_fn or (lambda: None)
        self.capture_success_fn = capture_success_fn or (lambda original, translated: None)
        self.capture_failure_fn = capture_failure_fn or (lambda error: None)
        # Fired (instead of silently no-op'ing) when the hotkey is pressed
        # again while a previous capture/translate/paste is still running.
        # Without this, a second press mid-translation does nothing visible
        # at all, which reads as "the hotkey is broken" rather than "it's
        # still working on the first one."
        self.busy_fn = busy_fn or (lambda: None)
        self.max_capture_chars = max_capture_chars

        self._capture_in_progress = False
        self._lock = threading.Lock()
        self._win32_thread = None
        self._pynput_engine = None
        self._controller = None
        self._running = False

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if not AUTOMATION_AVAILABLE:
            _log.warning("pynput/pyperclip not installed; hotkey capture disabled.")
            return
        enforce_linux_subsystem_guard()
        self._controller = keyboard.Controller()
        fire_fn = self._make_fire_fn()
        if IS_WINDOWS:
            self._win32_thread = _Win32HotkeyThread(self.spec, fire_fn)
            self._win32_thread.start()
        else:
            self._pynput_engine = _PynputHotkeyEngine(self.spec, fire_fn)
            self._pynput_engine.start()
        self._running = True

    def stop(self):
        if self._win32_thread:
            self._win32_thread.stop()
            self._win32_thread = None
        if self._pynput_engine:
            self._pynput_engine.stop()
            self._pynput_engine = None
        self._running = False

    def set_hotkey(self, spec: HotkeySpec):
        was_running = self._running
        if was_running:
            self.stop()
        self.spec = spec
        if was_running:
            self.start()

    # ── fire/dispatch ────────────────────────────────────────────────────

    def _make_fire_fn(self):
        def _fire():
            with self._lock:
                if self._capture_in_progress:
                    try:
                        self.busy_fn()
                    except Exception:
                        pass
                    return
                self._capture_in_progress = True
            try:
                threading.Thread(target=self._capture_pipeline, name="CaptureThread", daemon=True).start()
            except Exception:
                with self._lock:
                    self._capture_in_progress = False
        return _fire

    def _release_all_modifiers(self):
        """Release any modifier keys still physically held from the hotkey
        combo itself, so they don't leak into the synthesized Ctrl+A/Ctrl+C
        (e.g. avoids Ctrl+Shift+A selecting nothing in some apps)."""
        for k in (Key.ctrl_l, Key.ctrl_r, Key.shift_l, Key.shift_r,
                  Key.alt_l, Key.alt_r, Key.cmd, Key.cmd_l, Key.cmd_r):
            try:
                self._controller.release(k)
            except Exception:
                pass

    def _send_combo(self, *keys):
        ctrl = _ctrl_key()
        with self._controller.pressed(ctrl):
            for k in keys:
                self._controller.press(k)
                self._controller.release(k)

    def _capture_pipeline(self):
        # Perf note: this pipeline used to clear the clipboard and poll it on
        # a fixed 6x50ms schedule, plus a flat 150ms settle after paste —
        # roughly half a second of pure local overhead before the network
        # call even started. It now detects the *actual* clipboard-change
        # event (wait_for_clipboard_change, ~10ms poll) instead of clearing
        # and waiting out a fixed schedule, and the settle delays are trimmed
        # to the minimum needed for the target app to register the
        # synthesized keystrokes. Remaining end-to-end latency is dominated
        # by the AI provider's network round trip, not this local code.
        original_clipboard = None
        try:
            if not self.app_ready_fn():
                return

            try:
                original_clipboard = pyperclip.paste()
            except Exception:
                original_clipboard = None

            self._release_all_modifiers()
            time.sleep(0.01)

            self.notify_fn()

            # Seed a unique sentinel onto the clipboard *before* the copy, so we
            # can reliably tell whether Ctrl+C actually replaced it. Without
            # this, a field whose text happens to equal the previous clipboard
            # is indistinguishable from a copy that silently failed — and the
            # old fallback would then translate and paste the *stale* clipboard,
            # overwriting the user's field with unrelated text. Comparing
            # against a random sentinel removes that ambiguity in both
            # directions: any real captured text differs from the sentinel, and
            # a failed copy leaves the sentinel untouched.
            sentinel = "​​LH-CAPTURE-" + uuid.uuid4().hex
            _clipboard_write(sentinel)

            self._send_combo(_KEY_A)
            time.sleep(0.02)
            self._send_combo(_KEY_C)

            captured_text = wait_for_clipboard_change(sentinel, timeout=1.5)
            # Copy never landed (clipboard still holds the sentinel, or the
            # read failed) -> capture nothing rather than translate stale data.
            if captured_text == sentinel:
                captured_text = None

            if not _is_valid_capture(captured_text):
                raise ContentCaptureError(
                    "Could not capture any text. Make sure your cursor is in a "
                    "text field with a message typed, then try the hotkey again."
                )

            # Blind-input guard clause: bail out *before* any tokens are spent
            # and *before* the Ctrl+A/Ctrl+V paste-back step, so an oversized
            # accidental capture never overwrites the active window.
            if len(captured_text) > self.max_capture_chars:
                self.capture_failure_fn(
                    f"Captured selection is too long ({len(captured_text):,} "
                    f"characters, limit {self.max_capture_chars:,}). Paused — "
                    "select a shorter piece of text and try the hotkey again."
                )
                return

            try:
                translated_text = self.translate_fn(captured_text)
            except Exception as ai_err:
                user_msg = getattr(ai_err, "user_message", None) or str(ai_err)
                self.capture_failure_fn(user_msg)
                return

            if not isinstance(translated_text, str) or not translated_text.strip():
                self.capture_failure_fn("The AI returned an empty translation.")
                return

            _clipboard_write(translated_text)
            time.sleep(0.015)

            self._send_combo(_KEY_A)
            time.sleep(0.02)
            self._send_combo(_KEY_V)
            # Race-condition fix: give the OS/target app time to actually read
            # the clipboard and complete the paste before `finally` restores
            # `original_clipboard`. 0.08s was too tight under load (remote
            # desktops, slower apps, IME-heavy fields) and the paste would
            # sometimes land the *restored* clipboard instead of the
            # translation. 0.15s is a safe, still-imperceptible margin.
            time.sleep(0.15)

            self.capture_success_fn(captured_text, translated_text)

        except ContentCaptureError as e:
            self.capture_failure_fn(str(e))
        except Exception as e:  # noqa: BLE001
            self.capture_failure_fn(f"Unexpected error: {e}")
        finally:
            if original_clipboard is not None:
                _clipboard_write(original_clipboard)
            with self._lock:
                self._capture_in_progress = False
