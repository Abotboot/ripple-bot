"""
RippleBot Service
- Gateway WebSocket listener
- Slash Commands: /imagine, /image, /purge, /translate, /speak, /languages, /ask, "Translate to English" (Context Menu)
- Text Commands: !imagine <prompt>, !image <prompt>, !draw <prompt>, !purge <n>, !tr <text>
- Anti-Abuse & Rate Limiter: Per-user sliding window + global API protection
- Direct Discord File Attachments: Full quality images uploaded directly to Discord CDN (never blank!)
- Vision AI: Inspect and solve images, test strips, math screenshots, charts
- Image Gen: High-fidelity Flux AI image generation (free, unlimited)
- Chat Summarization: Summarize what members missed in chat
- Conversational Brain: Groq-powered, chill, concise, games, bulletproof math formatting
- Flag Reaction Translation
- Self-Assign Role Reactions
- Channel Webhook Proxying for /speak
"""

import asyncio
import json
import urllib.request
import urllib.parse
import urllib.error
import re
import sys
import os
import io
import logging
from contextlib import closing
import discord
from voice_capture import MeetingRecorder

from deep_translator import GoogleTranslator
import groq_engine
import rate_limiter
import image_gen
import meeting_tracker
import music_player
import extras
import role_setup
import chat_memory

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '').strip().lstrip('\ufeff')
if not TOKEN:
    token_file = os.path.join(os.path.dirname(__file__), "bot_token.txt")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding='utf-8-sig') as f:
                TOKEN = f.read().strip()
        except Exception:
            pass

GUILD_ID = '1545531421081346101'
BOT_ID = '1546333781764345936'

ROLE_MAP = {
    '1546398389401288826': {
        '📢': '1546397884872659034',
        '🚨': '1546397885610987540',
        '🌊': '1546397886441586689',
    },
    '1546398404085817346': {
        '🧪': '1546397888001736706',
        '💻': '1546397889167753267',
        '🏫': '1546397890081988648',
        '💧': '1546397890929496147',
    }
}

FLAG_TO_LANG = {
    '🇺🇸': ('en', 'English'),
    '🇬🇧': ('en', 'English'),
    '🇪🇸': ('es', 'Spanish'),
    '🇲🇽': ('es', 'Spanish'),
    '🇫🇷': ('fr', 'French'),
    '🇩🇪': ('de', 'German'),
    '🇯🇵': ('ja', 'Japanese'),
    '🇨🇳': ('zh-CN', 'Chinese (Simplified)'),
    '🇹🇼': ('zh-TW', 'Chinese (Traditional)'),
    '🇮🇹': ('it', 'Italian'),
    '🇵🇹': ('pt', 'Portuguese'),
    '🇧🇷': ('pt', 'Portuguese'),
    '🇷🇺': ('ru', 'Russian'),
    '🇰🇷': ('ko', 'Korean'),
    '🇸🇦': ('ar', 'Arabic'),
    '🇮🇳': ('hi', 'Hindi'),
    '🇳🇱': ('nl', 'Dutch'),
    '🇹🇷': ('tr', 'Turkish'),
    '🇻🇳': ('vi', 'Vietnamese'),
    '🇵🇱': ('pl', 'Polish'),
    '🇺🇦': ('uk', 'Ukrainian'),
    '🇸🇪': ('sv', 'Swedish'),
}

LANG_ALIASES = {
    'en': 'en', 'english': 'en',
    'es': 'es', 'spanish': 'es', 'espanol': 'es', 'español': 'es', 'sp': 'es',
    'fr': 'fr', 'french': 'fr', 'français': 'fr',
    'de': 'de', 'german': 'de', 'deutsch': 'de',
    'ja': 'ja', 'japanese': 'ja', 'jp': 'ja',
    'zh': 'zh-CN', 'chinese': 'zh-CN', 'cn': 'zh-CN', 'mandarin': 'zh-CN',
    'zh-cn': 'zh-CN', 'zh-tw': 'zh-TW', 'taiwanese': 'zh-TW',
    'it': 'it', 'italian': 'it', 'italiano': 'it',
    'pt': 'pt', 'portuguese': 'pt', 'português': 'pt', 'br': 'pt',
    'ru': 'ru', 'russian': 'ru',
    'ko': 'ko', 'korean': 'ko', 'kr': 'ko',
    'ar': 'ar', 'arabic': 'ar',
    'hi': 'hi', 'hindi': 'hi',
    'nl': 'nl', 'dutch': 'nl',
    'tr': 'tr', 'turkish': 'tr',
    'vi': 'vi', 'vietnamese': 'vi',
    'tl': 'tl', 'tagalog': 'tl', 'filipino': 'tl',
    'pl': 'pl', 'polish': 'pl',
    'uk': 'uk', 'ukrainian': 'uk',
    'el': 'el', 'greek': 'el',
    'sv': 'sv', 'swedish': 'sv',
}

_webhook_cache = {}

async def api_call(endpoint, method='GET', data=None, max_retries=3):
    path, _, query = endpoint.partition('?')
    # Preserve Discord's major parameters so rate limits are shared per channel.
    params = {}
    parts = path.split('/')
    if len(parts) > 2 and parts[1] in ('channels', 'guilds'):
        key = 'channel_id' if parts[1] == 'channels' else 'guild_id'
        params[key] = parts[2]
        parts[2] = '{' + key + '}'
    route = discord.http.Route(method, '/'.join(parts), **params)
    kwargs = {'params': dict(urllib.parse.parse_qsl(query))} if query else {}
    if data is not None:
        kwargs['json'] = dict(data)
        if 'view' in kwargs['json']:
            view_obj = kwargs['json'].pop('view')
            if hasattr(view_obj, 'to_components'):
                kwargs['json']['components'] = view_obj.to_components()
            try:
                client.add_view(view_obj)
            except Exception:
                pass
        if 'content' in data or 'embeds' in data:
            kwargs['json'].setdefault('allowed_mentions', {'parse': []})
    return await client.http.request(route, **kwargs)

tracker = meeting_tracker.MeetingTracker(BOT_ID, api_call)



async def meeting_monitor_loop():
    await client.wait_until_ready()
    while True:
        await asyncio.sleep(10)
        try:
            if tracker.check_auto_end(grace_period_secs=60):
                await finish_meeting(automatic=True)
        except Exception as exc:
            logging.error('Meeting auto-end failed: %s', type(exc).__name__)

async def send_channel_file(channel_id, file_bytes, filename="image.jpg", payload=None):
    channel = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
    options = message_options(payload or {})
    with closing(discord.File(io.BytesIO(file_bytes), filename=filename)) as upload:
        message = await channel.send(file=upload, **options)
    return {'id': str(message.id)}

async def interaction_edit_original_file(i_token, file_bytes, filename="image.jpg", payload=None):
    interaction = _interactions[i_token]
    with closing(discord.File(io.BytesIO(file_bytes), filename=filename)) as upload:
        await interaction.edit_original_response(attachments=[upload], **message_options(payload or {}))
    return True

def resolve_lang(q):
    if not q:
        return 'en', 'English'
    cleaned = q.strip().lower()
    code = LANG_ALIASES.get(cleaned, cleaned)
    for k, v in LANG_ALIASES.items():
        if v == code and len(k) > 2:
            return code, k.capitalize()
    return code, code.upper()

def do_translate(text, target_code='en', source_code='auto'):
    tgt_code, tgt_name = resolve_lang(target_code)
    src_code, src_name = resolve_lang(source_code) if source_code != 'auto' else ('auto', 'auto')

    try:
        translated = groq_engine.groq_translate(text, tgt_name, src_name)
        if translated and not translated.startswith("[Translation Error"):
            return translated
    except Exception as e:
        print(f"[Warn] Groq translation error: {e}")

    try:
        translated = GoogleTranslator(source=source_code, target=target_code).translate(text)
        if translated and not translated.startswith("Error"):
            return translated
    except Exception:
        pass
    
    return text

async def modify_role(user_id, role_id, action='PUT'):
    url = f'/guilds/{GUILD_ID}/members/{user_id}/roles/{role_id}'
    try:
        await api_call(url, method=action)
        print(f"[{action}] Role {role_id} for user {user_id}")
    except Exception as e:
        print(f"Error {action} role {role_id} for user {user_id}: {e}")

async def get_channel_webhook(channel_id):
    if channel_id in _webhook_cache:
        return _webhook_cache[channel_id]

    try:
        webhooks = await api_call(f'/channels/{channel_id}/webhooks')
        for w in webhooks:
            if w.get('name') == 'RippleProxy' and w.get('token'):
                _webhook_cache[channel_id] = (w['id'], w['token'])
                return _webhook_cache[channel_id]

        new_wh = await api_call(f'/channels/{channel_id}/webhooks', method='POST', data={'name': 'RippleProxy'})
        _webhook_cache[channel_id] = (new_wh['id'], new_wh['token'])
        return _webhook_cache[channel_id]
    except Exception as e:
        print(f"Error retrieving/creating webhook for channel {channel_id}: {e}")
        return None



