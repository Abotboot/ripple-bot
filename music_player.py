"""High-performance Discord music player with Spotify resolution and yt-dlp streaming."""
import asyncio
import ipaddress
import json
import logging
import random
import re
import socket
import time
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
    'ignoreerrors': True,
    'socket_timeout': 10,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios', 'web']
        }
    }
}

# Flat search only resolves the listing (title/id/duration), which is several
# times faster than fully extracting every candidate video.
SEARCH_OPTIONS = {**YTDL_OPTIONS, 'extract_flat': 'discard_in_playlist'}

FFMPEG_BEFORE_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
FFMPEG_OPTIONS = '-vn'

STREAM_CACHE_TTL = 1800  # resolved stream URLs stay valid for hours; refresh every 30 min
_stream_cache: dict[str, tuple[float, dict]] = {}


def assert_public_http_url(url: str) -> str:
    """SSRF guard: only public http(s) URLs may be fetched by yt-dlp/FFmpeg.

    Resolves every hostname to an IP and rejects private, loopback, and
    link-local targets so a crafted !play URL cannot make the (cloud-hosted)
    bot probe internal services. Residual risk: a public host that later
    redirects to a private IP cannot be caught by DNS checks alone.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('Only http(s) media URLs are supported')
    host = parsed.hostname
    if not host:
        raise ValueError('Invalid media URL')
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f'Could not resolve media host: {host}') from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError('Media host resolves to a private address')


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


def _spotify_oembed(url: str) -> dict:
    req = urllib.request.Request(
        f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}",
        headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _spotify_page_meta(url: str) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        html = resp.read().decode('utf-8', errors='ignore')
    meta = {}
    m = re.search(r'<title>(.*?)</title>', html)
    if m:
        raw_title = m.group(1)
        # Format: "Track - song and lyrics by Artist | Spotify"
        match = re.search(r'(.*?)\s*-\s*song\s*(?:and lyrics\s*)?by\s*(.*?)\s*\|\s*Spotify', raw_title, re.IGNORECASE)
        if match:
            meta['title'] = match.group(1).strip()
            meta['artist'] = match.group(2).strip()
        elif ' | Spotify' in raw_title:
            cleaned = raw_title.replace(' | Spotify', '').strip()
            parts = cleaned.split(' - ')
            if len(parts) >= 2:
                meta['title'] = parts[0].strip()
                meta['artist'] = parts[1].strip()
            else:
                meta['title'] = cleaned
    return meta


def resolve_spotify_url(url: str) -> Optional[dict]:
    """Resolves Spotify track title and artist using public oEmbed + page title metadata (no API keys needed)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    # Strict host check (a bare substring match would accept open.spotify.com.evil.com)
    if host != 'open.spotify.com' and not host.endswith('.open.spotify.com'):
        return None

    oembed, page = {}, {}
    try:
        oembed = _spotify_oembed(url)
    except Exception as e:
        logger.debug("Spotify oEmbed lookup failed: %s", e)
    try:
        page = _spotify_page_meta(url)
    except Exception as e:
        logger.debug("Spotify page title lookup failed: %s", e)

    title = page.get('title') or oembed.get('title', '')
    if not title:
        return None
    return {
        "title": title,
        "artist": page.get('artist') or "Spotify Artist",
        "search_query": f"{title} {page.get('artist') or ''}".strip(),
        "thumbnail": oembed.get('thumbnail_url', ''),
    }


def _flat_entries_sync(query: str, limit: int) -> list[dict]:
    """Fast search: resolves the result listing without extracting every video."""
    with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ytdl:
        info = ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = [e for e in ((info or {}).get('entries') or []) if e]
        if not entries:
            try:
                info = ytdl.extract_info(f"scsearch{limit}:{query}", download=False)
                entries = [e for e in ((info or {}).get('entries') or []) if e and e.get('url')]
            except Exception as sc_err:
                logger.warning("SoundCloud search failed: %s", sc_err)
        return entries[:limit]


def _extract_info_sync(url: str) -> Optional[dict]:
    """Full extraction of a single track (returns the playable info dict)."""
    assert_public_http_url(url)
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
        info = ytdl.extract_info(url, download=False)
        if info and 'entries' in info:
            info = next((e for e in info['entries'] if e), None)
        return info


