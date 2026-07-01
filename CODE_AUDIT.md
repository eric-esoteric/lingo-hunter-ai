# Lingo Hunter AI — Deep Code Audit

Scope: `src/` (8 modules, ~2,900 LOC) plus `build_exe.py` / `build_linux.py`.
Goal: hidden bugs, vulnerabilities, performance, and refactoring/readability growth points.
No fixes are applied here — analysis only.

Overall: this is a well-structured, unusually well-commented codebase. Most defects are
concurrency edge cases, a few over-aggressive heuristics that can reject valid output, and
one latent Windows crash risk. Nothing is architecturally broken.

---

## 1. Cross-file issues (how the files interact)

**1.1 `app_config` is a single mutable dict shared across three threads with no lock.**
The same dict object (`LingoHunterApp.app_config`) is:
- read/written on the **Tk main thread** (Settings save, theme, favorites);
- written on the **pystray backend thread** — `_set_target_language_from_tray()` does
  `self.app_config["target_language"] = language` then `save_config(...)`;
- read on the **capture thread** — `_translate()` calls
  `lh_ai_engine.translate_text(text, ..., self.app_config)`, which then reads many keys
  (`current_provider`, `api_keys`, `active_models`, `local_servers`, `translation_mode`).

Only the *file write* is guarded (`lh_storage_manager._file_lock`); the in-memory dict is
not. The GIL makes single key assignments atomic, but compound updates are not — e.g.
`self.app_config.setdefault("active_models", {})[provider_name] = selected_models` in
`_save_and_close()` is several steps, and can interleave with a concurrent read on the
capture thread. Realistic symptoms: a translation occasionally reading a half-updated
provider/model pair, or a torn favorites update pushed to the tray. **Recommendation:**
guard config mutation with a lock, and pass an immutable *snapshot* of the needed values to
the capture/translate thread rather than the live dict.

**1.2 The capture thread receives the live config by reference during a slow call.**
`translate_text(..., config=self.app_config)` can run for many seconds (local models:
120 s timeout). If the user opens Settings and saves mid-translation, the provider/key the
call is using can change under it. Snapshot at capture time.

**1.3 Global keep-alive connection pool thrashes for local providers.**
`lh_ai_engine._connections` is a process-global `(scheme, host) -> (conn, timeout)` pool.
`_get_pooled_connection` **drops and rebuilds the connection whenever the requested timeout
differs** from the cached one. The liveness probe `check_local_server()` uses `timeout=2.0`
while the actual request uses `request_timeout=120`. For Ollama/LM Studio these hit the same
host, so every probe→request sequence tears down and reopens the connection — defeating the
keep-alive optimization exactly where it was intended. Key the pool by host and set the
timeout per-request instead of per-connection.

**1.4 Version number has two "sources of truth" that can drift.**
`lh_version.py` documents itself as the single source of truth (build scripts read it), but
`main_app.py` hardcodes `APP_VERSION = "1.0.0"` and never imports `lh_version`. The window
title can therefore disagree with the built `.exe`'s embedded version. Import it.

**1.5 Duplicated language list.**
`main_app.COMMON_LANGUAGES` and `lh_tray_menu.DEFAULT_LANGUAGES` are hand-copied. The
duplication is deliberate (avoids a tray→main import cycle) and main passes its list in, so
the tray copy is only a fallback — acceptable, but a shared `lh_constants` module would
remove the drift risk cleanly.

---

## 2. Critical bugs and vulnerabilities, by file

### src/lh_automation.py

**2.1 (High, latent) `ctypes.wintypes.MSG()` used without importing `ctypes.wintypes`.**
`_Win32HotkeyThread.run()` does only `import ctypes`, then builds
`msg = ctypes.wintypes.MSG()`. Verified: `import ctypes` does **not** attach the `wintypes`
submodule — `hasattr(ctypes, "wintypes")` is `False` until something does
`import ctypes.wintypes`. Today it works *only* as a side effect: pynput's Windows backend
imports `ctypes.wintypes`, which attaches it to the module for the whole process. If that
side-effect import ever changes, `run()` raises `AttributeError` on first use, the daemon
hotkey thread dies silently, and **the global hotkey never fires on Windows — the primary
platform**, with no visible error. Fix is one line: `import ctypes.wintypes` explicitly.

**2.2 (Medium) Accidental content destruction via blind Ctrl+A + Ctrl+V paste-back.**
The pipeline selects-all and pastes into whatever field is focused. If focus moved, or the
target isn't a simple text box, Ctrl+A/Ctrl+V can wipe the field's real content. The
`MAX_CAPTURE_CHARS` guard mitigates oversized selections, but not a wrong-focus case. The
original-clipboard restore is timing-based (0.15 s after paste, documented) and can still
race on slow/remote targets. This is an inherent design risk worth surfacing to users
(and worth a confirmation for very large replacements).