async def interaction_callback(i_id, i_token, payload, max_retries=3):
    interaction = _interactions[i_token]
    if interaction.response.is_done():
        return True
    data = payload.get('data', {})
    if payload['type'] == 5:
        await interaction.response.defer(thinking=True, ephemeral=bool(data.get('flags', 0) & 64))
    else:
        await interaction.response.send_message(ephemeral=bool(data.get('flags', 0) & 64), **message_options(data))
    return True

async def interaction_edit_original(i_token, payload, max_retries=3):
    await _interactions[i_token].edit_original_response(**message_options(payload))
    return True

MUSIC_SLASH_COMMANDS = [
    {
        "name": "play",
        "description": "Play or queue a song from Spotify, YouTube, SoundCloud, or search query",
        "options": [
            {
                "type": 3,
                "name": "query",
                "description": "Song name or Spotify/YouTube/SoundCloud link",
                "required": True
            }
        ]
    },
    {"name": "skip", "description": "Skip the currently playing song"},
    {"name": "pause", "description": "Pause music playback"},
    {"name": "resume", "description": "Resume music playback"},
    {"name": "stop", "description": "Stop playback and clear the music queue"},
    {"name": "queue", "description": "View upcoming songs in the queue"},
    {"name": "nowplaying", "description": "Show the currently playing song"},
    {
        "name": "volume",
        "description": "Adjust music playback volume (1-100%)",
        "options": [
            {
                "type": 4,
                "name": "percent",
                "description": "Volume percentage (1-100)",
                "required": True
            }
        ]
    },
    {
        "name": "join",
        "description": "Join your current voice channel or a specified channel",
        "options": [
            {
                "type": 3,
                "name": "channel",
                "description": "Voice channel name or ID (leave blank to join your current VC)",
                "required": False
            }
        ]
    },
    {
        "name": "search",
        "description": "Search and choose from top 5 song results",
        "options": [
            {
                "type": 3,
                "name": "query",
                "description": "Song name or keywords to search",
                "required": True
            }
        ]
    },
    {"name": "loop", "description": "Toggle looping the current song"},
    {"name": "shuffle", "description": "Shuffle the upcoming queue"},
    {
        "name": "remove",
        "description": "Remove a song from the queue by position",
        "options": [
            {
                "type": 4,
                "name": "position",
                "description": "Queue position to remove (2+; 1 is playing)",
                "required": True
            }
        ]
    },
    {
        "name": "playtop",
        "description": "Queue a song to play next (skips the line)",
        "options": [
            {
                "type": 3,
                "name": "query",
                "description": "Song name or Spotify/YouTube/SoundCloud link",
                "required": True
            }
        ]
    },
    {
        "name": "leave",
        "description": "Disconnect bot from voice channel"
    },
]

recent_searches: dict[str, list[music_player.MusicTrack]] = {}

# In-flight play/search requests, per user (debounce so repeated requests
# don't stack up parallel yt-dlp searches).
_music_busy: set[str] = set()
MUSIC_SEARCH_TIMEOUT = 75  # seconds; covers the full fallback cascade (yt -> sc -> piped)
# (yt-dlp's socket_timeout only caps individual socket reads, not a whole
# stalled extraction - this wait_for is what actually bounds /play)


async def execute_music_command_guarded(command: str, query: str, author_id: str, author_name: str, channel_id: str) -> dict:
    """Runs a music command with debouncing and a hard timeout.

    - One in-flight search per user: hammering !play gets an instant reply
      instead of silently stacking yt-dlp searches.
    - Bounded wait: a hung search can no longer leave a /play interaction
      stuck on "Ripple Bot is thinking..." forever.
    """
    searchable = command in ('play', 'playtop', 'search', 'find')
    if searchable:
        if author_id in _music_busy:
            return {'content': '⏳ Still grabbing your last song — one at a time!'}
        _music_busy.add(author_id)
    try:
        if searchable:
            return await asyncio.wait_for(
                execute_music_command(command, query, author_id, author_name, channel_id),
                timeout=MUSIC_SEARCH_TIMEOUT,
            )
        return await execute_music_command(command, query, author_id, author_name, channel_id)
    except asyncio.TimeoutError:
        logging.warning('Music command %r timed out after %ss', command, MUSIC_SEARCH_TIMEOUT)
        return {'content': '⏱️ The song search timed out. Try again, or paste a direct YouTube/SoundCloud link.'}
    finally:
        if searchable:
            _music_busy.discard(author_id)


