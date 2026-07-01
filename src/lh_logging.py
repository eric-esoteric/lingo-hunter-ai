# lh_logging.py — process-wide logging setup for Lingo Hunter AI.
#
# Central place to configure logging so the previously-silent failure modes
# (a dead hotkey thread, a swallowed clipboard error, a background translate
# exception) leave a diagnosable trail instead of vanishing. Writes to a
# rotating file in the same per-user APPDATA dir the config lives in, and
# mirrors to stderr when running from a console (unfrozen dev runs).
#
# Deliberately dependency-free (stdlib logging only) and safe to import from
# any module, including ones imported before the GUI exists. get_logger()
# lazily initializes the root handlers exactly once, so import order doesn't
# matter and there's no separate "call setup() first" step to forget.

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

APP_DIRNAME = "Lingo Hunter AI"
LOG_FILENAME = "lingo_hunter.log"

_configured = False


def _log_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIRNAME)


def _log_path() -> str:
    return os.path.join(_log_dir(), LOG_FILENAME)


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("lh")
    root.setLevel(logging.INFO)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
    )

    # Rotating file handler — best effort. If the APPDATA dir can't be created
    # or opened (locked-down machine, read-only profile), fall back silently
    # to stderr-only rather than crashing the app over logging.
    try:
        os.makedirs(_log_dir(), exist_ok=True)
        fh = RotatingFileHandler(
            _log_path(), maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    # Console handler only when a real stderr is attached (i.e. not a frozen
    # --windowed build, where sys.stderr can be None). Avoids AttributeError
    # on write to a missing stream in the packaged GUI app.
    if getattr(sys, "stderr", None) is not None:
        try:
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            root.addHandler(sh)
        except Exception:
            pass

    # Guarantee at least one handler so "no handlers" warnings never appear.
    if not root.handlers:
        root.addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Returns a namespaced child of the 'lh' logger, configuring handlers on
    first use. `name` is typically the module name, e.g. get_logger(__name__)."""
    _configure_once()
    short = name.split(".")[-1] if name else "app"
    return logging.getLogger("lh").getChild(short)