**2.3 (Low) Clipboard fallback can translate the wrong text.**
In `_capture_pipeline`, if the synthesized copy silently fails,
`wait_for_clipboard_change` returns `None` and the fallback reads the *current* clipboard —
i.e. the user's pre-existing clipboard — treats it as a valid capture, translates it, and
pastes it into the field. That replaces the field with a translation of unrelated clipboard
content. Rare, but a data-loss edge case.

**2.4 (Low) Modifier state can desync in the pynput engine.**
`_PynputHotkeyEngine` tracks held modifiers in a set and fires on
`_pressed_mods >= _required_mods`. If a key-release event is missed (focus loss while a
modifier is held), a phantom modifier lingers and can cause an unintended fire later.

### src/lh_ai_engine.py

**2.5 (High, correctness) Refusal heuristic hard-fails legitimate translations.**
`_looks_like_refusal()` flags any short reply that *starts with* phrases like `i can't`,
`i cannot`, `i won't`, `i'm unable`, `i must decline`. A perfectly valid translation can
begin that way — e.g. Spanish *"No puedo ir hoy"* → English *"I can't go today."* That trips
`refused=True`, forces a retry, gets the same correct output, and then
`if refused and _looks_like_refusal(cleaned_retry): raise AIContentPolicyBlockError` — so the
user sees a "provider refused (likely hate speech)" error **for an ordinary sentence**. Any
translation into English that legitimately opens with a refusal-shaped clause is affected.
Needs to be gated (e.g. only when target isn't English, or require the whole reply to be a
canned refusal) and should degrade to best-effort rather than raising.

**2.6 (Medium) Parse/empty-response errors do not fail over.**
`call_with_failover` retries only `HTTPError`/`URLError`/`TimeoutError`; `AIResponseParseError`
and `AIContentPolicyBlockError` are subclasses of `AIEngineError` and hit
`except AIEngineError: raise`, aborting the cascade. So a *transient* empty Gemini candidate
on model #1 stops the whole call instead of trying model #2 — undercutting the "never goes
down / automatic failover" promise in the README. Transient empty/parse failures should fall
over; a genuine content-policy block should not.

**2.7 (Low) `_looks_self_censored` false positives.**
`[A-Za-z…][\*]{1,4}[A-Za-z…]` also matches legitimate `a*b`, some SKUs, glob-like text.
Expressive mode only, so the cost is one wasted retry round trip, not a hard failure.

**2.8 (Security, Low/Med) Secrets handling.**
- Gemini key is sent in the **URL query string** (`?key=<api_key>`); URLs are the most
  likely thing to leak into logs, proxies, and crash reports. Other providers use headers.
- API keys are stored **plaintext** in `%APPDATA%\Lingo Hunter AI\config.json`. Any process
  running as the user can read every provider key. Consider DPAPI/OS keyring.

**2.9 (Low) POST retried on a stale keep-alive socket.**
`_request_via_pool` retries once on a connection-level error, including for POST. Translation
is effectively idempotent so impact is minimal, but the retry isn't method-aware.

### src/main_app.py

**2.10 (Medium) UI thread blocks on network during launch.**
`__init__` calls `self._activate(silent=True)`, which for a local provider runs
`lh_ai_engine.check_local_server(...)` (2 s timeout) **synchronously on the Tk main thread**.
If the local server is slow/unreachable, the window (or `--tray` startup) stalls at launch.
Move the probe to a background thread and marshal the result back with `after()`.

**2.11 (Medium) `os._exit(0)` in `_do_exit`.**
Hard-exits the process, skipping atexit handlers, thread joins, and buffered flushes. Config
writes are atomic + fsync, so the config file stays intact, but this is a blunt instrument —
a graceful `quit()`/teardown is safer and less likely to truncate any future non-config work.

**2.12 (Low) Single-instance socket robustness/auth.**
`_bind_single_instance_socket` binds a fixed port (47823) without `SO_REUSEADDR` and treats
*any* bind failure as "already running." An unrelated process squatting that port would make
Lingo Hunter refuse to start. The loopback listener is also unauthenticated — any local
process can connect and trigger `_restore_from_tray` (low severity: only pops the window).

**2.13 (Low) Pervasive `except Exception: pass`.**
Dozens of silent swallows across the UI and lifecycle code. Combined with the fact that the
app logs nothing, the "silent" failure modes above (2.1, 2.3) become very hard to diagnose in
the field.

### src/lh_notifications.py

**2.14 (Low) Module-global theme state + races.**
`apply_theme()` mutates module-level globals (`BG`, `TITLE`, …) that every toast reads at
build time. A theme switch concurrent with an in-flight toast animation can mix colors. Fine
for a single-window app, but it's shared mutable global state driving rendering.

**2.15 (Cosmetic) Body/muted colors collapse after theming.**
`BODY = theme_dict.get("text", BODY)` — body text ends up the same color as the title
(`text`), discarding the `text_muted` distinction the defaults had.

### src/lh_storage_manager.py

