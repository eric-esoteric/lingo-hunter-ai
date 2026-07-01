# v1.0.0 Freeze — Code Audit Checklist

Run all scans from the repo root against `src/`.

## 0. Verify from a clean checkout first

Editor caches and synced/mounted drives can show stale file contents. Before
trusting any scan below, clone the repo fresh (or otherwise bypass any local
cache) and run:

```
python -m py_compile src/*.py
```

on that clean copy. A file that looks fine in your editor but fails here
means your working copy and the real committed file have diverged — resolve
that before doing anything else.

## 1. Job Hunter AI leftovers

Known hits today (provenance comments only, not logic bugs) — decide whether
to keep a single "ported from" attribution line per file or strip entirely
for the freeze:

```
src/lh_ai_engine.py:3
src/lh_automation.py:3
src/lh_notifications.py:3
src/lh_storage_manager.py:3-4, 52
src/main_app.py:3, 6-7, 10, 77, 459
```

Regex to re-scan after edits:

```
grep -rniE "job[ _-]?hunter" src/
grep -rniE "\bjh_[a-z_]+\b" src/          # old jh_* module/identifier prefixes
grep -rniE "\b(resume|vacanc(y|ies)|job filter|worker thread|results window)\b" src/
```

The last pattern should return **zero** hits — those are job-search-domain
terms with no reason to exist in a translation tool. Any hit is either dead
code or a copy-paste artifact.

## 2. Dead code / spaghetti

```
grep -rnE "\b(TODO|FIXME|XXX|HACK)\b" src/
grep -rnE "\bpdb\.set_trace\(\)|\bbreakpoint\(\)" src/
grep -rnE "^\s*#.*(def |class |import |self\.\w+\s*=|return |print\()" src/   # commented-out code, manual review — will also match legit prose comments
```

`print(...)` calls (`lh_automation.py`) are today's only diagnostic output
mechanism, and they go nowhere useful in a windowed/tray app with no
attached console. Before freeze, either route them through `logging` to a
file under the app's config directory, or remove them — don't ship silent
`print()` as the error-visibility story.

Unused-import / dead-symbol pass (more reliable than a regex):

```
pip install ruff --break-system-packages
ruff check src/ --select F401,F811,F841
```

## 3. Background daemon footprint

Everything below is based on the current implementation — verify against
the clean checkout, not a cached copy.

- **IPC listener** (`main_app.py`, single-instance socket): polls
  `accept()` with a 1-second timeout in a `while self._alive.is_set()` loop,
  forever, for the life of the app. Sub-second responsiveness isn't needed
  for "bring window to front" — bump the timeout to 5–10s to cut wakeups
  roughly 5–10x with no user-visible difference.
- **CaptureThread**: spawned per hotkey press, daemon thread, guarded by
  `_capture_in_progress` so rapid hotkey spam can't pile up threads. Already
  correct — just confirm this guard survives any future edits.
- **Win32HotkeyThread / pynput listener**: event-driven (`GetMessage` /
  OS-level hook), not a busy-poll loop. Correct as-is; don't introduce a
  `time.sleep()` polling loop here in future changes.
- **Toast notifications** (`lh_notifications.py`): each toast schedules
  several `.after(...)` callbacks (fade/slide). If notifications can fire
  faster than they're dismissed, confirm old toast windows are destroyed
  (not just hidden) so `.after()` callbacks don't accumulate against dead
  widgets.
- **Packaging**: prefer a PyInstaller **onedir** build over **onefile** for
  this app. Onefile re-unpacks to a temp directory on every launch — for an
  app that's meant to sit in the tray and get relaunched/auto-started
  repeatedly, that's avoidable disk churn and slower startup on every boot.
- **Asset weight**: `assets/logo.png` is ~1.5MB at 1024×1024. Fine for a
  README/marketing asset, wasteful as the actual in-app tray/window icon —
  make sure the runtime icon path points at `icon.ico` (already the case)
  rather than loading the full PNG into memory anywhere in the UI code.

## 4. Final sanity pass

- [ ] `python -m py_compile src/*.py` clean on a fresh checkout
- [ ] `ruff check src/` clean (or all findings triaged)
- [ ] Zero hits for job-search-domain terms (§1, last regex)
- [ ] No bare `print()` left as the only error-visibility mechanism
- [ ] IPC/toast/thread footprint items above reviewed
- [ ] Tag `v1.0.0` only after the above are checked off
