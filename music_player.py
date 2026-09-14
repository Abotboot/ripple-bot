"""High-performance Discord music player with Spotify resolution and yt-dlp streaming."""
import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

import discord
import yt_dlp

logger = logging.getLogger('music_player')

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'ignoreerrors': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios']
        }
    }
}

FFMPEG_BEFORE_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
FFMPEG_OPTIONS = '-vn'


@dataclass
class MusicTrack:
    title: str
    artist: str
    source_url: str
    stream_url: str
    duration: int  # in seconds
    thumbnail: str
    requester: str

    def format_duration(self) -> str:
        if self.duration <= 0:
            return "Live"
        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes:02d}:{seconds:02d}"


def resolve_spotify_url(url: str) -> Optional[dict]:
    """Resolves Spotify track title and artist using public oEmbed + page title metadata."""
    parsed = urllib.parse.urlparse(url)
    if 'spotify.com' not in parsed.netloc:
        return None

    title = ""
    artist = ""
    thumbnail = ""

    # 1. Fetch Spotify oEmbed
    try:
        oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
        req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            title = data.get('title', '')
            thumbnail = data.get('thumbnail_url', '')
    except Exception as e:
        logger.debug("Spotify oEmbed lookup failed: %s", e)

    # 2. Fetch page title for detailed artist
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            m = re.search(r'<title>(.*?)</title>', html)
            if m:
                raw_title = m.group(1)
                # Format: "Track - song and lyrics by Artist | Spotify"
                match = re.search(r'(.*?)\s*-\s*song\s*(?:and lyrics\s*)?by\s*(.*?)\s*\|\s*Spotify', raw_title, re.IGNORECASE)
                if match:
                    title = match.group(1).strip()
                    artist = match.group(2).strip()
                elif ' | Spotify' in raw_title:
                    cleaned = raw_title.replace(' | Spotify', '').strip()
                    parts = cleaned.split(' - ')
                    if len(parts) >= 2:
                        title = parts[0].strip()
                        artist = parts[1].strip()
                    else:
                        title = cleaned
    except Exception as e:
        logger.debug("Spotify page title lookup failed: %s", e)

    if not title:
        return None

    search_query = f"{title} {artist}".strip()
    return {
        "title": title,
        "artist": artist or "Spotify Artist",
        "search_query": search_query,
        "thumbnail": thumbnail,
    }


def search_tracks_sync(query: str, limit: int = 5) -> list[dict]:
    """Extracts up to `limit` candidates from YouTube (android/ios client) or SoundCloud."""
    results = []
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
        if query.startswith(('http://', 'https://')):
            try:
                info = ytdl.extract_info(query, download=False)
                if info:
                    if 'entries' in info and info['entries']:
                        return [e for e in info['entries'] if e][:limit]
                    return [info]
            except Exception as e:
                logger.warning("Direct URL extraction failed: %s", e)

        try:
            target = f"ytsearch{limit}:{query}"
            info = ytdl.extract_info(target, download=False)
            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry and entry.get('title'):
                        results.append(entry)
                        if len(results) >= limit:
                            break
        except Exception as e:
            logger.warning("YouTube search failed: %s; trying SoundCloud...", e)

        if len(results) < limit:
            try:
                sc_count = limit - len(results)
                sc_target = f"scsearch{sc_count + 3}:{query}"
                sc_info = ytdl.extract_info(sc_target, download=False)
                if sc_info and 'entries' in sc_info:
                    for entry in sc_info['entries']:
                        if entry and entry.get('title') and entry.get('url'):
                            results.append(entry)
                            if len(results) >= limit:
                                break
            except Exception as sc_err:
                logger.warning("SoundCloud search failed: %s", sc_err)

    return results


async def search_tracks(query: str, requester: str, limit: int = 5) -> list[MusicTrack]:
    """Searches and returns up to `limit` candidates as MusicTrack objects."""
    query = query.strip()
    spotify_meta = None

    if 'open.spotify.com' in query:
        spotify_meta = await asyncio.to_thread(resolve_spotify_url, query)
        if spotify_meta:
            query = spotify_meta['search_query']

    raw_entries = await asyncio.to_thread(search_tracks_sync, query, limit)
    tracks = []
    for idx, info in enumerate(raw_entries):
        title = (spotify_meta['title'] if (spotify_meta and idx == 0) else info.get('title')) or 'Unknown Title'
        artist = (spotify_meta['artist'] if (spotify_meta and idx == 0) else (info.get('artist') or info.get('uploader'))) or 'Unknown Artist'
        duration = int(info.get('duration') or 0)
        thumbnail = (spotify_meta.get('thumbnail') if (spotify_meta and idx == 0) else None) or info.get('thumbnail') or ""
        source_url = info.get('webpage_url') or query
        stream_url = info.get('url') or ""

        tracks.append(MusicTrack(
            title=title,
            artist=artist,
            source_url=source_url,
            stream_url=stream_url,
            duration=duration,
            thumbnail=thumbnail,
            requester=requester,
        ))
    return tracks