**2.16 (Low) No schema validation for most config fields.**
`load_config` validates hotkey, favorites, `target_language`, `current_provider`, and
`translation_mode`, but copies every other saved key verbatim (`cfg[key] = val`). A
hand-edited/corrupted `config.json` can inject arbitrary keys or wrong types (e.g.
`active_models` as a non-list), which surface later as errors deeper in the engine. Low risk
(local file), but a light schema/type check would fail fast and clearly.

### src/lh_tray_menu.py / src/lh_autostart.py

Both are solid. Minor: `TrayMenuController.rebuild()` swallows all exceptions silently;
`lh_autostart` treats any Run-key value as "enabled" (intended). No functional defects found.

### build_exe.py / build_linux.py

**2.17 (Cosmetic) Magic sentinel `woudl_placeholder`.**
`generate_version_file` injects a misspelled sentinel string and string-replaces it. It works,
but a typo'd magic token is fragile and reads like a bug. Prefer an explicit `{}`/format
template. Otherwise the PyInstaller flags (`--collect-submodules=pynput`, hidden imports) are
correct and thorough.

---

## 3. Growth points (what can be done better)

- **Concurrency model:** wrap config in a small `ConfigStore` with a lock; hand the capture
  thread an immutable snapshot. This removes 1.1, 1.2, and the class of bug behind them.
- **Failover semantics:** let transient parse/empty-response errors fall over to the next
  model; keep genuine content blocks non-failing-over. Add a hard cap on total retries and
  degrade to best-effort instead of raising for the heuristic backstops (2.5).
- **Heuristic backstops:** the mirror/censor/refusal checks are the riskiest correctness
  surface. Make them language-aware, prefer whole-reply matches over prefix matches, and
  never convert a *valid* translation into a user-facing error.
- **Observability:** replace `print(...)` diagnostics and silent `except: pass` with the
  `logging` module writing to a rotating file in APPDATA. This is the single highest-leverage
  change for maintainability and would have caught 2.1/2.5 immediately.
- **Typing:** add parameter/return type hints across the engine and automation; use `Enum`
  for providers and translation modes instead of bare strings; a `TypedDict` for the config.
- **Networking:** factor the hand-rolled pool; key connections by host only; per-request
  timeouts; jittered backoff; method-aware retry.
- **UI responsiveness:** move all network probes (`check_local_server`) off the Tk thread.
- **Security:** store keys via Windows DPAPI / OS keyring; move the Gemini key out of the URL
  if/when the API allows a header, and avoid logging full request URLs.
- **Single source of truth:** import `APP_VERSION` from `lh_version`; share the language list.
- **Tests:** the pure functions (`clean_translation_output`, script detection, mirror/refusal
  detection, `HotkeySpec` parsing, `load_config` merge) are trivially unit-testable and would
  pin down the very heuristics most likely to regress.

---

## 4. Step-by-step fix plan (ordered to avoid breakage)

Start with zero-behavior-change safety nets, end with the changes most likely to affect
runtime behavior — each step keeps the app shippable.

1. **Add `logging` + stop swallowing errors silently.** Introduce a rotating file logger;
   replace `except Exception: pass` with `except Exception: log.exception(...)`. No behavior
   change; makes everything below diagnosable.
2. **One-line hardening:** add `import ctypes.wintypes` in `_Win32HotkeyThread.run` (2.1) and
   import `APP_VERSION` from `lh_version` in `main_app` (1.4). Isolated, trivial.
3. **Fix the refusal false-positive (2.5)** — highest user-visible correctness bug. Add unit
   tests first (English translations starting with "I can't/won't/cannot…"), then make the
   check language-aware and non-fatal. Test-covered, so no silent regression.
4. **Move the startup `check_local_server` off the UI thread (2.10).** Contained to
   `__init__`/`_activate`; verify launch in both normal and `--tray` modes.
5. **Introduce config locking + snapshotting (1.1/1.2).** Largest correctness change — do it
   after step 1's logging and after tests exist. Keep `app_config`'s external shape identical
   so no UI code changes.
6. **Failover improvements (2.6):** allow parse/empty → next model; keep content blocks
   terminal. Add tests for the cascade order.
7. **Connection-pool fix (1.3):** key by host, set timeout per request. Verify local + remote
   providers still work.
8. **Security hardening (2.8):** encrypted key storage with a one-time migration of existing
   `config.json`. Larger and optional — do it deliberately, last among functional work.
9. **Refactors/nice-to-haves:** Enums/TypedDict, shared constants module, and finally
   replacing `os._exit` with a graceful shutdown (2.11) — this one most directly affects exit
   behavior (tray + hotkey teardown), so gate it behind manual QA.

Rationale for the order: steps 1–3 are near-zero-risk and immediately valuable; 4–7 are
contained and test-guardable; 8–9 are the broadest and are sequenced last so a regression in
them can't mask the earlier, higher-impact fixes.