def _direct_url_entries_sync(query: str, limit: int) -> list[dict]:
    """Full extraction for direct links (needed for the stream URL right away)."""
    assert_public_http_url(query)
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
        info = ytdl.extract_info(query, download=False)
        if not info:
            return []
        if 'entries' in info:
            return [e for e in info['entries'] if e][:limit]
        return [info]


async def get_stream_info(source_url: str) -> Optional[dict]:
    """Resolves (and caches) the playable stream info for a track page URL."""
    if not source_url:
        return None
    cached = _stream_cache.get(source_url)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        info = await asyncio.to_thread(_extract_info_sync, source_url)
    except Exception as e:
        logger.warning("Stream resolution failed for %s: %s", source_url, e)
        return None
    if not info or not info.get('url'):
        return None
    if len(_stream_cache) > 256:
        for key in list(_stream_cache)[:64]:
            _stream_cache.pop(key, None)
    _stream_cache[source_url] = (time.time() + STREAM_CACHE_TTL, info)
    return info


async def search_tracks(query: str, requester: str, limit: int = 5, resolve_first: bool = True) -> list[MusicTrack]:
    """Searches and returns up to `limit` candidates as MusicTrack objects.

    Uses a fast flat listing for search queries and only fully resolves the
    stream URL of the first track; the rest resolve lazily when played.
    """
    query = query.strip()
    spotify_meta = None

    if 'open.spotify.com' in query:
        spotify_meta = await asyncio.to_thread(resolve_spotify_url, query)
        if spotify_meta:
            query = spotify_meta['search_query']

    is_url = query.startswith(('http://', 'https://'))
    if is_url:
        raw_entries = await asyncio.to_thread(_direct_url_entries_sync, query, limit)
    else:
        raw_entries = await asyncio.to_thread(_flat_entries_sync, query, limit)

    tracks = []
    for idx, info in enumerate(raw_entries):
        title = (spotify_meta['title'] if (spotify_meta and idx == 0) else info.get('title')) or 'Unknown Title'
        artist = (spotify_meta['artist'] if (spotify_meta and idx == 0) else (info.get('artist') or info.get('uploader') or info.get('channel'))) or 'Unknown Artist'
        duration = int(info.get('duration') or 0)
        thumbnail = (spotify_meta.get('thumbnail') if (spotify_meta and idx == 0) else None) or info.get('thumbnail') or ""
        source_url = info.get('webpage_url') or info.get('url') or query
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

    if tracks and resolve_first and not tracks[0].stream_url:
        info = await get_stream_info(tracks[0].source_url)
        if info:
            tracks[0].stream_url = info.get('url') or ""
            if tracks[0].duration <= 0 and info.get('duration'):
                tracks[0].duration = int(info['duration'])
    return tracks


class GuildMusicQueue:
    def __init__(self, guild_id: str, client: discord.Client):
        self.guild_id = guild_id
        self.client = client
        self.queue: list[MusicTrack] = []
        self.now_playing: Optional[MusicTrack] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.volume: float = 0.5
        self.loop: bool = False
        self.lock = asyncio.Lock()
        self.source = None

    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    def is_paused(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_paused())

    async def enqueue(self, track: MusicTrack, vc: discord.VoiceClient, channel_to_notify=None, front: bool = False) -> int:
        async with self.lock:
            self.voice_client = vc
            if front and self.now_playing:
                self.queue.insert(0, track)
            else:
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
            # Resolve the stream URL lazily (and re-resolve expired ones) so
            # searches stay fast and cached URLs never go stale.
            if not track.stream_url:
                info = await get_stream_info(track.source_url or track.title)
                if info:
                    track.stream_url = info.get('url') or ""
                    if track.duration <= 0 and info.get('duration'):
                        track.duration = int(info['duration'])

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
                if self.loop and self.now_playing is track:
                    self.queue.insert(0, track)
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

    def shuffle(self) -> int:
        random.shuffle(self.queue)
        return len(self.queue)

    def remove(self, index: int) -> Optional[MusicTrack]:
        if 1 <= index <= len(self.queue):
            return self.queue.pop(index - 1)
        return None

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

def all_queues() -> list[GuildMusicQueue]:
    return list(_guild_queues.values())