async def resolve_track(query: str, requester: str) -> Optional[MusicTrack]:
    """Resolves best match for query."""
    tracks = await search_tracks(query, requester, limit=1)
    return tracks[0] if tracks else None


class GuildMusicQueue:
    def __init__(self, guild_id: str, client: discord.Client):
        self.guild_id = guild_id
        self.client = client
        self.queue: list[MusicTrack] = []
        self.now_playing: Optional[MusicTrack] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.volume: float = 0.5
        self.lock = asyncio.Lock()
        self.source = None

    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    def is_paused(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_paused())

    async def enqueue(self, track: MusicTrack, vc: discord.VoiceClient, channel_to_notify=None) -> int:
        async with self.lock:
            self.voice_client = vc
            self.queue.append(track)
            position = len(self.queue)
            if not self.is_playing() and not self.is_paused() and not self.now_playing:
                await self._play_next(channel_to_notify)
            return position

    async def _play_next(self, notify_channel=None):
        if not self.queue:
            self.now_playing = None
            self.source = None
            return

        self.now_playing = self.queue.pop(0)
        track = self.now_playing

        if not self.voice_client or not self.voice_client.is_connected():
            logger.warning("Voice client disconnected while queueing %s", track.title)
            self.now_playing = None
            return

        try:
            # Re-resolve stream url if expired (yt-dlp stream urls can expire after a while)
            if not track.stream_url:
                refreshed = await resolve_track(track.source_url or track.title, track.requester)
                if refreshed:
                    track.stream_url = refreshed.stream_url

            ffmpeg_audio = discord.FFmpegPCMAudio(
                track.stream_url,
                before_options=FFMPEG_BEFORE_OPTIONS,
                options=FFMPEG_OPTIONS,
            )
            self.source = discord.PCMVolumeTransformer(ffmpeg_audio, volume=self.volume)

            loop = asyncio.get_running_loop()

            def after_playing(err):
                if err:
                    logger.error("Error playing track %s: %s", track.title, err)
                asyncio.run_coroutine_threadsafe(self._play_next(notify_channel), loop)

            self.voice_client.play(self.source, after=after_playing)

            if notify_channel:
                embed = discord.Embed(
                    title="🎶 Now Playing",
                    description=f"**[{track.title}]({track.source_url})**\nby **{track.artist}**",
                    color=discord.Color.blue(),
                )
                embed.add_field(name="Duration", value=track.format_duration(), inline=True)
                embed.add_field(name="Requested By", value=track.requester, inline=True)
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                await notify_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

        except Exception as e:
            logger.exception("Failed to start playback for %s: %s", track.title, e)
            if notify_channel:
                await notify_channel.send(f"⚠️ Error playing **{track.title}**: `{e}`")
            await self._play_next(notify_channel)

    def skip(self) -> bool:
        if self.voice_client and (self.is_playing() or self.is_paused()):
            self.voice_client.stop()
            return True
        return False

    def pause(self) -> bool:
        if self.voice_client and self.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.is_paused():
            self.voice_client.resume()
            return True
        return False

    def stop(self) -> bool:
        self.queue.clear()
        self.now_playing = None
        if self.voice_client and (self.is_playing() or self.is_paused()):
            self.voice_client.stop()
        return True

    def set_volume(self, volume_percent: int) -> float:
        vol = max(1, min(100, volume_percent)) / 100.0
        self.volume = vol
        if self.source:
            self.source.volume = vol
        return vol


# Global registry of music queues keyed by guild_id
_guild_queues: dict[str, GuildMusicQueue] = {}

def get_queue(guild_id: str, client: discord.Client) -> GuildMusicQueue:
    if guild_id not in _guild_queues:
        _guild_queues[guild_id] = GuildMusicQueue(guild_id, client)
    return _guild_queues[guild_id]
