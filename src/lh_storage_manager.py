# lh_storage_manager.py — config persistence for Lingo Hunter AI
#
# Drastically trimmed compared to Job Hunter AI's storage manager: there is no
# vacancy/resume database at all. The only thing that needs to survive a
# restart is the user's small set of settings (provider, API keys, model
# choice, hotkey, target language). Reuses the exact crash-safe atomic write
# pattern from the original (mkstemp -> write -> flush -> fsync -> replace).

import os
import json
import tempfile
import threading

import lh_logging

_log = lh_logging.get_logger(__name__)

# Safe: lh_automation has no dependency back on this module, so importing it
# here for hotkey validation doesn't create a cycle. Only HotkeySpec (a plain
# dataclass-style class with no pynput/pyperclip calls in its constructor) is
# used, so this import stays safe even when AUTOMATION_AVAILABLE is False.
import lh_automation

APPDATA_DIR = os.path.join(
    os.environ.get('APPDATA', os.path.expanduser('~')), 'Lingo Hunter AI'
)
CONFIG_FILE = os.path.join(APPDATA_DIR, "config.json")

_file_lock = threading.Lock()

DEFAULT_HOTKEY = {"mod1": "ctrl", "mod2": "shift", "key": "L"}

default_config = {
    # AI provider / model — one of the kept "custom settings"
    "current_provider": "Gemini",
    "api_keys": {},          # {"Gemini": "...", "OpenAI": "...", ...}
    "active_models": {},     # {"Gemini": ["gemini-2.0-flash"], ...}
    "local_servers": {       # base URLs for local providers
        "Ollama": "http://localhost:11434",
        "LM Studio": "http://localhost:1234",
    },
    # Target language — the other kept "custom setting"
    "target_language": "English",
    # Translation style — "expressive" (default, translate profanity/slang
    # faithfully) or "standard" (the AI provider's own default, more
    # conservative register). See lh_ai_engine.TRANSLATION_MODES.
    "translation_mode": "expressive",
    # Hotkey combination — the third kept "custom setting"
    "hotkey": dict(DEFAULT_HOTKEY),
    # Always-on, not exposed as a UI setting (kept minimal per spec).
    "notifications_enabled": True,
    # "Start with Windows" checkbox in Settings. The registry (see
    # lh_autostart.py) is the actual source of truth for whether autostart
    # is active — this stored value is just what the checkbox showed last,
    # kept here so the config file is a complete record and reduces a
    # registry read for casual inspection. Defaults off: this app has never
    # auto-started before, so existing users shouldn't suddenly get a new
    # Run-key entry they didn't ask for.
    "launch_at_startup": False,
    # Visual theme — a series feature carried over from Job Hunter AI.
    "theme": "Calm Dark",
    # Starred/"favorite" languages — pinned to the top of the Settings
    # language section, and to the *bottom* of the tray quick-switch menu
    # (closest to the tray icon, least reaching — see lh_tray_menu.py).
    # Order is preserved (most-recently-starred last), not alphabetical, so
    # users can arrange their most-used languages by starring order.
    "favorite_languages": [],
}


def _ensure_dirs():
    os.makedirs(APPDATA_DIR, exist_ok=True)


def _write_json_atomic(filepath: str, data) -> None:
    """Crash-safe write: temp file in same dir -> fsync -> atomic replace."""
    _ensure_dirs()
    dirpath = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def _load_file(filepath: str):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Corrupt/unreadable config: log it and fall back to defaults rather
        # than crashing, but don't stay silent about why settings "reset."
        _log.exception("failed to read config at %s; falling back to defaults", filepath)
        return None


def init_db() -> None:
    """No real DB to initialize — just make sure the appdata dir exists."""
    _ensure_dirs()