class MusicSelectionView(discord.ui.View):
    def __init__(self, track_count: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        for i in range(min(track_count, 5)):
            btn = discord.ui.Button(
                label=f"Play #{i+1}",
                style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
                custom_id=f"mselect:{i}"
            )
            self.add_item(btn)


async def play_resolved_track(track: music_player.MusicTrack, author_id: str, author_name: str, channel_id: str, guild=None, front: bool = False) -> dict:
    if not guild:
        guild = client.get_guild(int(GUILD_ID))
    channel = client.get_channel(int(channel_id)) if channel_id else None
    if not channel and channel_id:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception:
            pass
    if channel and getattr(channel, 'guild', None):
        guild = channel.guild

    queue = music_player.get_queue(str(guild.id if guild else GUILD_ID), client)

    member = guild.get_member(int(author_id)) if guild and author_id else None
    if not member and guild and author_id:
        try:
            member = await guild.fetch_member(int(author_id))
        except Exception:
            pass

    user_vc = getattr(member, 'voice', None).channel if (member and getattr(member, 'voice', None)) else None

    vc = (recorder.vc if recorder and getattr(recorder, 'vc', None) and recorder.vc.is_connected() else None)
    if not vc:
        vc = discord.utils.get(client.voice_clients, guild=guild)

    if not vc:
        if not user_vc:
            return {'content': '⚠️ You must join a voice channel first so I know where to play!'}
        from discord.ext import voice_recv
        try:
            vc = await user_vc.connect(cls=voice_recv.VoiceRecvClient, timeout=20, reconnect=True, self_deaf=False)
        except Exception as e:
            logging.error('Failed to connect to voice channel: %s', e)
            return {'content': f'⚠️ Could not connect to voice channel: {e}'}
    elif user_vc and getattr(vc, 'channel', None) and vc.channel.id != user_vc.id and not queue.is_playing():
        try:
            await vc.move_to(user_vc)
        except Exception as e:
            logging.error('Failed moving to user voice channel: %s', e)

    pos = await queue.enqueue(track, vc, channel_to_notify=channel, front=front)
    if pos == 1:
        return {'content': f'🎶 **Now Playing:** **[{track.title}]({track.source_url})** by **{track.artist}** ({track.format_duration()})'}
    else:
        return {'content': f'✅ Added to queue at **#{pos}**: **[{track.title}]({track.source_url})** by **{track.artist}** ({track.format_duration()})'}


async def handle_music_button(interaction: discord.Interaction, custom_id: str):
    await interaction.response.defer(ephemeral=False)
    channel_id = str(interaction.channel_id)
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name or interaction.user.name

    try:
        idx = int(custom_id.split(':')[1])
    except (IndexError, ValueError):
        await interaction.followup.send("⚠️ Invalid track selection.", ephemeral=True)
        return

    tracks = recent_searches.get(f"{channel_id}:{user_id}") or recent_searches.get(channel_id)
    if not tracks or idx >= len(tracks):
        await interaction.followup.send("⚠️ Search session expired or track not available. Run `/search` or `!search` again.", ephemeral=True)
        return

    track = tracks[idx]
    result = await play_resolved_track(track, user_id, user_name, channel_id, interaction.guild)
    await interaction.followup.send(**message_options(result))


async def execute_music_command(command: str, query: str, author_id: str, author_name: str, channel_id: str) -> dict:
    guild = client.get_guild(int(GUILD_ID))
    channel = client.get_channel(int(channel_id)) if channel_id else None
    if not channel and channel_id:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception:
            pass
    if channel and getattr(channel, 'guild', None):
        guild = channel.guild

    queue = music_player.get_queue(str(guild.id if guild else GUILD_ID), client)

    if command in ('play', 'playtop'):
        query = (query or '').strip()
        if not query:
            return {'content': '⚠️ Please provide a song name or Spotify/YouTube/SoundCloud URL.\nExample: `!play lofi beats` or `/play <spotify-url>`'}

        # Quick pick from recent search if query is digit 1-5 (instant; the
        # voice check inside play_resolved_track still applies)
        if query.isdigit() and 1 <= int(query) <= 5:
            cached = recent_searches.get(f"{channel_id}:{author_id}") or recent_searches.get(channel_id)
            if cached and int(query) <= len(cached):
                track = cached[int(query) - 1]
                return await play_resolved_track(track, author_id, author_name, channel_id, guild, front=(command == 'playtop'))

        # Fast fallback: check voice BEFORE searching, so someone who is not
        # in a voice channel gets an instant error instead of waiting on a
        # song search that can never play.
        member = guild.get_member(int(author_id)) if guild and author_id else None
        user_vc = getattr(member.voice, 'channel', None) if (member and getattr(member, 'voice', None)) else None
        bot_vc = (recorder.vc if recorder and getattr(recorder, 'vc', None) and recorder.vc.is_connected() else None) \
            or discord.utils.get(client.voice_clients, guild=guild)
        if not user_vc and not (bot_vc and bot_vc.is_connected()):
            return {'content': '⚠️ Join a voice channel first, then run `/play` again — I play wherever you are!'}

        tracks = await music_player.search_tracks(query, author_name, limit=5)
        if not tracks:
            return {'content': '⚠️ Could not find or stream that audio track. Try another song or URL.'}

        recent_searches[f"{channel_id}:{author_id}"] = tracks
        recent_searches[channel_id] = tracks

        res = await play_resolved_track(tracks[0], author_id, author_name, channel_id, guild, front=(command == 'playtop'))
        if len(tracks) > 1 and not query.startswith(('http://', 'https://')):
            res['view'] = MusicSelectionView(len(tracks))
            res['content'] = res.get('content', '') + f"\n💡 *Found {len(tracks)} versions. Click a button below or type `!play 1-{len(tracks)}` to switch/queue alternatives.*"
        return res

    elif command in ('search', 'find'):
        query = (query or '').strip()
        if not query:
            return {'content': '⚠️ Please provide a song name to search.\nExample: `/search yeat big tonka` or `!search lofi`'}

        tracks = await music_player.search_tracks(query, author_name, limit=5)
        if not tracks:
            return {'content': f'⚠️ Could not find any songs matching **{query}**.'}

        recent_searches[f"{channel_id}:{author_id}"] = tracks
        recent_searches[channel_id] = tracks

        embed = discord.Embed(
            title=f"🔍 Song Results for: {query[:50]}",
            description="Click a button below or type `!play 1-5` to play a track.",
            color=discord.Color.blue()
        )
        for i, t in enumerate(tracks):
            embed.add_field(
                name=f"{i+1}. {t.title[:60]}",
                value=f"Artist: **{t.artist[:40]}** | Duration: `{t.format_duration()}` | [Link]({t.source_url})",
                inline=False
            )
        if tracks[0].thumbnail:
            embed.set_thumbnail(url=tracks[0].thumbnail)

        view = MusicSelectionView(len(tracks))
        return {'embeds': [embed.to_dict()], 'view': view}

    elif command in ('skip', 'next'):
        if queue.skip():
            return {'content': '⏭️ Skipped current track!'}
        return {'content': '⚠️ Nothing is currently playing.'}

    elif command == 'pause':
        if queue.pause():
            return {'content': '⏸️ Paused playback. Use `!resume` or `/resume` to continue.'}
        return {'content': '⚠️ Nothing is playing to pause.'}

    elif command == 'resume':
        if queue.resume():
            return {'content': '▶️ Resumed playback.'}
        return {'content': '⚠️ Playback is not paused.'}

    elif command == 'stop':
        queue.stop()
        return {'content': '⏹️ Stopped playback and cleared the queue.'}

    elif command in ('queue', 'q'):
        if not queue.now_playing and not queue.queue:
            return {'content': '📭 The queue is currently empty. Use `!play <song>` or `/play <song>` to add one!'}

        embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blue())
        if queue.now_playing:
            embed.add_field(
                name="🔊 Now Playing",
                value=f"**[{queue.now_playing.title}]({queue.now_playing.source_url})** by **{queue.now_playing.artist}** ({queue.now_playing.format_duration()}) [Req: {queue.now_playing.requester}]",
                inline=False
            )

        if queue.queue:
            upcoming_lines = []
            for idx, t in enumerate(queue.queue[:10], start=1):
                upcoming_lines.append(f"`{idx}.` **[{t.title}]({t.source_url})** by **{t.artist}** ({t.format_duration()}) [Req: {t.requester}]")

            more = len(queue.queue) - 10
            if more > 0:
                upcoming_lines.append(f"*... and {more} more track(s)*")
            embed.add_field(name="📋 Up Next", value="\n".join(upcoming_lines), inline=False)

        embed.set_footer(text=f"Total in queue: {len(queue.queue) + (1 if queue.now_playing else 0)}")
        return {'embeds': [embed.to_dict()]}

    elif command in ('nowplaying', 'np'):
        track = queue.now_playing
        if not track:
            return {'content': '⚠️ Nothing is currently playing.'}
        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"**[{track.title}]({track.source_url})**\nby **{track.artist}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Duration", value=track.format_duration(), inline=True)
        embed.add_field(name="Requested By", value=track.requester, inline=True)
        embed.add_field(name="Volume", value=f"{int(queue.volume * 100)}%", inline=True)
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        return {'embeds': [embed.to_dict()]}

    elif command in ('volume', 'vol'):
        query = (query or '').strip()
        if not query:
            return {'content': f'🔊 Current volume is **{int(queue.volume * 100)}%**.'}
        try:
            vol_val = int(query.rstrip('%'))
            vol = queue.set_volume(vol_val)
            return {'content': f'🔊 Volume set to **{int(vol * 100)}%**.'}
        except ValueError:
            return {'content': '⚠️ Invalid volume. Provide a number between 1 and 100.'}

    elif command in ('join', 'summon'):
        member = guild.get_member(int(author_id)) if guild and author_id else None
        if not member and guild and author_id:
            try:
                member = await guild.fetch_member(int(author_id))
            except Exception:
                pass
        user_vc = getattr(member, 'voice', None).channel if (member and getattr(member, 'voice', None)) else None

        target_vc = None
        if query:
            q_clean = query.strip().strip('<#>').strip()
            if q_clean.isdigit():
                target_vc = client.get_channel(int(q_clean))
                if not target_vc and guild:
                    try:
                        target_vc = await client.fetch_channel(int(q_clean))
                    except Exception:
                        pass
            if not target_vc and guild:
                for v in guild.voice_channels:
                    if v.name.lower() == query.strip().lower():
                        target_vc = v
                        break

        if not target_vc:
            target_vc = user_vc

        if not target_vc:
            return {'content': '⚠️ Join a voice channel first, or specify a channel name/ID (e.g. `/join General` or `!join <#id>`).'}

        from discord.ext import voice_recv
        vc = (recorder.vc if recorder and getattr(recorder, 'vc', None) and recorder.vc.is_connected() else None)
        if not vc:
            vc = discord.utils.get(client.voice_clients, guild=guild)

        try:
            if vc and vc.is_connected():
                if getattr(vc, 'channel', None) and vc.channel.id != target_vc.id:
                    await vc.move_to(target_vc)
            else:
                vc = await target_vc.connect(cls=voice_recv.VoiceRecvClient, timeout=20, reconnect=True, self_deaf=False)
            return {'content': f'🔊 Joined **{target_vc.name}**!'}
        except Exception as e:
            return {'content': f'⚠️ Could not connect to **{target_vc.name}**: {e}'}

    elif command == 'loop':
        queue.loop = not queue.loop
        return {'content': f'🔁 Loop is now **{"ON" if queue.loop else "OFF"}**.'}

    elif command == 'shuffle':
        count = queue.shuffle()
        return {'content': f'🔀 Shuffled **{count}** upcoming track(s).' if count else '📭 The queue is empty.'}

    elif command == 'remove':
        if not (query or '').strip().isdigit():
            return {'content': '⚠️ Provide a queue position, e.g. `!remove 2`.'}
        removed = queue.remove(int(query.strip()))
        if removed:
            return {'content': f"🗑️ Removed **{removed.title}** from the queue."}
        return {'content': '⚠️ No track at that queue position.'}

    elif command in ('leave', 'disconnect', 'dc'):
        vc = (recorder.vc if recorder and getattr(recorder, 'vc', None) and recorder.vc.is_connected() else None)
        if not vc:
            vc = discord.utils.get(client.voice_clients, guild=guild)

        if vc and vc.is_connected():
            queue.stop()
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            return {'content': '👋 Left voice channel.'}
        return {'content': '⚠️ Bot is not currently in any voice channel.'}

    return {'content': '⚠️ Unknown music command.'}


