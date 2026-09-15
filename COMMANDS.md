# RippleBot — Full Command Reference

Every command works as a **slash command (`/`)** and, unless noted, as a **prefix command (`!`)** (the `?` prefix works too, e.g. `?tr`).

---

## 🎵 Music

| Command | Prefix aliases | What it does |
|---|---|---|
| `/play <query>` | `!play`, `!p` | Play or queue a song by name or **Spotify / YouTube / SoundCloud link** |
| `/playtop <query>` | `!playtop` | Queue a song to play next (skips the line) |
| `/search <query>` | `!search`, `!find` | Show the top 5 results — pick with a **Play #1–5** button or `!play 1-5` |
| `/skip` | `!skip`, `!next` | Skip the current song |
| `/pause` | `!pause` | Pause playback |
| `/resume` | `!resume` | Resume playback |
| `/stop` | `!stop` | Stop and clear the queue |
| `/queue` | `!queue`, `!q` | Show upcoming songs |
| `/nowplaying` | `!nowplaying`, `!np` | Show the current song |
| `/volume <1-100>` | `!volume`, `!vol` | Set playback volume |
| `/loop` | `!loop` | Toggle looping the current song |
| `/shuffle` | `!shuffle` | Shuffle the queue |
| `/remove <position>` | `!remove` | Remove a queued song by position (2+; 1 is playing) |
| `/join [channel]` | `!join`, `!summon` | Join your voice channel (or a named channel) |
| `/leave` | `!leave`, `!disconnect`, `!dc` | Disconnect from voice |

💡 Spotify playlist/album/track links are resolved automatically to playable audio. You can also summon the bot with `@RippleBot play <song>`.

---

## 🤖 AI, Images & Translation

| Command | Prefix aliases | What it does |
|---|---|---|
| `/imagine <prompt>` | `!imagine`, `!image`, `!draw` | Generate an image (Flux AI, free & unlimited). Also `@RippleBot imagine <prompt>` |
| `/ask <question>` | — | Ask the AI brain (also just @mention the bot and chat) |
| `/translate <text> [to] [from]` | `!tr <text>`, `!translate` | Translate text. **Reply** to any message with `to spanish` to translate it. `?tr` works too |
| "Translate to English" | — | Context-menu command: right-click any message → Apps → Translate to English |
| `/speak <language> <message>` | — | Send your message as a webhook in the target language, signed with your name |
| `/languages` | — | Show the supported language codes |
| `/purge <amount>` | `!purge <amount>` | Bulk-delete messages (**Manage Messages** required) |
| 👁️ Vision | — | Attach an image and mention the bot to inspect/solve it (test strips, math screenshots, charts) |

🌍 Bonus: react to any message with a country flag emoji to translate it.

---

## 🛡️ Moderation *(permissions enforced per command)*

| Command | Prefix aliases | What it does |
|---|---|---|
| `/warn <user> [reason]` | `!warn` | Warn a member (auto-escalates: mute → kick at 3+ warnings) |
| `/warnings [user]` | `!warnings` | Show warnings (others' requires **Manage Messages**) |
| — | `!clearwarnings [user]` | Reset a member's warnings |
| `/kick <user> [reason]` | `!kick` | Kick a member (**Kick Members**) |
| `/ban <user> [reason]` | `!ban` | Ban a member (**Ban Members**) |
| `/unban <user_id>` | `!unban` | Unban by user ID (**Ban Members**) |
| `/timeout <user> [minutes] [reason]` | `!timeout`, `!mute` | Timeout a member (default 10 min, max 30 days) |
| `/untimeout <user>` | `!untimeout`, `!unmute` | Remove a timeout |
| `/clear <amount>` | `!clear` | Delete the last N messages in the channel |
| `/slowmode <seconds>` | `!slowmode` | Set channel slowmode (0 disables) |
| `/lock` | `!lock` | Lock the current channel |
| `/unlock` | `!unlock` | Unlock the current channel |
| `/automod` | `!automod` | AutoMod settings: `status`, `toggle`, `addword <word>`, `removeword <word>`, `wordlist` (**Manage Server**) |

**AutoMod runs passively:** deletes messages with banned words, excessive spam/caps/mentions/prompt-mention abuse, warns the author, and escalates repeat offenders — no command needed.

---

## 📈 Leveling (MEE6-style, automatic)

| Command | Prefix aliases | What it does |
|---|---|---|
| `/rank [user]` | `!rank` | Show XP, level, and progress bar |
| `/leaderboard` | `!leaderboard`, `!lb` | Top 10 members by XP |

Chat activity earns XP (60s cooldown per user); level-ups are announced automatically.

---

## 👋 Welcome & Roles

| Command | Prefix aliases | What it does |
|---|---|---|
| `/welcome channel <#channel>` | — | Set the welcome channel |
| `/welcome message <text>` | — | Custom welcome text — supports `{user}`, `{server}`, `{count}` |
| `/welcome goodbye [#channel]` | — | Set (or disable) the goodbye channel |
| `/welcome test` | — | Preview the welcome message |
| `/autorole [role]` | `!autorole` | Role auto-given to new members (blank = disable) |

---

## ⏰ Reminders, Polls & Utility

| Command | Prefix aliases | What it does |
|---|---|---|
| `/remind <time> <text>` | `!remind`, `!remindme` | Reminder — time like `10m`, `1h30m`, `2d` |
| `/reminders` | `!reminders` | List your active reminders |
| `/poll <question>` | `!poll` | Reaction poll (👍 👎 🤷) |

---

## 🎲 Fun

| Command | Prefix aliases | What it does |
|---|---|---|
| `/8ball <question>` | `!8ball` | Magic 8-ball |
| `/roll [dice]` | `!roll` | Roll dice — `d20`, `3d6`, etc. |
| `/flip` | `!flip`, `!coinflip` | Flip a coin |
| `/choose <a, b, ...>` | `!choose` | Bot picks for you (comma-separated) |
| `/avatar [user]` | `!avatar` | Show a member's avatar (full size) |

---

## ℹ️ Info

| Command | Prefix aliases | What it does |
|---|---|---|
| `/userinfo [user]` | `!userinfo`, `!user` | Member info: joined, roles, warnings, level |
| `/serverinfo` | `!serverinfo`, `!server` | Server stats: members, boosts, channels |
| `/help` | `!help`, `!commands`, `!h` | Quick command cheat sheet |

---

## 🎙️ Meetings *(Founders channels / voice only)*

| Command | Prefix aliases | What it does |
|---|---|---|
| `/meeting start` | `!meeting start`, `!startmeeting` | Start recording + attendance in your voice channel |
| `/meeting end` | `!meeting end`, `!endmeeting` | End the meeting and post the AI summary + transcript |
| `/meeting status` | `!meeting status` | Live status: audio received, transcript size |
| `/meeting stats` | `!meeting stats` | Meeting participation leaderboard |
| — | `!note <text>` | Add a note to the active meeting transcript |

Voice messages sent during a meeting are transcribed automatically.

---

## ⚙️ Passive features (always on)

- **AutoMod** message filtering with warning escalation
- **XP leveling** with level-up announcements
- **Welcome / goodbye messages** and **auto-role**
- **Rate limiting** on AI commands (per-user sliding window + global protection)
- **Fast music** — flat yt-dlp search with lazy stream resolution and a 30-min track cache
- **Music watchdog** — auto-recovers playback if a stream stalls