def load_config() -> dict:
    """Return default_config merged with any saved config.json, with light
    backward-compat handling in case the on-disk file is from a partial/older
    save (e.g. missing keys after a manual edit)."""
    with _file_lock:
        saved = _load_file(CONFIG_FILE) or {}

    cfg = dict(default_config)
    cfg["api_keys"] = dict(default_config["api_keys"])
    cfg["active_models"] = dict(default_config["active_models"])
    cfg["local_servers"] = dict(default_config["local_servers"])
    cfg["hotkey"] = dict(default_config["hotkey"])
    # Explicit copy, same reasoning as the dicts above: `dict(default_config)`
    # is a shallow copy, so without this line every fresh config would share
    # the *same* list object as default_config["favorite_languages"] — the
    # first user who starred a language would silently mutate the module-
    # level default for every config loaded afterwards in the same process.
    cfg["favorite_languages"] = list(default_config["favorite_languages"])

    for key, val in saved.items():
        if key in ("api_keys", "active_models", "local_servers") and isinstance(val, dict):
            cfg[key].update(val)
        elif key == "hotkey" and isinstance(val, dict):
            cfg["hotkey"].update(val)
        elif key == "favorite_languages" and isinstance(val, list):
            cfg[key] = [f for f in val if isinstance(f, str) and f.strip()]
        else:
            cfg[key] = val

    # Legacy/partial hotkey safety: ensure all three sub-fields are present.
    for hk_field, hk_default in DEFAULT_HOTKEY.items():
        cfg["hotkey"].setdefault(hk_field, hk_default)

    # Hard validation safety net: reject/auto-correct any on-disk hotkey that
    # lacks a Ctrl/Alt/Shift modifier (e.g. from a hand-edited config.json, an
    # older buggy save, or a sync conflict) *before* it ever reaches the UI or
    # the hotkey engine. Round-tripping through HotkeySpec is the single
    # source of truth for this rule — its constructor already refuses
    # Win-only/no-modifier combos and falls back to Ctrl+Alt.
    cfg["hotkey"] = lh_automation.HotkeySpec.from_dict(cfg["hotkey"]).to_dict()

    if not cfg.get("target_language"):
        cfg["target_language"] = default_config["target_language"]
    if not cfg.get("current_provider"):
        cfg["current_provider"] = default_config["current_provider"]
    if cfg.get("translation_mode") not in ("expressive", "standard"):
        cfg["translation_mode"] = default_config["translation_mode"]

    return cfg


def save_config(config_data: dict) -> None:
    with _file_lock:
        _write_json_atomic(CONFIG_FILE, config_data)


def is_local_provider(provider_name: str) -> bool:
    return provider_name in ("Ollama", "LM Studio")


def get_local_server_url(config: dict, provider_name: str) -> str:
    defaults = default_config["local_servers"]
    return (config.get("local_servers") or {}).get(provider_name, defaults.get(provider_name, ""))


# ───────────────────────── favorite ("starred") languages ───────────────────
#
# Kept deliberately storage-only: this module owns reading/writing/validating
# the favorites list, callers (Settings UI, tray menu) just call these
# helpers and then persist via save_config() same as any other config change.
# Matching is case-insensitive (so "spanish" and "Spanish" aren't both
# starrable at once) but the originally-typed casing is preserved for
# display.

def get_favorite_languages(config: dict) -> list:
    """Returns the starred languages, in the order they were added (oldest
    first). Always a plain list of non-empty strings, even if the on-disk
    value was corrupted/missing."""
    favs = (config or {}).get("favorite_languages")
    if not isinstance(favs, list):
        return []
    return [f for f in favs if isinstance(f, str) and f.strip()]


def is_favorite_language(config: dict, language: str) -> bool:
    needle = (language or "").strip().lower()
    if not needle:
        return False
    return any(f.strip().lower() == needle for f in get_favorite_languages(config))


def add_favorite_language(config: dict, language: str) -> list:
    """Stars `language`, mutating config in place. No-op if already starred
    or if `language` is blank. Returns the resulting list."""
    language = (language or "").strip()
    if not language:
        return get_favorite_languages(config)

    favs = config.get("favorite_languages")
    if not isinstance(favs, list):
        favs = []
    if not is_favorite_language(config, language):
        favs = favs + [language]
    config["favorite_languages"] = favs
    return favs


def remove_favorite_language(config: dict, language: str) -> list:
    """Unstars `language` (case-insensitive), mutating config in place.
    Returns the resulting list."""
    needle = (language or "").strip().lower()
    favs = get_favorite_languages(config)
    config["favorite_languages"] = [f for f in favs if f.strip().lower() != needle]
    return config["favorite_languages"]


def toggle_favorite_language(config: dict, language: str) -> bool:
    """Stars `language` if it isn't already starred, unstars it otherwise.
    Returns True if it's a favorite after the call, False otherwise."""
    if is_favorite_language(config, language):
        remove_favorite_language(config, language)
        return False
    add_favorite_language(config, language)
    return True


def set_favorite_languages(config: dict, languages) -> list:
    """Bulk-replaces the favorites list, de-duplicating case-insensitively
    while preserving first-seen order/casing. Mutates config in place."""
    seen = set()
    cleaned = []
    for lang in (languages or []):
        lang = (lang or "").strip()
        key = lang.lower()
        if lang and key not in seen:
            seen.add(key)
            cleaned.append(lang)
    config["favorite_languages"] = cleaned
    return cleaned