async def handle_interaction(d):
    try:
        i_id = d['id']
        i_token = d['token']
        channel_id = d.get('channel_id')
        data = d.get('data', {})
        cname = data.get('name')
        member = d.get('member') or {}
        user = member.get('user') or d.get('user') or {}
        user_name = member.get('nick') or user.get('global_name') or user.get('username') or 'User'
        user_id = user.get('id', '')

        user_avatar = None
        if user.get('avatar'):
            user_avatar = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=256"
        elif user.get('id'):
            user_avatar = f"https://cdn.discordapp.com/embed/avatars/{(int(user['id']) >> 22) % 6}.png"

        # 1. /purge command
        if cname == 'purge':
            if not _interactions[i_token].permissions.manage_messages:
                await interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': 'Manage Messages permission required.', 'flags': 64}})
                return
            ok = await interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            if not ok:
                return

            p_ok, p_msg = rate_limiter.check_purge_cooldown(channel_id)
            if not p_ok:
                await interaction_edit_original(i_token, {'content': p_msg})
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            amount = min(max(int(options.get('amount', 5)), 1), 100)

            try:
                msgs = await api_call(f'/channels/{channel_id}/messages?limit={amount}')
                if msgs:
                    msg_ids = [m['id'] for m in msgs]
                    if len(msg_ids) == 1:
                        await api_call(f'/channels/{channel_id}/messages/{msg_ids[0]}', method='DELETE')
                    else:
                        await api_call(f'/channels/{channel_id}/messages/bulk-delete', method='POST', data={'messages': msg_ids})
                    await interaction_edit_original(i_token, {'content': f"🧹 Purged **{len(msg_ids)}** messages from this channel!"})
                else:
                    await interaction_edit_original(i_token, {'content': "No messages found to purge."})
            except Exception as e:
                await interaction_edit_original(i_token, {'content': f"⚠️ Error purging messages: {e}"})
            return

        # 2. /languages
        if cname == 'languages':
            lang_text = (
                "🌐 **Popular Languages**\n"
                "`en` English • `es` Spanish • `fr` French • `de` German • `ja` Japanese\n"
                "`zh-CN` Chinese • `it` Italian • `pt` Portuguese • `ru` Russian • `ko` Korean\n"
                "`ar` Arabic • `hi` Hindi • `nl` Dutch • `tr` Turkish • `vi` Vietnamese\n\n"
                "*Use with `/translate`, `/speak`, or reply to any message with `to spanish` or `!tr`.*"
            )
            await interaction_callback(i_id, i_token, {
                'type': 4,
                'data': {
                    'embeds': [{
                        'title': 'RippleBot Language Guide',
                        'description': lang_text,
                        'color': 0x0ea5e9
                    }]
                }
            })
            return

        # 3. /imagine or /image
        if cname in ('imagine', 'image'):
            allowed, limit_msg = rate_limiter.check_rate_limit(user_id)
            if not allowed:
                await interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': limit_msg, 'flags': 64}})
                return

            ok = await interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            prompt = options.get('prompt', '').strip()

            loop = asyncio.get_event_loop()
            try:
                img_bytes, clean_p, model, enhanced_p = await loop.run_in_executor(None, image_gen.fetch_generated_image, prompt)
                payload = {
                    'embeds': [{
                        'title': '🎨 AI Generated Image',
                        'description': f"**Prompt:** {clean_p}\n*✨ Enhanced: {enhanced_p}*",
                        'image': {'url': 'attachment://generated.jpg'},
                        'color': 0x0ea5e9,
                        'footer': {'text': f'Requested by {user_name} • {model.upper()} Model'}
                    }]
                }
                await interaction_edit_original_file(i_token, img_bytes, filename="generated.jpg", payload=payload)
            except Exception as e:
                await interaction_edit_original(i_token, {'content': f"⚠️ Failed to generate image: {e}"})
            return

        # Rate limit check for text/AI commands
        if cname in ('translate', 'speak', 'Translate to English', 'ask'):
            allowed, limit_msg = rate_limiter.check_rate_limit(user_id)
            if not allowed:
                await interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': limit_msg, 'flags': 64}})
                return

        # 4. /translate
        if cname == 'translate':
            ok = await interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            text_to_translate = options.get('text', '').strip()
            target_input = options.get('to', 'en')
            source_input = options.get('from', 'auto')

            if not text_to_translate:
                await interaction_edit_original(i_token, {'content': "Provide text to translate: `/translate text: <text> to: <lang>`"})
                return

            tgt_code, tgt_name = resolve_lang(target_input)
            src_code, src_name = resolve_lang(source_input) if source_input != 'auto' else ('auto', 'Auto-detect')

            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(None, do_translate, text_to_translate, tgt_code, src_code)

            embed = {
                'title': f'🌐 Translation (➔ {tgt_name})',
                'color': 0x0ea5e9,
                'fields': [
                    {'name': 'Original', 'value': text_to_translate[:1024], 'inline': False},
                    {'name': f'Translation ({tgt_name})', 'value': translated[:1024], 'inline': False}
                ],
                'footer': {'text': f'Requested by {user_name}'}
            }
            await interaction_edit_original(i_token, {'embeds': [embed]})
            return

        # 5. /speak
        if cname == 'speak':
            ok = await interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            lang_input = options.get('language', 'es')
            raw_message = options.get('message', '')

            tgt_code, tgt_name = resolve_lang(lang_input)
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(None, do_translate, raw_message, tgt_code, 'auto')

            wh_info = await get_channel_webhook(channel_id)
            if wh_info:
                wh_id, wh_token = wh_info
                webhook = discord.Webhook.partial(int(wh_id), wh_token, client=client)
                await webhook.send(translated, username=f"{user_name} ({tgt_name})", avatar_url=user_avatar, allowed_mentions=discord.AllowedMentions.none())

                await interaction_edit_original(i_token, {'content': f"✅ Sent in **{tgt_name}**!"})
            else:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f"**{user_name}** ({tgt_name}): {translated}"
                })
                await interaction_edit_original(i_token, {'content': '✅ Sent!'})
            return

        # 6. Translate to English (Context Menu)
        if cname == 'Translate to English':
            ok = await interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            resolved = data.get('resolved', {})
            messages = resolved.get('messages', {})
            target_id = data.get('target_id')
            target_msg = messages.get(target_id, {})
            text_to_translate = target_msg.get('content', '')
            author_name = target_msg.get('author', {}).get('username', 'Original Author')

            if not text_to_translate:
                await interaction_edit_original(i_token, {'content': "No text found to translate."})
                return

            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(None, do_translate, text_to_translate, 'en', 'auto')

            embed = {
                'title': '🌐 Translated to English',
                'color': 0x0ea5e9,
                'fields': [
                    {'name': f'Original (by {author_name})', 'value': text_to_translate[:1024], 'inline': False},
                    {'name': 'English', 'value': translated[:1024], 'inline': False}
                ],
                'footer': {'text': f'Requested by {user_name}'}
            }
            await interaction_edit_original(i_token, {'embeds': [embed]})
            return

        # 7. /ask
        if cname == 'ask':
            ok = await interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            question = options.get('question', '').strip()

            loop = asyncio.get_event_loop()
            history = chat_memory.build_history(channel_id)
            answer = await loop.run_in_executor(None, groq_engine.groq_water_chat, question, user_name, history)

            try:
                chat_memory.record(channel_id, i_id, user_name, question)
                chat_memory.record(channel_id, f"{i_id}-bot", 'RippleBot', answer, is_bot=True)
            except Exception:
                pass

            chunks = split_discord_chunks(answer, max_len=1900)
            if chunks:
                await interaction_edit_original(i_token, {'content': chunks[0]})
                for ch in chunks[1:]:
                    await api_call(f'/channels/{channel_id}/messages', method='POST', data={'content': ch})
            return

        # 8. /meeting
        if cname == 'meeting':
            await interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            interaction = _interactions[i_token]
            if not await can_use_meeting(interaction.user, channel_id):
                await interaction_edit_original(i_token, {'content': 'Use meeting commands inside the Founders channels or while in a voice channel.'})
                return
            options = data.get('options', [])
            subcmd = options[0].get('name') if options else 'status'
            target_vc = getattr(interaction.user, 'voice', None).channel if getattr(interaction.user, 'voice', None) else None
            result = await meeting_command(subcmd, user_name, target_vc)
            await interaction_edit_original(i_token, result)
            return

        # 9. /setup-roles: one-shot team role setup, guild owner only
        if cname == 'setup-roles':
            await interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            report = await role_setup.setup_roles(api_call, GUILD_ID, requester_id=str(user_id))
            await interaction_edit_original(i_token, {'content': report})
            return

        # 10. Extras slash commands: moderation, leveling, reminders, fun, info
        if cname in {c['name'] for c in extras.SLASH_COMMANDS}:
            await interaction_callback(i_id, i_token, {'type': 5})
            raw_options = data.get('options', [])
            sub = ''
            raw_opts = raw_options[0].get('options') if raw_options else None
            if raw_opts:
                sub = raw_options[0]['name']
                options = {o['name']: o['value'] for o in raw_opts if o.get('value') is not None}
            else:
                options = {opt['name']: opt['value'] for opt in raw_options if opt.get('value') is not None}
            options['_sub'] = sub
            result = await extras.handle_slash(_interactions[i_token], cname, options)
            if result:
                await interaction_edit_original(i_token, result)
            return

        # 10. Music / Voice slash commands: /play, /skip, /pause, /resume, /stop, /queue, /nowplaying, /volume, /join, /leave, /search, /loop, /shuffle, /remove, /playtop
        if cname in ('play', 'playtop', 'skip', 'pause', 'resume', 'stop', 'queue', 'nowplaying', 'volume', 'join', 'leave', 'search', 'find', 'loop', 'shuffle', 'remove'):
            await interaction_callback(i_id, i_token, {'type': 5})
            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            query = options.get('query') or options.get('percent') or options.get('channel') or options.get('position') or ''
            result = await execute_music_command_guarded(cname, str(query), user_id, user_name, channel_id)
            await interaction_edit_original(i_token, result)
            return

    except Exception as e:
        logging.error('Interaction %s failed: %s', d.get('data', {}).get('name'), type(e).__name__)
        interaction = _interactions.get(d.get('token'))
        if interaction and interaction.response.is_done():
            try:
                await interaction.edit_original_response(content='Command failed. Check bot permissions and service logs, then retry.')
            except discord.HTTPException:
                pass

