<p align="right">
  <a href="README.md"><img src="https://img.shields.io/badge/EN-English-0078D4?style=for-the-badge" alt="English"></a>
  &nbsp;
  <a href="README.ru.md"><img src="https://img.shields.io/badge/RU-Русский-CC0000?style=for-the-badge" alt="Русский"></a>
</p>

<h1 align="center">
  <img src="assets/logo.png" width="120" alt="Lingo Hunter AI logo"><br>
  Lingo Hunter AI
</h1>

<p align="center"><b>Type in any language. Hit a hotkey. It's translated — in place, instantly.</b></p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-4C1D95?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/AI%20providers-5%20cloud%20%2B%202%20local-0EA5E9?style=flat-square" alt="AI providers">
  <img src="https://img.shields.io/badge/license-non--commercial-CC0000?style=flat-square" alt="License">
</p>

<p align="center">
<details>
<summary><b>▶️ Watch the demo</b></summary>
<br>

https://github.com/user-attachments/assets/ca662a98-2d5f-4217-9d0c-c2061325597e

</details>
</p>

---

You're mid-message. A Slack DM to a client, a comment on a job board, a line in a game's team chat, a form field that only takes one language. Right now that means: select the text, alt-tab to a browser, find the translator tab, paste, wait, copy the result, alt-tab back, paste over what you wrote, hope the formatting survived. By the time you're done, the conversation has moved on.

Lingo Hunter AI skips all of that. Just type your message anywhere and hit a hotkey — no selecting, no mouse. It grabs what you typed, translates it, and pastes it straight back into the same field, in your target language. No tab switching, no clipboard juggling, no "wait, which window was I in."

<p align="center">
  <img src="assets/main menu.png" width="45%" alt="Lingo Hunter AI main window">
  <img src="assets/settings.png" width="45%" alt="Lingo Hunter AI settings panel">
</p>

## The old way vs. this way

| Without Lingo Hunter AI | With Lingo Hunter AI |
|---|---|
| Select your text | Type your message |
| Copy it | Press **Ctrl+Shift+Z** |
| Switch to a browser tab | *(that's it — no selecting, no mouse)* |
| Paste into a translator |  |
| Wait, then copy the result |  |
| Switch back and paste it in, hoping formatting held |  |
| Clean up whatever the clipboard did to your original text |  |

One step instead of six — no selecting, no mouse, and your clipboard is exactly what it was before you started.

## Why it's different

- **No selecting, no mouse.** Type your message, hit the hotkey. Lingo Hunter AI grabs everything in the active field, translates it, and pastes it back in place — the whole thing is a keyboard-only reflex, faster than reaching for your mouse.
- **Works everywhere.** Any focused text field, in any app — Slack, email, a game's chat box, a support widget, a web form. No allow-list, no integrations to configure.
- **Never goes down.** Five cloud AI providers (Gemini, OpenAI, Anthropic, DeepSeek, OpenRouter) plus local models (Ollama, LM Studio), with automatic failover across whichever models you've selected. If one is slow, blocked, or out of quota, the next one picks up the sentence without you noticing.
- **Says what you actually meant.** Most translation tools quietly sand down tone, slang, and swearing into something safe and corporate. "Expressive" mode translates it as-is — including the parts a polite AI would normally soften. Prefer the safer default instead? Switch to "Standard" any time.
- **Your keys, your traffic.** Bring your own API key and Lingo Hunter AI talks straight to the provider you picked — there's no middleman server relaying or logging your messages.
- **Simple by default, deep when you want it.** Target language — 115 to pick from in a compact scrollable selector (star your favorites for one-click switching) — hotkey, AI provider, and per-provider model failover order all live in one settings panel. Two built-in themes — Calm Dark and Cyberpunk — if you're going to stare at it all day, it should at least look good.
- **Lives in your tray, not in your way.** Closes to the background, one click to bring it back. Turn on "Start with Windows" and it's already waiting in the tray before you sit down.

## How it works

1. **Type** your message normally, in whatever app you're already using.
2. **Hit your hotkey** (**Ctrl+Shift+Z** by default — remap it to anything). No selecting, no mouse.
3. **Send it.** The field already holds the translation, in place, ready to go.

## Quick start

```
pip install -r requirements.txt
python src/main_app.py
```

Prebuilt installers: `python build_exe.py` (Windows) or `python3 build_linux.py` (Linux).

## Platform support

Windows (native global hotkey) and Linux/X11 (hardware-keycode hotkey listener). Wayland-only sessions aren't supported yet — run under XWayland.

## Changelog

### 1.2.0
- **115 target languages.** The language list grew from 22 to 115 — roughly Google Translate's coverage, now including Kazakh, Armenian, Tajik, Uzbek, Kyrgyz, Turkmen, Azerbaijani, Georgian, Uyghur, Tatar, and many more.
- **New scrollable language selector.** A compact popup replaces the old giant dropdown lists everywhere: at most 20 languages visible at a time (fewer on small screens), mouse-wheel scrolling, and type-a-letter to jump. Clicking a language selects it; the ☆ star next to each row adds or removes it from favorites on the spot.
- **Tray menu no longer overflows the screen.** The tray's "Target language" submenu now shows only your starred favorites (closest to the tray icon) plus a "Select language…" item that opens the new selector — instead of listing every language in one column that climbed off the top of the screen.
- **Settings language field reworked.** The combo box is now an entry + ▾ button that opens the same selector; typing a custom, freeform target language still works.

### 1.1.0
- **Added OpenRouter as a fifth AI provider.** OpenRouter fronts hundreds of third-party-hosted models, including community fine-tunes (the Cognitive Computations "Dolphin" line ships as the default pool) that don't carry the same content-policy refusals as the flagship providers — useful when Gemini/OpenAI/Anthropic/DeepSeek decline to translate otherwise-legal but crude or explicit text.
- **Fixed over-blocking in Expressive mode.** Gemini's `HARASSMENT` and `SEXUALLY_EXPLICIT` safety thresholds are now fully relaxed (`BLOCK_NONE`) instead of partially relaxed (`BLOCK_ONLY_HIGH`), so ordinary crude or sexually explicit-but-non-hateful text no longer gets silently blocked. Hate speech and dangerous content thresholds are untouched.

### 1.0.0
- Initial release.

## License

Non-commercial. See [license.txt](license.txt).
