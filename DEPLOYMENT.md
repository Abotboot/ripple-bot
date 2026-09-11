# RippleBot deployment

Use Python 3.12, install requirements.txt, and run `python -u ripple_bot_gateway.py`.
Set DISCORD_BOT_TOKEN and GROQ_API_KEY in the host environment. Never commit credentials.
Enable Message Content Intent in Discord Developer Portal for text commands.

Bot permissions in Founders channels: View Channel, Connect, Send Messages, Embed Links,
Attach Files and Read Message History. Manage Messages is required only for purge;
Manage Roles for reaction roles; Manage Webhooks for /speak proxying.

## Meetings

Use /meeting start, status, end, stats inside Founders channels. Equivalent !meeting
commands and !note <text> work with Message Content Intent enabled. Join the VC first.
Starting announces recording in the voice channel. Voice is decrypted using Discord's
negotiated DAVE session and sent to Groq Whisper in bounded segments. Audio stays in
memory and is discarded after transcription. Status reports received audio packets,
transcript segments, dropped packets and transcription failures.
Ending waits for pending transcription before posting attendance and an AI summary.
No transcript means no invented voice summary. Uploaded voice notes and !note also work.

The pinned receive extension lacks built-in DAVE decoding. voice_capture.py uses its
Opus sink API and the SDK's negotiated DAVE session; it never disables encryption or
patches installed packages. Recheck integration when upgrading Discord dependencies.
Linux needs libopus; the Dockerfile installs it. Native Render images must provide it.

## Hosting and persistence

Render Free can sleep and restart. It does not guarantee 24/7 operation, and its local
filesystem is ephemeral. Completed reports include meeting-stats.json in the existing
private reports channel. Startup restores the latest bot-authored snapshot from the
last 100 messages. Failed report uploads are reported to the command user; those local
stats remain vulnerable to restart. Active meetings interrupted by a restart cannot
recover uncaptured audio. For continuous operation use an always-on host with outbound
UDP and durable storage. Do not add a paid service without the owner's approval.

/health returns 200 only when Discord is ready, otherwise 503. It includes voice counters
without transcripts or credentials. Render's Live badge alone does not verify commands.

## Verification

Run `python -m unittest -v test_bot`. After deploying, verify Discord READY in logs.
Start a test meeting in Discord, speak, confirm packet/transcript counters increase,
then end and inspect the report. Check /meeting stats after restart for restoration.
