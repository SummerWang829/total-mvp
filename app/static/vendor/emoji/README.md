# Emoji assets (self-hosted: nothing is loaded from an outside server)

| File | What | Licence |
|---|---|---|
| `index.js`, `picker.js`, `database.js`, `i18n/*.js` | emoji-picker-element 1.29.1 (the picker: search, categories, skin tones, recents), https://github.com/nolanlawson/emoji-picker-element | Apache-2.0 |
| `data/en.json`, `data/zh.json` | emoji-picker-element-data 1.8.0 (the emoji list with names in English and Chinese) | Apache-2.0 |

The emoji are drawn with each device's own emoji font (Apple Color Emoji, Segoe UI Emoji / Fluent on Windows 11, Noto Color Emoji on Android), like iMessage,
WhatsApp and WeChat do. Tried and dropped: the Noto Color Emoji web font from @fontsource (a bitmap font that Chrome on Windows draws as blank squares).
If one look on every device is wanted later, a colour font that works everywhere (for example Twemoji Mozilla, COLRv0) can be added behind a feature check.

The server also reads `data/*.json` to check that a reaction is a real emoji (`app/signals/emoji.py`).
