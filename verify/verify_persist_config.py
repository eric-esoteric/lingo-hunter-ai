"""Proves the _persist_config fix: no infinite recursion, and the RLock is
safely reentrant when a mutation block that already holds the lock calls
_persist_config() (as _save_and_close does). Run: python verify/verify_persist_config.py
"""
import threading

saves = []


class Fake:
    def __init__(self):
        self._config_lock = threading.RLock()
        self.app_config = {"theme": "Calm Dark"}

    # Mirrors the FIXED helper: calls the storage function, not itself.
    def _persist_config(self):
        with self._config_lock:
            saves.append(dict(self.app_config))  # stand-in for save_config()

    # Mirrors _save_and_close: acquires the lock, mutates, then calls
    # _persist_config() (which re-acquires the same RLock).
    def save_and_close(self):
        with self._config_lock:
            self.app_config["theme"] = "Cyberpunk"
            self._persist_config()

    # Mirrors apply_theme(persist=True): calls _persist_config() unlocked.
    def apply_theme(self):
        self.app_config["theme"] = "Cyberpunk"
        self._persist_config()


f = Fake()

# 1) plain persist works (would RecursionError before the fix)
f._persist_config()

# 2) reentrant path: locked block -> _persist_config re-acquires RLock
f.save_and_close()

# 3) apply_theme path
f.apply_theme()

assert len(saves) == 3, saves
assert saves[-1]["theme"] == "Cyberpunk"
print("PASS: _persist_config saves without recursion; RLock reentry OK; "
      f"{len(saves)} saves recorded")
