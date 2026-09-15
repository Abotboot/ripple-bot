---
title: Ripple Bot
emoji: 🌊
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 5.20.0
python_version: '3.12'
app_file: app.py
pinned: false
---

# 🌊 RippleBot

24/7 Discord AI assistant, meeting recorder, and music player — runs free on Hugging Face Spaces.

📄 **Full command reference: [COMMANDS.md](COMMANDS.md)**

## What RippleBot does

**AI** — Groq-powered chat (`@RippleBot <question>` or `/ask`), vision (attach an image and mention the bot),
chat recaps (`@RippleBot summarize`), image generation (`/imagine`, `!draw`), translation (`/translate`, `!tr`,
flag-reaction translation, reply with `to spanish`), and webhook-proxy speech (`/speak`).

**Music** — `/play` or `!play` with a song name, Spotify link, YouTube link, or SoundCloud link.
Flat-search resolution and stream caching keep `!play` to a couple of seconds; alternative versions are one
click away. Includes `skip`, `pause`, `resume`, `stop`, `queue`, `nowplaying`, `volume`, `join`, `leave`,
`search`, `loop`, `shuffle`, `remove`, and `playtop`. Spotify needs no API keys (oEmbed resolution), and a
watchdog auto-disconnects from empty voice channels.

**Moderation (MEE6 / Carl-bot style)** — `warn`, `warnings`, `clearwarnings`, `kick`, `ban`, `unban`,
`timeout`/`mute`, `untimeout`/`unmute`, `clear`, `slowmode`, `lock`, `unlock` — with automatic escalation
(3 warnings → 10 min timeout, 5 → 1 h, 7 → 24 h).

**AutoMod** — blocks server invite links, configurable banned words, mention spam, excessive caps, and
flood spam (8 messages / 8 s), deleting the message, warning the user, and escalating repeat offenders.
Configure with `/automod status|toggle|addword|removeword|wordlist`.

**Community** — MEE6-style XP leveling with `/rank` and `/leaderboard` (level-up announcements),
welcome & goodbye messages (`/welcome`), auto-role (`/autorole`), persistent reminders (`/remind 10m drink water`),
reaction polls (`/poll`), fun commands (`/8ball`, `/roll`, `/flip`, `/choose`), info commands (`/avatar`,
`/userinfo`, `/serverinfo`), and `/help`.

**Meetings** — `/meeting start|status|end|stats` tracks attendance, transcribes live voice (DAVE-encrypted
capture) and voice-note attachments, and posts a summary report with cumulative stats that survive redeploys.

Nearly everything works as both a **slash command** (`/play`) and a **prefix command** (`!play`, `?play`, or
`@RippleBot play`). Slash commands are upserted idempotently on startup.

## Run locally

```bash
pip install -r requirements.txt
set DISCORD_BOT_TOKEN=...   # or put it in bot_token.txt (git-ignored)
set GROQ_API_KEY=...
python app.py               # dashboard on :7860 + bot gateway
python -m unittest test_bot test_upgrades   # offline test suite
```

The Discord application needs the **Server Members**, **Message Content**, and voice intents enabled.

## Deploy (Hugging Face Spaces)

The space at https://huggingface.co/spaces/BcStray/ripple-bot runs `app.py` (Gradio status dashboard +
gateway) with secrets `DISCORD_BOT_TOKEN` and `GROQ_API_KEY` configured in Space settings. See
[DEPLOYMENT.md](DEPLOYMENT.md) for details.
