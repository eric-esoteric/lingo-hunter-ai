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
  <a href="assets/demo.mp4"><img src="https://img.shields.io/badge/▶️_Watch-Demo-FF0000?style=for-the-badge" alt="Watch the demo"></a>
</p>

https://github.com/user-attachments/assets/ca662a98-2d5f-4217-9d0c-c2061325597e

No browser tab. No copy-paste into a translator and back. No app allow-list. Type your message anywhere — Slack, email, a game chat, a form — press your hotkey (**Ctrl+Shift+Z** by default, fully remappable), and it's translated right there in the same box. Your clipboard is untouched afterward.

<p align="center">
  <img src="assets/main menu.png" width="45%" alt="Lingo Hunter AI main window">
  <img src="assets/settings.png" width="45%" alt="Lingo Hunter AI settings panel">
</p>

## Why it's different

- **Works everywhere** — any focused text field, any app, no restrictions.
- **Never goes down** — four AI providers (Gemini, OpenAI, Anthropic, DeepSeek) plus local models (Ollama, LM Studio), with automatic failover across your selected models if one is slow, blocked, or unavailable.
- **Says what you meant** — an "Expressive" translation style translates tone, slang, emoji, and profanity as-is instead of the AI provider's usual corporate-safe softening; switch to "Standard" if you'd rather have the conservative default.
- **Simple by default, deep when you want it** — target language (with starrable favorites for quick switching), hotkey, AI provider, and per-provider model failover order are all one panel away, with two built-in themes (Calm Dark, Cyberpunk).
- **Lives in your tray** — closes to the background, one click to bring it back; optional "Start with Windows" launches straight into the tray.

## Quick start

```
pip install -r requirements.txt
python src/main_app.py
```

Prebuilt installers: `python build_exe.py` (Windows) or `python3 build_linux.py` (Linux).

## Platform support

Windows (native global hotkey) and Linux/X11 (hardware-keycode hotkey listener). Wayland-only sessions aren't supported yet — run under XWayland.

## License

Non-commercial. See [license.txt](license.txt).