def split_discord_chunks(text: str, max_len: int = 1900) -> list[str]:
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    in_codeblock = False
    codeblock_syntax = ""

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_codeblock = not in_codeblock
            if in_codeblock:
                codeblock_syntax = line.strip()[3:]

        if len(current) + len(line) + 1 > max_len:
            if in_codeblock:
                current += "\n```"
                chunks.append(current.strip())
                current = f"```{codeblock_syntax}\n{line}\n"
            else:
                chunks.append(current.strip())
                current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        chunks.append(current.strip())
    return chunks

async def send_discord_reply(channel_id: str, content: str, reply_to_id: str = None):
    chunks = split_discord_chunks(content, max_len=1900)
    for i, chunk in enumerate(chunks):
        data = {'content': chunk}
        if i == 0 and reply_to_id:
            data['message_reference'] = {'message_id': reply_to_id}
        await api_call(f'/channels/{channel_id}/messages', method='POST', data=data)

def extract_media_from_message(msg: dict) -> str:
    if not msg or not isinstance(msg, dict):
        return None
    # 1. Check attachments
    for att in msg.get('attachments') or []:
        ctype = att.get('content_type', '')
        fname = att.get('filename', '').lower()
        if ctype.startswith('image/') or fname.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
            return att.get('url')
    # 2. Check embeds (GIF picker, Tenor, Klipy, Giphy, web previews)
    for emb in msg.get('embeds') or []:
        if emb.get('thumbnail') and emb['thumbnail'].get('url'):
            return emb['thumbnail']['url']
        if emb.get('image') and emb['image'].get('url'):
            return emb['image']['url']
    # 3. Check direct URLs in content
    content = msg.get('content') or ''
    for u in re.findall(r'https?://[^\s<>"]+', content):
        u_low = u.split('?')[0].lower()
        if u_low.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
            return u
        if any(dom in u.lower() for dom in ['static.klipy.com', 'media.tenor.com', 'media.giphy.com', 'i.giphy.com', 'i.imgur.com']):
            return u
    return None

