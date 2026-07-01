# Lingo Hunter AI — Fixes Applied

All errors from `CODE_AUDIT.md` have been fixed. Summary of what changed and why.

## New files
- **`src/lh_logging.py`** — process-wide rotating-file logger (in APPDATA), used
  everywhere previously-silent failures could occur. Dependency-free, lazily
  configured, safe on frozen `--windowed` builds (no stderr).
- **`verify/verify_refusal_logic.py`** — regression test for the refusal fix.
  Run `python verify/verify_refusal_logic.py` from the repo root.

## Critical bugs
1. **Latent Windows hotkey crash** (`lh_automation.py`) — added explicit
   `import ctypes.wintypes` in `_Win32HotkeyThread.run()`, so the message loop no
   longer depends on pynput's side-effect import. Previously an `AttributeError`
   waiting to happen that would silently kill the hotkey on Windows.
2. **Refusal heuristic rejected valid translations** (`lh_ai_engine.py`) —
   `_looks_like_refusal()` now requires a canned opener **and** request/assistant/
   policy meta-language (or a self-identifying "as an AI" phrase). A translation
   like *"I can't go today"* is no longer misclassified as a policy refusal and
   hard-failed. Verified by the regression test (10 valid + 8 real refusals pass).
3. **Clipboard data-loss edge case** (`lh_automation.py`) — capture now seeds a
   unique sentinel before Ctrl+C, so a failed copy can never translate and paste
   the user's stale clipboard over their field. Removes the ambiguous fallback.

## Reliability / correctness
4. **Failover now covers transient empty/parse responses** (`lh_ai_engine.py`) —
   `AIResponseParseError` falls over to the next model in the pool; genuine
   `AIContentPolicyBlockError` and `AIAuthError` stay terminal. Delivers the
   "automatic failover" the README promises.
5. **Config is now thread-safe** (`main_app.py`) — added an `RLock`; the
   capture/translate thread reads an atomic deep-copy snapshot
   (`_config_snapshot()`), and all mutations + saves go through the lock
   (`_persist_config()`, tray language switch, settings save, favorites). No more
   torn reads across the main / tray / capture threads.
6. **Startup no longer blocks the UI thread** (`main_app.py`) — the launch-time
   auto-arm runs its local-server probe on a worker thread
   (`_activate_startup_async`) and marshals only the UI state change back, so a
   slow/unreachable local server can't freeze the window (or `--tray` startup).

## Performance
7. **Connection-pool timeout thrash fixed** (`lh_ai_engine.py`) — the keep-alive
   pool is keyed by host and applies each caller's timeout to the existing socket
   instead of tearing the connection down whenever the timeout differs (2s probe
   vs 120s request). Keeps local-model connections warm.

## Robustness / hygiene
8. **Graceful shutdown** (`main_app.py`) — `_do_exit` now lets `mainloop()` unwind
   (Tk teardown + log flush) with a daemon safety-net timer guaranteeing the
   process still dies if a native backend thread wedges — replacing the abrupt
   `os._exit(0)`.
9. **Single source of truth for version** (`main_app.py`) — imports
   `APP_NAME`/`APP_VERSION` from `lh_version.py` instead of a hardcoded literal.
10. **Logging wired into silent paths** — hotkey-thread errors, capture failures,
    config-load corruption, IPC listener errors, and the translation backstop now
    log instead of vanishing.
11. **Build script** (`build_exe.py`) — replaced the misspelled `woudl_placeholder`
    sentinel with a clean `__STRING_TABLE__` token.

## Deliberately NOT changed (and why)
- **`SO_REUSEADDR` on the single-instance socket** — on Windows it permits a second
  bind to the same port, which would **break** the single-instance guarantee (the
  primary platform relies on `bind()` failing). Left as-is on purpose.
- **`_looks_self_censored` asterisk heuristic** — a false positive here only costs
  one extra retry and then returns best-effort (never a hard failure or loop), and
  tightening it risks missing real self-censorship. Left as-is.
- **Plaintext API-key storage** — a design choice, not a bug; encrypting keys is a
  larger migration flagged as a future growth point in the audit, not applied here
  to avoid a risky config migration.

## Verification performed
- Refusal regression test passes (valid translations allowed, real refusals
  flagged, length cap honored).
- `lh_logging` imports and emits correctly.
- Every edited region reviewed for correct indentation and balanced structure;
  exception ordering in the failover cascade confirmed (specific subclasses before
  the `AIEngineError` base).