async def handle_message(d):
    try:
        author = d.get('author', {})
        if author.get('id') == BOT_ID or author.get('bot'):
            return

        content = (d.get('content') or '').strip()
        channel_id = d.get('channel_id')
        msg_id = d.get('id')
        author_id = author.get('id', '')
        author_name = author.get('global_name') or author.get('username') or 'Founder'

        if content.startswith(('!', '?')):
            logging.info('Received prefix command %r in channel %s from %s', content, channel_id, author_name)

        # AutoMod: delete and warn rule-breaking messages before anything else.
        if await extras.run_automod(d):
            return

        # XP leveling (MEE6-style, 60s cooldown per user).
        leveled = extras.award_xp(author_id, author_name)
        if leveled:
            try:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f'🎉 <@{author_id}> just reached level **{leveled}**!',
                    'allowed_mentions': {'parse': ['users']},
                    'message_reference': {'message_id': msg_id},
                })
            except Exception:
                pass

        # Conversation memory: capture casual chat so @mentions build on context.
        if not content.startswith(('!', '?', '/')):
            try:
                chat_memory.record(channel_id, msg_id, author_name, content)
            except Exception:
                pass

        # Extras prefix commands: !warn, !rank, !remind, !8ball, !poll, ...
        if re.match(r'^[!?]\w', content):
            channel = client.get_channel(int(channel_id))
            guild = getattr(channel, 'guild', None) or client.get_guild(int(GUILD_ID))
            member = guild.get_member(int(author_id)) if (guild and author_id) else None
            result = await extras.handle_prefix(content, author_id, author_name, channel_id, msg_id, member, channel)
            if result:
                if result.get('__poll__'):
                    question = result['__poll__']
                    if channel:
                        poll_msg = await channel.send(f'📊 **{question}**\n*by {author_name}*')
                        for emoji in ('👍', '👎', '🤷'):
                            await poll_msg.add_reaction(emoji)
                    return
                await api_call(f'/channels/{channel_id}/messages', method='POST',
                               data={**result, 'message_reference': {'message_id': msg_id}})
                return

        # Meeting commands share the same serialized lifecycle as slash commands.
        match = re.fullmatch(r'(?:!meeting|<@!?1546333781764345936>\s*meeting)(?:\s+(start|join|end|stop|status|stats|leaderboard))?|!(startmeeting|endmeeting)|!note\s+(.+)', content, re.IGNORECASE | re.DOTALL)
        if match:
            channel = client.get_channel(int(channel_id))
            if not channel and channel_id:
                try:
                    channel = await client.fetch_channel(int(channel_id))
                except Exception:
                    pass
            guild = getattr(channel, 'guild', None) or client.get_guild(int(GUILD_ID))
            member = guild.get_member(int(author_id)) if guild else None
            if not member and guild and author_id:
                try:
                    member = await guild.fetch_member(int(author_id))
                except Exception:
                    pass
            if not await can_use_meeting(member, channel_id):
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={'content': 'Use meeting commands inside the Founders channels.'})
                return
            if match.group(3):
                async with _meeting_lock:
                    if tracker.is_active:
                        tracker.add_transcript(author_name, match.group(3))
                        result = {'content': 'Note added to the meeting.'}
                    else:
                        result = {'content': 'No active meeting. Use /meeting start first.'}
            else:
                command = match.group(1) or {'startmeeting': 'start', 'endmeeting': 'end'}.get((match.group(2) or '').lower(), 'status')
                target_vc = getattr(member, 'voice', None).channel if (member and getattr(member, 'voice', None)) else None
                result = await meeting_command(command.lower(), author_name, target_vc)
            await api_call(f'/channels/{channel_id}/messages', method='POST', data={**result, 'message_reference': {'message_id': msg_id}})
            return

        # 0.1 Music / Voice text commands: !play, !skip, !pause, !resume, !stop, !queue, !np, !volume, !join, !leave, !search, !find
        m_music = re.match(r'^(?:!(play|p|search|find|skip|next|pause|resume|stop|queue|q|nowplaying|np|volume|vol|join|summon|leave|disconnect|dc)|<@!?1546333781764345936>\s*(play|search|find|skip|pause|resume|stop|queue|nowplaying|volume|join|leave))\b(?:\s+(.*))?$', content, re.IGNORECASE | re.DOTALL)
        if m_music:
            cmd = (m_music.group(1) or m_music.group(2) or '').lower()
            if cmd == 'p': cmd = 'play'
            elif cmd in ('next',): cmd = 'skip'
            elif cmd in ('q',): cmd = 'queue'
            elif cmd in ('np',): cmd = 'nowplaying'
            elif cmd in ('vol',): cmd = 'volume'
            elif cmd in ('summon',): cmd = 'join'
            elif cmd in ('dc', 'disconnect'): cmd = 'leave'
            q_arg = m_music.group(3) or ''
            try:
                result = await execute_music_command_guarded(cmd, q_arg, author_id, author_name, channel_id)
            except Exception as e:
                logging.error('Music text command %r failed: %s', cmd, e)
                result = {'content': '⚠️ Something went wrong with that music command. Try again in a moment.'}
            await api_call(f'/channels/{channel_id}/messages', method='POST', data={**result, 'message_reference': {'message_id': msg_id}})
            return

        # 0.1 In-meeting live discussion & voice notes capture
        if tracker.is_active and channel_id in (meeting_tracker.FOUNDERS_VC_ID, '1545542223381274634'):
            # Check for audio attachments / voice messages
            for att in d.get('attachments') or []:
                fname = (att.get('filename') or '').lower()
                ctype = att.get('content_type') or ''
                if any(ext in fname for ext in ('.ogg', '.wav', '.mp3', '.m4a')) or 'audio/' in ctype:
                    try:
                        meeting_id = tracker.start_time
                        a_bytes = await asyncio.to_thread(download_media, att['url'])
                        transcribed = await asyncio.to_thread(groq_engine.groq_transcribe_audio, a_bytes, fname)
                        if transcribed and tracker.is_active and tracker.start_time == meeting_id:
                            tracker.add_transcript(author_name, f"[Voice Note]: {transcribed}")
                            await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                                'content': f"🎙️ **Transcribed Voice Note ({author_name}):**\n\"{transcribed}\"",
                                'message_reference': {'message_id': msg_id}
                            })
                    except Exception as ea:
                        print(f"[MeetingTracker] Audio transcription error: {ea}")

            # Capture live in-meeting text messages (excluding commands)
            if content and not content.startswith(('!', '/', '?')):
                tracker.add_transcript(author_name, content)

        # 1. Purge text command: !purge <amount> or @RippleBot purge <amount>
        m_purge = re.match(r'^(?:!purge|<@!?1546333781764345936>\s*purge)\s*(\d+)?', content, re.IGNORECASE)
        if m_purge:
            channel = client.get_channel(int(channel_id))
            member = channel.guild.get_member(int(author_id)) if getattr(channel, 'guild', None) else None
            if member is None or not channel.permissions_for(member).manage_messages:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={'content': 'Manage Messages permission required.'})
                return
            p_ok, p_msg = rate_limiter.check_purge_cooldown(channel_id)
            if not p_ok:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': p_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            amount_str = m_purge.group(1)
            amount = min(max(int(amount_str) if amount_str else 5, 1), 100)

            try:
                await api_call(f'/channels/{channel_id}/messages/{msg_id}', method='DELETE')
            except Exception:
                pass

            msgs = await api_call(f'/channels/{channel_id}/messages?limit={amount}')
            if msgs:
                msg_ids = [m['id'] for m in msgs]
                if len(msg_ids) == 1:
                    await api_call(f'/channels/{channel_id}/messages/{msg_ids[0]}', method='DELETE')
                else:
                    await api_call(f'/channels/{channel_id}/messages/bulk-delete', method='POST', data={'messages': msg_ids})

                sent = await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f"🧹 Purged **{len(msg_ids)}** messages!"
                })
                if sent and sent.get('id'):
                    async def auto_delete(c_id, m_id):
                        await asyncio.sleep(3)
                        try:
                            await api_call(f'/channels/{c_id}/messages/{m_id}', method='DELETE')
                        except Exception:
                            pass
                    asyncio.create_task(auto_delete(channel_id, sent['id']))
            return

        # 2. Image generation command: !imagine <prompt>, !image <prompt>, !draw <prompt>, @RippleBot imagine/draw/generate image
        m_img = re.match(r'^(?:!(?:imagine|image|draw)|<@!?1546333781764345936>\s*(?:imagine|draw|image|generate\s+(?:an?\s+)?image(?:\s+of)?))\s+(.+)$', content, re.IGNORECASE | re.DOTALL)
        if m_img:
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': limit_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            prompt = m_img.group(1).strip()
            try:
                await api_call(f'/channels/{channel_id}/typing', method='POST')
            except Exception:
                pass

            loop = asyncio.get_event_loop()
            try:
                img_bytes, clean_p, model, enhanced_p = await loop.run_in_executor(None, image_gen.fetch_generated_image, prompt)
                payload = {
                    'embeds': [{
                        'title': '🎨 AI Generated Image',
                        'description': f"**Prompt:** {clean_p}\n*✨ Enhanced: {enhanced_p}*",
                        'image': {'url': 'attachment://generated.jpg'},
                        'color': 0x0ea5e9,
                        'footer': {'text': f'Requested by {author.get("username", "User")} • {model.upper()} Model'}
                    }],
                    'message_reference': {'message_id': msg_id}
                }
                await send_channel_file(channel_id, img_bytes, filename="generated.jpg", payload=payload)
            except Exception as e:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f"⚠️ Failed to generate image: {e}",
                    'message_reference': {'message_id': msg_id}
                })
            return

        ref = d.get('message_reference')
        ref_msg = d.get('referenced_message')
        mentions = [u.get('id') for u in d.get('mentions', [])]
        is_bot_mentioned = BOT_ID in mentions
        is_reply_to_bot = (ref_msg and ref_msg.get('author', {}).get('id') == BOT_ID)
        is_tr_cmd = content.lower().startswith(('!tr', '!translate', '?tr'))

        # 3. Reply Translation
        if ref:
            cleaned_cmd = re.sub(rf'<@!?{BOT_ID}>', '', content).strip()
            cleaned_lower = cleaned_cmd.lower()
            is_reply_translation = False
            target_lang = 'en'

            if is_tr_cmd or any(w in cleaned_lower for w in ['translate', 'tr ']) or cleaned_lower == 'tr':
                is_reply_translation = True
                cleaned_cleaned = re.sub(r'^[!?](?:tr|translate)\b', '', cleaned_cmd, flags=re.IGNORECASE).strip()
                cleaned_cleaned = re.sub(r'^(?:translate|tr)\b', '', cleaned_cleaned, flags=re.IGNORECASE).strip()
                tgt_match = re.match(r'^(?:to\s+)?([a-zA-Z\-]+)', cleaned_cleaned)
                if tgt_match and len(tgt_match.group(1)) <= 15:
                    target_lang = tgt_match.group(1)
            elif cleaned_lower.startswith('to ') or cleaned_lower in LANG_ALIASES:
                is_reply_translation = True
                tgt_match = re.match(r'^(?:to\s+)?([a-zA-Z\-]+)', cleaned_cmd, re.IGNORECASE)
                if tgt_match and len(tgt_match.group(1)) <= 15:
                    target_lang = tgt_match.group(1)
            elif is_bot_mentioned and not cleaned_cmd:
                is_reply_translation = True
                target_lang = 'en'

            if is_reply_translation:
                allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
                if not allowed:
                    await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                        'content': limit_msg,
                        'message_reference': {'message_id': msg_id}
                    })
                    return

                target_text = ""
                ref_author = "User"
                if ref_msg and ref_msg.get('content'):
                    target_text = ref_msg['content']
                    ref_author = ref_msg.get('author', {}).get('username', 'User')
                else:
                    ref_id = ref.get('message_id')
                    if ref_id:
                        m = await api_call(f'/channels/{channel_id}/messages/{ref_id}')
                        target_text = m.get('content', '')
                        ref_author = m.get('author', {}).get('username', 'User')

                if not target_text:
                    await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                        'content': "💡 **Tip:** Right-click the message ➔ **Apps** ➔ **Translate to English**",
                        'message_reference': {'message_id': msg_id}
                    })
                    return

                tgt_code, tgt_name = resolve_lang(target_lang)
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(None, do_translate, target_text, tgt_code, 'auto')

                embed = {
                    'title': f'🌐 Translation (➔ {tgt_name})',
                    'color': 0x0ea5e9,
                    'fields': [
                        {'name': f'Original (by {ref_author})', 'value': target_text[:1024], 'inline': False},
                        {'name': f'Translation ({tgt_name})', 'value': translated[:1024], 'inline': False}
                    ],
                    'footer': {'text': f'Replied by {author.get("username", "User")}'}
                }

                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
                return

        # 4. Direct Translation Command
        if is_tr_cmd or (is_bot_mentioned and any(w in content.lower() for w in ['translate ', 'translate\n', 'tr '])):
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': limit_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            cleaned = re.sub(rf'<@!?{BOT_ID}>', '', content).strip()
            cleaned = re.sub(r'^[!?](?:tr|translate)\b', '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r'^(?:translate|tr)\b', '', cleaned, flags=re.IGNORECASE).strip()

            m_to = re.match(r'^(?:to\s+)?([a-zA-Z\-]{2,12})\s+(.+)$', cleaned, re.DOTALL | re.IGNORECASE)
            if m_to:
                lang_part, text_part = m_to.group(1), m_to.group(2)
                tgt_code, tgt_name = resolve_lang(lang_part)
            else:
                text_part = cleaned
                tgt_code, tgt_name = 'en', 'English'

            if text_part:
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(None, do_translate, text_part, tgt_code, 'auto')
                embed = {
                    'title': f'🌐 Translation (➔ {tgt_name})',
                    'color': 0x0ea5e9,
                    'fields': [
                        {'name': 'Original Text', 'value': text_part[:1024], 'inline': False},
                        {'name': f'Translation ({tgt_name})', 'value': translated[:1024], 'inline': False}
                    ],
                    'footer': {'text': f'Requested by {author.get("username", "User")}'}
                }
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
            return

        # 5. Vision & Conversational AI (@RippleBot or reply to RippleBot)
        if is_bot_mentioned or is_reply_to_bot:
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': limit_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            cleaned_prompt = re.sub(rf'<@!?{BOT_ID}>', '', content).strip()
            prompt_lower = cleaned_prompt.lower()

            image_url = extract_media_from_message(d)
            if not image_url and ref_msg:
                image_url = extract_media_from_message(ref_msg)
            if not image_url and ref:
                ref_id = ref.get('message_id')
                if ref_id:
                    m = await api_call(f'/channels/{channel_id}/messages/{ref_id}')
                    if isinstance(m, dict):
                        image_url = extract_media_from_message(m)

            # Only scan immediate previous message if current prompt clearly refers to media
            media_triggers = ['this', 'look', 'see', 'who is', 'what is this', 'what is that', 'pic', 'photo', 'gif', 'image', 'meme', 'view', 'read']
            if not image_url and channel_id and (any(t in prompt_lower for t in media_triggers) or len(cleaned_prompt.split()) <= 3):
                try:
                    recent = await api_call(f'/channels/{channel_id}/messages?limit=3')
                    if isinstance(recent, list):
                        for past_m in recent:
                            if past_m.get('id') != msg_id and past_m.get('author', {}).get('id') != BOT_ID:
                                found = extract_media_from_message(past_m)
                                if found:
                                    image_url = found
                                    break
                except Exception as e:
                    print(f"Channel history media scan error: {e}")

            try:
                await api_call(f'/channels/{channel_id}/typing', method='POST')
            except Exception:
                pass

            user_display = author.get('global_name') or author.get('username') or 'Friend'
            loop = asyncio.get_event_loop()

            # A. Vision flow
            if image_url:
                try:
                    img_bytes = await asyncio.to_thread(download_media, image_url)
                    data_url = await asyncio.to_thread(groq_engine.prepare_image_base64, img_bytes)
                    answer = await loop.run_in_executor(None, groq_engine.groq_vision_chat, cleaned_prompt, data_url, user_display)
                except Exception as e:
                    answer = f"⚠️ Couldn't process image/GIF: {e}"

                await send_discord_reply(channel_id, answer, reply_to_id=msg_id)
                return

            # B. Empty mention
            if not cleaned_prompt:
                await api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': "Yo! What's up? Ask me anything, generate images with `!imagine <prompt>`, or clean chat with `/purge`.",
                    'message_reference': {'message_id': msg_id}
                })
                return

            # C. Chat summarization
            if any(k in prompt_lower for k in ['summarize', 'summary', 'what i miss', 'what did i miss', 'recap', 'catch me up']):
                recent_msgs = await api_call(f'/channels/{channel_id}/messages?limit=25')
                chat_lines = []
                if recent_msgs:
                    for rm in reversed(recent_msgs):
                        r_author = rm.get('author', {}).get('username', 'User')
                        r_content = rm.get('content', '').strip()
                        r_id = rm.get('author', {}).get('id')
                        if r_id != BOT_ID and r_content and not r_content.startswith('!') and rm.get('id') != msg_id:
                            clean_c = re.sub(rf'<@!?{BOT_ID}>', '', r_content).strip()
                            if clean_c:
                                chat_lines.append(f"{r_author}: {clean_c}")

                transcript = "\n".join(chat_lines[-12:]) if chat_lines else "No recent messages."
                answer = await loop.run_in_executor(None, groq_engine.groq_summarize_chat, transcript, user_display)
            else:
                # Memory is thin (fresh restart or quiet channel): read the
                # messages above us so the conversation has context anyway.
                if chat_memory.message_count(channel_id) < 5:
                    try:
                        past = await api_call(f'/channels/{channel_id}/messages?limit=25')
                        if isinstance(past, list):
                            chat_memory.backfill(channel_id, past, bot_id=BOT_ID)
                    except Exception as e:
                        print(f"Chat memory backfill error: {e}")
                history = chat_memory.build_history(channel_id, exclude_message_id=msg_id)
                answer = await loop.run_in_executor(None, groq_engine.groq_water_chat, cleaned_prompt, user_display, history)

            if not answer:
                answer = "Hit a quick hiccup. Try asking again!"

            # Remember our own reply so follow-ups continue the conversation.
            try:
                chat_memory.record(channel_id, f"{msg_id}-bot", 'RippleBot', answer, is_bot=True)
            except Exception:
                pass

            await send_discord_reply(channel_id, answer, reply_to_id=msg_id)
            return

    except Exception as e:
        logging.error('Message handler failed: %s', type(e).__name__)
        try:
            await api_call(f"/channels/{d['channel_id']}/messages", method='POST', data={'content': 'Command failed. Check bot permissions and service logs, then retry.'})
        except discord.HTTPException:
            pass

async def start_health_server():
    from aiohttp import web
    async def health(request):
        ready = client.is_ready()
        return web.json_response({'ready': ready, 'revision': os.environ.get('RENDER_GIT_COMMIT', 'local'), 'meeting_active': tracker.is_active,
                                  'voice': recorder.status() if recorder else 'idle'}, status=200 if ready else 503)
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    base_port = int(os.environ.get('HEALTH_PORT', 8080 if str(os.environ.get('PORT')) == '7860' else os.environ.get('PORT', 8080)))
    for p in [base_port, base_port + 1, base_port + 2, 0]:
        try:
            site = web.TCPSite(runner, '0.0.0.0', p)
            await site.start()
            logging.info('Healthcheck listening on port %s', p)
            break
        except OSError:
            if p == 0:
                raise

def message_options(payload):
    result = {}
    for key in ('content',):
        if key in payload:
            result[key] = payload[key]
    if 'embeds' in payload:
        result['embeds'] = [discord.Embed.from_dict(embed) if isinstance(embed, dict) else embed for embed in payload['embeds']]
    if 'view' in payload:
        result['view'] = payload['view']
    result['allowed_mentions'] = discord.AllowedMentions.none()
    return result


def download_media(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {
        'cdn.discordapp.com', 'media.discordapp.net', 'media.tenor.com',
        'media.giphy.com', 'i.giphy.com', 'i.imgur.com', 'static.klipy.com'
    }:
        raise ValueError('Upload the media to Discord first')
    # A redirect must not turn an untrusted media URL into a local-network request.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'RippleBot/1.0'}), timeout=15) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Media exceeds 20 MB')
    return data


_interactions = {}
_meeting_lock = asyncio.Lock()
recorder = None


async def can_use_meeting(member, channel_id):
    if not member:
        return False
    channel = client.get_channel(int(channel_id)) if channel_id else None
    if not channel and channel_id:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception:
            pass
    guild = client.get_guild(int(GUILD_ID))
    if not isinstance(member, discord.Member):
        if guild and hasattr(member, 'id'):
            try:
                member = await guild.fetch_member(int(member.id))
            except Exception:
                return False
        else:
            return False
    if str(member.guild.id) != GUILD_ID:
        return False

    if channel and getattr(channel, 'category_id', None) == int(meeting_tracker.FOUNDERS_CATEGORY_ID):
        return True
    user_vc = getattr(member, 'voice', None).channel if getattr(member, 'voice', None) else None
    if user_vc and user_vc.permissions_for(member).connect:
        return True
    vc = client.get_channel(int(meeting_tracker.FOUNDERS_VC_ID))
    return bool(vc and vc.permissions_for(member).view_channel and vc.permissions_for(member).connect)


async def meeting_command(command, username, target_vc=None):
    global recorder
    async with _meeting_lock:
        if command in ('start', 'join'):
            if tracker.is_active:
                return {'content': f'A meeting is already active in <#{tracker.active_vc_id}>. Use /meeting status.'}
            channel = target_vc or client.get_channel(int(meeting_tracker.FOUNDERS_VC_ID))
            if channel is None:
                return {'content': 'Voice channel is unavailable. Check the bot’s View Channel and Connect permissions.'}
            members = [{'user_id': str(m.id), 'username': m.name, 'display_name': m.display_name}
                       for m in channel.members if not m.bot]
            if not members:
                return {'content': f'Join <#{channel.id}> before starting a meeting.'}
            if not groq_engine.get_groq_key():
                return {'content': 'GROQ_API_KEY is missing; transcription cannot start.'}
            tracker.start_meeting(username, members, vc_id=str(channel.id))
            recorder = MeetingRecorder(tracker)
            try:
                await recorder.start(channel)
            except Exception as exc:
                tracker.is_active = False
                recorder = None
                logging.error('Voice connection failed: %s', type(exc).__name__)
                return {'content': 'Voice connection failed; recording did not start. Check View Channel/Connect permissions and host UDP access.'}
            return {'content': f'🎙️ Meeting started in <#{channel.id}>. Attendance tracking and voice reception are active. /meeting status shows received audio and transcript counts.'}
        if command in ('end', 'stop'):
            return await finish_meeting(locked=True)
        if command in ('stats', 'leaderboard'):
            return {'embeds': [tracker.get_stats_embed()]}
        embed = tracker.get_status_embed()
        if recorder:
            embed.setdefault('fields', []).append({'name': 'Voice transcription', 'value': recorder.status(), 'inline': False})
        return {'embeds': [embed]}


async def finish_meeting(automatic=False, locked=False):
    global recorder
    if not locked:
        async with _meeting_lock:
            return await finish_meeting(automatic, locked=True)
    if not tracker.is_active:
        return {'content': 'No active meeting to end.'}
    audio_status = recorder.status() if recorder else 'No voice capture'
    if recorder:
        await recorder.stop()
        audio_status = recorder.status()
        recorder = None
    ok, embeds, summary = await asyncio.to_thread(tracker.end_meeting)
    if ok:
        embeds[0].setdefault('fields', []).append({'name': 'Audio capture', 'value': audio_status, 'inline': False})
        channel = client.get_channel(int(tracker.reports_channel_id)) or await client.fetch_channel(int(tracker.reports_channel_id))
        # Store cumulative stats in the existing private reports channel, surviving Render redeploys.
        backup = json.dumps(tracker.stats).encode('utf-8')
        try:
            with closing(discord.File(io.BytesIO(backup), filename='meeting-stats.json')) as upload:
                await channel.send(content='Meeting ended after 60 seconds of empty VC.' if automatic else None,
                                   embeds=[discord.Embed.from_dict(e) for e in embeds], file=upload,
                                   allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return {'content': 'Meeting saved locally, but the report could not be posted. Check Send Messages, Embed Links and Attach Files permissions.'}
    return {'content': summary}


_seen_messages: set[str] = set()


def _mark_seen(msg_id) -> bool:
    """Deduplicates MESSAGE_CREATE handling across the raw-socket and SDK event paths."""
    key = str(msg_id)
    if len(_seen_messages) > 1024:
        _seen_messages.clear()
    if key in _seen_messages:
        return False
    _seen_messages.add(key)
    return True


def message_payload(message: discord.Message) -> dict:
    """Converts a discord.Message into the raw-gateway dict shape handle_message expects."""
    def convert(target: discord.Message) -> dict:
        return {
            'id': str(target.id),
            'channel_id': str(target.channel.id),
            'guild_id': str(target.guild.id) if target.guild else None,
            'content': target.content or '',
            'author': {'id': str(target.author.id), 'username': target.author.name,
                       'global_name': getattr(target.author, 'global_name', None), 'bot': target.author.bot},
            'attachments': [{'id': str(a.id), 'url': a.url, 'filename': a.filename,
                             'content_type': a.content_type} for a in target.attachments],
            'embeds': [e.to_dict() for e in target.embeds],
            'mentions': [{'id': str(u.id), 'username': u.name, 'bot': u.bot} for u in target.mentions],
            'message_reference': ({'message_id': str(target.reference.message_id),
                                   'channel_id': str(target.reference.channel_id)}
                                  if target.reference else None),
            'referenced_message': convert(target.referenced_message) if target.referenced_message else None,
        }

    return convert(message)


async def music_watchdog_loop():
    """Leaves voice and stops playback when nobody is listening."""
    await client.wait_until_ready()
    while True:
        await asyncio.sleep(60)
        try:
            for q in music_player.all_queues():
                vc = q.voice_client
                if not vc or not vc.is_connected():
                    continue
                humans = [m for m in getattr(vc.channel, 'members', []) if not m.bot]
                if not humans and (q.is_playing() or q.is_paused() or q.now_playing or q.queue):
                    q.stop()
                    await vc.disconnect(force=True)
                    logging.info('Music watchdog disconnected from empty channel %s', vc.channel.name)
        except Exception as exc:
            logging.warning('Music watchdog error: %s', type(exc).__name__)


class RippleClient(discord.Client):
    async def setup_hook(self):
        self.monitor = asyncio.create_task(meeting_monitor_loop())
        self.reminders = asyncio.create_task(extras.reminder_loop())
        self.music_watchdog = asyncio.create_task(music_watchdog_loop())

    async def on_ready(self):
        logging.info('Discord READY; bot=%s; message_content=%s', self.user.id, self.intents.message_content)
        if not getattr(self, 'stats_restored', False):
            self.stats_restored = True
            channel = self.get_channel(int(tracker.reports_channel_id))
            if channel:
                try:
                    async for message in channel.history(limit=100):
                        if message.author.id != self.user.id:
                            continue
                        attachment = next((a for a in message.attachments if a.filename == 'meeting-stats.json' and a.size <= 8 * 1024 * 1024), None)
                        if attachment:
                            stats = json.loads(await attachment.read())
                            if isinstance(stats.get('members'), dict) and isinstance(stats.get('history'), list) and stats.get('total_meetings', -1) > tracker.stats['total_meetings']:
                                tracker.stats = stats
                                await asyncio.to_thread(tracker._save_stats)
                                logging.info('Restored cumulative meeting statistics')
                            break
                except (discord.HTTPException, ValueError, TypeError):
                    logging.warning('Could not restore meeting statistics from reports channel')

        # Upsert slash commands (music + extras) for the guild without touching
        # commands registered elsewhere (e.g. portal-created global commands).
        commands_url = f'/applications/{self.user.id}/guilds/{GUILD_ID}/commands'
        try:
            existing = {c['name']: c['id'] for c in await api_call(commands_url)}
        except Exception:
            existing = {}
        for cmd in MUSIC_SLASH_COMMANDS + extras.SLASH_COMMANDS + role_setup.SLASH_COMMANDS:
            try:
                if cmd['name'] in existing:
                    await api_call(f"{commands_url}/{existing[cmd['name']]}", method='PATCH', data=cmd)
                else:
                    await api_call(commands_url, method='POST', data=cmd)
            except Exception as e:
                logging.debug('Could not register slash command %s: %s', cmd['name'], e)

    async def on_interaction(self, interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get('custom_id', '')
            if custom_id.startswith('mselect:'):
                await handle_music_button(interaction, custom_id)
                return
            try:
                self._connection._view_store.dispatch_view(interaction)
            except Exception:
                pass
            return
        if interaction.type != discord.InteractionType.application_command:
            return
        _interactions[interaction.token] = interaction
        member = {'user': {'id': str(interaction.user.id), 'username': interaction.user.name,
                           'global_name': interaction.user.display_name}}
        try:
            await handle_interaction({'id': str(interaction.id), 'token': interaction.token,
                                      'channel_id': str(interaction.channel_id), 'data': interaction.data, 'member': member})
        finally:
            _interactions.pop(interaction.token, None)

    async def on_message(self, message):
        # Primary text-command path: the SDK message event. The raw-socket
        # listener below is only a fallback; deduplication prevents double runs.
        if message.author.bot:
            return
        if not _mark_seen(message.id):
            return
        await handle_message(message_payload(message))

    async def on_socket_raw_receive(self, message):
        # Fallback path in case the SDK message event misses messages
        # (discord.py emits decompressed JSON text for this debug event).
        if isinstance(message, bytes):
            message = message.decode('utf-8', errors='ignore')
        try:
            payload = json.loads(message)
        except Exception:
            return
        if payload.get('t') == 'MESSAGE_CREATE':
            d = payload['d']
            if not _mark_seen(d.get('id')):
                return
            await handle_message(d)

    async def on_member_join(self, member):
        try:
            await extras.on_member_join(member)
        except Exception as exc:
            logging.warning('Member join handler failed: %s', type(exc).__name__)

    async def on_member_remove(self, member):
        try:
            await extras.on_member_remove(member)
        except Exception as exc:
            logging.warning('Member remove handler failed: %s', type(exc).__name__)

    async def on_voice_state_update(self, member, before, after):
        if str(member.guild.id) != GUILD_ID or member.bot:
            return
        async with _meeting_lock:
            tracker.on_voice_state_update(str(member.id), member.name, member.display_name,
                                          str(before.channel.id) if before.channel else None,
                                          str(after.channel.id) if after.channel else None)

    async def on_raw_reaction_add(self, event):
        if event.user_id == self.user.id or str(event.guild_id) != GUILD_ID:
            return
        role_id = ROLE_MAP.get(str(event.message_id), {}).get(event.emoji.name)
        if role_id:
            await modify_role(str(event.user_id), role_id, 'PUT')
        elif event.emoji.name in FLAG_TO_LANG:
            code, name = FLAG_TO_LANG[event.emoji.name]
            message = await api_call(f'/channels/{event.channel_id}/messages/{event.message_id}')
            if message.get('content'):
                translated = await asyncio.to_thread(do_translate, message['content'], code)
                await send_discord_reply(str(event.channel_id), f'{event.emoji.name} **{name}:**\n{translated}', str(event.message_id))

    async def on_raw_reaction_remove(self, event):
        if str(event.guild_id) == GUILD_ID and event.user_id != self.user.id:
            role_id = ROLE_MAP.get(str(event.message_id), {}).get(event.emoji.name)
            if role_id:
                await modify_role(str(event.user_id), role_id, 'DELETE')


intents = discord.Intents.default()
intents.message_content = True
client = RippleClient(intents=intents, enable_debug_events=True, allowed_mentions=discord.AllowedMentions.none())
extras.setup(client, api_call, GUILD_ID)


async def run_bot():
    if not TOKEN:
        raise RuntimeError('DISCORD_BOT_TOKEN is required')
    await start_health_server()
    async with client:
        delay = 300
        while not client.is_closed():
            try:
                await client.start(TOKEN)
                return
            except discord.HTTPException as exc:
                if exc.status != 429 and exc.status < 500:
                    raise
                retry_after = exc.response.headers.get('Retry-After', '')
                try:
                    wait = max(delay, float(retry_after))
                except ValueError:
                    wait = delay
                logging.warning('Discord login HTTP %s; retrying in %ss. Commands remain unavailable until READY.', exc.status, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2, 900)

if __name__ == '__main__':
    print(f"RippleBot SDK runtime {os.environ.get('RENDER_GIT_COMMIT', 'local')}", flush=True)
    print("=" * 60)
    print("             🌊 RIPPLEBOT SERVICE (AI-POWERED) 🌊")
    print("=" * 60)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())
