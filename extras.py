"""RippleBot Extras: MEE6/Carl-bot style features.

- Moderation: warn / warnings / clearwarnings / kick / ban / unban / timeout / untimeout
- AutoMod: anti-spam, invite links, banned words, mention spam, excessive caps
- Leveling: MEE6-style XP, /rank, /leaderboard, level-up announcements
- Welcome & Goodbye messages + auto-role
- Reminders (persistent, survive restarts)
- Fun & info: 8ball, roll, coinflip, choose, poll, avatar, userinfo, serverinfo, help

Everything is persisted to extras_data.json next to this file. All commands are
exposed both as slash commands and ! prefix commands.
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord

logger = logging.getLogger('extras')

client = None
api = None
GUILD_ID = '0'
DATA_FILE = os.path.join(os.path.dirname(__file__), 'extras_data.json')

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

DEFAULT_DATA = {
    'warnings': {},          # user_id -> [{reason, mod, ts}]
    'xp': {},                # user_id -> {'xp': int, 'name': str}
    'automod': {
        'enabled': True,
        'words': [],
        'block_invites': True,
        'block_caps': True,
        'welcome_channel': None,
        'welcome_message': 'Welcome {user} to **{server}**! 🎉 You are member #{count}.',
        'goodbye_channel': None,
        'autorole': None,
    },
    'reminders': [],         # {'id', 'user_id', 'user_name', 'channel_id', 'at', 'text'}
}


def _load():
    global _data
    _data = json.loads(json.dumps(DEFAULT_DATA))
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            stored = json.load(f)
        for key, value in stored.items():
            if key == 'automod' and isinstance(value, dict):
                _data['automod'].update(value)
            else:
                _data[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning('Could not load extras data: %s', type(exc).__name__)


def _save():
    tmp = DATA_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_data, f, indent=1)
        os.replace(tmp, DATA_FILE)
    except Exception as exc:
        logger.warning('Could not save extras data: %s', type(exc).__name__)


def setup(client_, api_call_, guild_id_):
    global client, api, GUILD_ID
    client, api, GUILD_ID = client_, api_call_, str(guild_id_)
    _load()


def _amod() -> dict:
    return _data['automod']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guild() -> discord.Guild:
    return client.get_guild(int(GUILD_ID))


async def _member_from_id(user_id: str) -> Optional[discord.Member]:
    guild = _guild()
    if not guild:
        return None
    member = guild.get_member(int(user_id))
    if not member:
        try:
            member = await guild.fetch_member(int(user_id))
        except (discord.HTTPException, ValueError):
            return None
    return member


async def _timeout_member(user_id: str, minutes: int, reason: str) -> bool:
    try:
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
        await api(f'/guilds/{GUILD_ID}/members/{user_id}', method='PATCH',
                  data={'communication_disabled_until': until, 'reason': reason})
        return True
    except Exception as exc:
        logger.warning('Timeout failed for %s: %s', user_id, type(exc).__name__)
        return False


def _member_from_mention(arg: str) -> Optional[str]:
    m = re.match(r'^(?:<@!?(\d+)>|(\d+))$', arg.strip())
    if m:
        return m.group(1) or m.group(2)
    return None


def _user_display(user_id: str) -> str:
    member = _guild().get_member(int(user_id)) if _guild() else None
    if member:
        return member.display_name
    rec = _data['xp'].get(user_id) or {}
    return rec.get('name') or f'User {user_id}'


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def _add_warning(user_id: str, reason: str, mod_name: str) -> int:
    lst = _data['warnings'].setdefault(user_id, [])
    lst.append({'reason': reason[:300], 'mod': mod_name, 'ts': int(time.time())})
    if len(lst) > 100:
        _data['warnings'][user_id] = lst[-100:]
    _save()
    return len(lst)


async def escalate(user_id: str, user_name: str) -> Optional[str]:
    """Auto-escalate repeat offenders: 3 warns -> 10m timeout, 5 -> 60m, 7 -> 24h."""
    count = len(_data['warnings'].get(user_id, []))
    if count >= 7:
        minutes = 24 * 60
    elif count >= 5:
        minutes = 60
    elif count >= 3:
        minutes = 10
    else:
        return None
    ok = await _timeout_member(user_id, minutes, f'Auto-escalation: {count} warnings')
    if ok:
        return f'⏰ **{user_name}** was timed out for {minutes} minutes ({count} warnings).'
    return None


# ---------------------------------------------------------------------------
# AutoMod
# ---------------------------------------------------------------------------

_msg_times: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
_INVITE_RE = re.compile(r'(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/[\w\-]+', re.IGNORECASE)


def _automod_violation(author_id: str, content: str, mention_count: int) -> Optional[str]:
    am = _amod()
    if not am.get('enabled'):
        return None
    lowered = content.lower()

    if am.get('block_invites', True) and _INVITE_RE.search(content):
        return 'server invite links are not allowed here'
    words = am.get('words') or []
    if words:
        for word in words:
            if word and re.search(rf'\b{re.escape(word.lower())}\b', lowered):
                return f'language filter (`{word}`)'
    if mention_count > 5:
        return 'mention spam'
    if am.get('block_caps', True):
        letters = [c for c in content if c.isalpha()]
        if len(letters) >= 30 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
            return 'excessive caps'
    now = time.time()
    times = _msg_times[author_id]
    times.append(now)
    if len([t for t in times if now - t < 8]) >= 8:
        times.clear()
        return 'sending messages too fast (spam)'
    return None


async def run_automod(d: dict) -> bool:
    """Checks a message against AutoMod. Returns True when the message was acted on."""
    channel_id = d.get('channel_id')
    msg_id = d.get('id')
    author = d.get('author', {})
    author_id = author.get('id', '')
    content = d.get('content') or ''

    guild = _guild()
    member = guild.get_member(int(author_id)) if guild else None
    channel = client.get_channel(int(channel_id)) if channel_id else None
    if member and channel and member.guild_permissions.manage_messages:
        _msg_times[author_id].clear()
        return False

    violation = _automod_violation(author_id, content, len(d.get('mentions') or []))
    if not violation:
        return False

    reason = f'AutoMod: {violation}'
    _add_warning(author_id, reason, 'AutoMod')
    try:
        await api(f'/channels/{channel_id}/messages/{msg_id}', method='DELETE')
    except Exception:
        pass
    escalation = await escalate(author_id, author.get('username', 'User'))
    notice = f'🛑 **{author.get("username", "User")}**, your message was removed: {violation}. Warning **{len(_data["warnings"].get(author_id, []))}**.'
    if escalation:
        notice += f'\n{escalation}'
    try:
        if channel:
            sent = await channel.send(notice, allowed_mentions=discord.AllowedMentions.none())
            await asyncio.sleep(6)
            await sent.delete()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Leveling (MEE6-style)
# ---------------------------------------------------------------------------

_xp_cooldown: dict[str, float] = {}


def _xp_for_level(level: int) -> int:
    return 5 * level ** 2 + 50 * level + 100


def _level_from_xp(xp: int) -> int:
    level, total = 0, 0
    while total + _xp_for_level(level) <= xp:
        total += _xp_for_level(level)
        level += 1
    return level


def award_xp(author_id: str, author_name: str) -> Optional[int]:
    """Awards XP for activity. Returns the new level when the user leveled up."""
    now = time.time()
    if now - _xp_cooldown.get(author_id, 0) < 60:
        return None
    _xp_cooldown[author_id] = now
    rec = _data['xp'].setdefault(author_id, {'xp': 0, 'name': author_name})
    rec['name'] = author_name
    before = _level_from_xp(rec['xp'])
    rec['xp'] += random.randint(15, 25)
    after = _level_from_xp(rec['xp'])
    if after > before:
        _save()
        return after
    return None


def _progress_bar(xp: int, level: int) -> str:
    needed = _xp_for_level(level)
    into = xp - sum(_xp_for_level(l) for l in range(level))
    ratio = max(0.0, min(1.0, into / needed))
    filled = int(ratio * 14)
    return f"{'█' * filled}{'░' * (14 - filled)} {into}/{needed} XP"


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

_DUR_RE = re.compile(r'(\d+d)|(\d+h)|(\d+m)|(\d+s)', re.IGNORECASE)


def parse_duration(text: str) -> Optional[int]:
    matches = _DUR_RE.findall(text)
    if not matches:
        return None
    total = 0
    for d, h, m, s in matches:
        total += int(d[:-1]) * 86400 if d else 0
        total += int(h[:-1]) * 3600 if h else 0
        total += int(m[:-1]) * 60 if m else 0
        total += int(s[:-1]) if s else 0
    return total if 5 <= total <= 30 * 86400 else None


async def add_reminder(user_id: str, user_name: str, channel_id: str, delay: int, text: str) -> dict:
    reminder = {
        'id': f'{int(time.time() * 1000) % 100000000}',
        'user_id': user_id,
        'user_name': user_name,
        'channel_id': channel_id,
        'at': time.time() + delay,
        'text': text[:500],
    }
    _data['reminders'].append(reminder)
    _save()
    return reminder


async def reminder_loop():
    await client.wait_until_ready()
    while True:
        try:
            now = time.time()
            due = [r for r in _data['reminders'] if r['at'] <= now]
            for r in due:
                _data['reminders'].remove(r)
                await api(f"/channels/{r['channel_id']}/messages", method='POST', data={
                    'content': f"⏰ <@{r['user_id']}> **Reminder:** {r['text']}",
                    'allowed_mentions': {'parse': ['users']},
                })
            if due:
                _save()
        except Exception as exc:
            logger.warning('Reminder loop error: %s', type(exc).__name__)
        await asyncio.sleep(10)


# ---------------------------------------------------------------------------
# Welcome / Goodbye / AutoRole
# ---------------------------------------------------------------------------

async def on_member_join(member: discord.Member):
    am = _amod()
    role_id = am.get('autorole')
    if role_id:
        try:
            await api(f'/guilds/{GUILD_ID}/members/{member.id}/roles/{role_id}', method='PUT')
        except Exception as exc:
            logger.warning('Autorole failed: %s', type(exc).__name__)
    chan_id = am.get('welcome_channel')
    if not chan_id:
        return
    try:
        count = _guild().member_count if _guild() else 0
        msg = (am.get('welcome_message') or '').replace('{user}', member.mention) \
            .replace('{server}', member.guild.name).replace('{count}', str(count))
        await api(f'/channels/{chan_id}/messages', method='POST',
                  data={'content': msg, 'allowed_mentions': {'parse': ['users']}})
    except Exception as exc:
        logger.warning('Welcome message failed: %s', type(exc).__name__)


async def on_member_remove(member: discord.Member):
    chan_id = _amod().get('goodbye_channel')
    if not chan_id:
        return
    try:
        await api(f'/channels/{chan_id}/messages', method='POST', data={
            'content': f'👋 **{member.display_name}** has left the server.'})
    except Exception as exc:
        logger.warning('Goodbye message failed: %s', type(exc).__name__)


# ---------------------------------------------------------------------------
# Command implementations (shared by slash + prefix)
# ---------------------------------------------------------------------------

def _fmt_warnings(user_id: str) -> dict:
    lst = _data['warnings'].get(user_id, [])
    if not lst:
        return {'content': f'✅ **{_user_display(user_id)}** has no warnings.'}
    lines = [f"`{i+1}.` {w['reason']} — *by {w['mod']}* <t:{w['ts']}:R>" for i, w in enumerate(lst[-10:])]
    return {'content': f"⚠️ **{_user_display(user_id)}** has **{len(lst)}** warning(s):\n" + '\n'.join(lines)}


async def cmd_warn(target_id: str, reason: str, mod_name: str) -> dict:
    member = await _member_from_id(target_id)
    if not member:
        return {'content': '⚠️ Could not find that member.'}
    if member.guild_permissions.manage_messages:
        return {'content': '⚠️ You cannot warn a moderator.'}
    count = _add_warning(target_id, reason or 'No reason provided', mod_name)
    escalation = await escalate(target_id, member.display_name)
    msg = f"⚠️ **{member.display_name}** was warned by {mod_name} ({count} total): *{reason or 'No reason provided'}*"
    if escalation:
        msg += f'\n{escalation}'
    return {'content': msg}


async def cmd_kick(target_id: str, reason: str, mod_name: str) -> dict:
    member = await _member_from_id(target_id)
    if not member:
        return {'content': '⚠️ Could not find that member.'}
    try:
        await member.kick(reason=f'By {mod_name}: {reason}')
        return {'content': f"👢 **{member.display_name}** was kicked. Reason: *{reason or 'No reason provided'}*"}
    except discord.HTTPException:
        return {'content': '⚠️ Could not kick: I need the **Kick Members** permission and a higher role.'}


async def cmd_ban(target_id: str, reason: str, mod_name: str) -> dict:
    member = await _member_from_id(target_id)
    if not member:
        # Maybe already banned / left; ban by ID directly.
        try:
            await api(f'/guilds/{GUILD_ID}/bans/{target_id}', method='PUT',
                      data={'delete_message_seconds': 0, 'reason': f'By {mod_name}: {reason}'})
            return {'content': f'🔨 Banned user ID `{target_id}`.'}
        except discord.HTTPException:
            return {'content': '⚠️ Could not find or ban that user.'}
    try:
        await member.ban(reason=f'By {mod_name}: {reason}', delete_message_days=0)
        return {'content': f"🔨 **{member.display_name}** was banned. Reason: *{reason or 'No reason provided'}*"}
    except discord.HTTPException:
        return {'content': '⚠️ Could not ban: I need the **Ban Members** permission and a higher role.'}


async def cmd_unban(user_id: str, mod_name: str) -> dict:
    try:
        await api(f'/guilds/{GUILD_ID}/bans/{user_id}', method='DELETE')
        return {'content': f'✅ Unbanned `{user_id}`.'}
    except discord.HTTPException:
        return {'content': '⚠️ Could not unban that ID (not banned, or I lack **Ban Members**).'}


async def cmd_timeout(target_id: str, minutes: int, reason: str, mod_name: str) -> dict:
    member = await _member_from_id(target_id)
    if not member:
        return {'content': '⚠️ Could not find that member.'}
    ok = await _timeout_member(target_id, minutes, f'By {mod_name}: {reason}')
    if ok:
        return {'content': f"🔇 **{member.display_name}** is muted for **{minutes}m**. Reason: *{reason or 'No reason provided'}*"}
    return {'content': '⚠️ Could not timeout: I need the **Moderate Members** permission and a higher role.'}


async def cmd_untimeout(target_id: str) -> dict:
    ok = await _timeout_member(target_id, 0, 'Timeout removed')
    return {'content': f'🔊 Timeout removed for **{_user_display(target_id)}**.'} if ok else \
        {'content': '⚠️ Could not remove timeout (I need **Moderate Members**).'}


async def cmd_clear(channel_id: str, amount: int, requester_name: str) -> dict:
    amount = max(1, min(100, amount))
    msgs = await api(f'/channels/{channel_id}/messages?limit={amount}')
    if not msgs:
        return {'content': 'No messages found to delete.'}
    ids = [m['id'] for m in msgs]
    if len(ids) == 1:
        await api(f'/channels/{channel_id}/messages/{ids[0]}', method='DELETE')
    else:
        await api(f'/channels/{channel_id}/messages/bulk-delete', method='POST', data={'messages': ids})
    return {'content': f'🧹 Deleted **{len(ids)}** messages (requested by {requester_name}).'}


async def cmd_slowmode(channel_id: str, seconds: int) -> dict:
    seconds = max(0, min(21600, seconds))
    await api(f'/channels/{channel_id}', method='PATCH', data={'rate_limit_per_user': seconds})
    if seconds == 0:
        return {'content': '⚡ Slowmode disabled.'}
    return {'content': f'🐢 Slowmode set to **{seconds}s**.'}


async def cmd_lock(channel_id: str) -> dict:
    await api(f'/channels/{channel_id}/permissions/{_guild().id}', method='PATCH',
              data={'type': 0, 'deny': str(1 << 10)})  # SEND_MESSAGES
    return {'content': '🔒 Channel locked.'}


async def cmd_unlock(channel_id: str) -> dict:
    await api(f'/channels/{channel_id}/permissions/{_guild().id}', method='PATCH',
              data={'type': 0, 'allow': str(1 << 10)})
    return {'content': '🔓 Channel unlocked.'}


def cmd_rank(user_id: str) -> dict:
    rec = _data['xp'].get(user_id)
    if not rec:
        return {'content': '📉 No XP yet — start chatting to earn levels!'}
    xp = rec['xp']
    level = _level_from_xp(xp)
    board = sorted(_data['xp'].items(), key=lambda kv: kv[1]['xp'], reverse=True)
    place = next((i + 1 for i, (uid, _) in enumerate(board) if uid == user_id), '?')
    embed = {
        'title': f"📊 {rec.get('name', 'User')}'s Rank",
        'color': 0x0ea5e9,
        'fields': [
            {'name': 'Level', 'value': f'**{level}**', 'inline': True},
            {'name': 'Total XP', 'value': f'{xp}', 'inline': True},
            {'name': 'Server Rank', 'value': f'#{place} of {len(board)}', 'inline': True},
            {'name': f'Progress to level {level + 1}', 'value': _progress_bar(xp, level), 'inline': False},
        ],
    }
    return {'embeds': [embed]}


def cmd_leaderboard() -> dict:
    board = sorted(_data['xp'].items(), key=lambda kv: kv[1]['xp'], reverse=True)[:10]
    if not board:
        return {'content': '📉 Nobody has earned XP yet. Start chatting!'}
    medals = ['🥇', '🥈', '🥉']
    lines = []
    for i, (uid, rec) in enumerate(board):
        icon = medals[i] if i < 3 else f'`{i+1}.`'
        lines.append(f"{icon} **{rec.get('name', f'User {uid}')}** — Level **{_level_from_xp(rec['xp'])}** ({rec['xp']} XP)")
    return {'content': '🏆 **XP Leaderboard**\n' + '\n'.join(lines)}


def cmd_userinfo(user_id: str) -> dict:
    member = _guild().get_member(int(user_id)) if _guild() else None
    if not member:
        return {'content': '⚠️ Could not find that member in this server.'}
    roles = ', '.join(r.mention for r in member.roles[1:][:15]) or 'None'
    joined = int(member.joined_at.timestamp()) if member.joined_at else 0
    created = int(member.created_at.timestamp())
    embed = {
        'title': f'👤 {member.display_name}',
        'thumbnail': {'url': member.display_avatar.url},
        'color': 0x0ea5e9,
        'fields': [
            {'name': 'Username', 'value': f'{member.name}', 'inline': True},
            {'name': 'ID', 'value': str(member.id), 'inline': True},
            {'name': 'Joined Server', 'value': f'<t:{joined}:R>' if joined else 'Unknown', 'inline': True},
            {'name': 'Account Created', 'value': f'<t:{created}:R>', 'inline': True},
            {'name': f'Roles ({len(member.roles) - 1})', 'value': roles[:1024], 'inline': False},
        ],
    }
    if member.premium_since:
        embed['fields'].append({'name': 'Boosting', 'value': '💎 Yes', 'inline': True})
    return {'embeds': [embed]}


def cmd_serverinfo() -> dict:
    guild = _guild()
    if not guild:
        return {'content': '⚠️ Server not loaded yet.'}
    created = int(guild.created_at.timestamp())
    embed = {
        'title': f'🏠 {guild.name}',
        'thumbnail': {'url': guild.icon.url if guild.icon else None},
        'color': 0x0ea5e9,
        'fields': [
            {'name': 'Members', 'value': str(guild.member_count), 'inline': True},
            {'name': 'Created', 'value': f'<t:{created}:R>', 'inline': True},
            {'name': 'Channels', 'value': str(len(guild.channels)), 'inline': True},
            {'name': 'Roles', 'value': str(len(guild.roles)), 'inline': True},
            {'name': 'Boosts', 'value': f'💎 {guild.premium_subscription_count}', 'inline': True},
            {'name': 'Owner', 'value': f'<@{guild.owner_id}>', 'inline': True},
        ],
    }
    return {'embeds': [embed]}


def cmd_automod(args: dict, sub: str = '') -> dict:
    am = _amod()
    if sub == 'toggle':
        am['enabled'] = not am.get('enabled', True)
        _save()
        return {'content': f"🛡️ AutoMod is now **{'ON' if am['enabled'] else 'OFF'}**."}
    if sub == 'addword':
        word = (args.get('word') or '').strip().lower()
        if not word:
            return {'content': '⚠️ Provide a word to filter.'}
        if word not in am['words']:
            am['words'].append(word)
            _save()
        return {'content': f'🛡️ Added `{word}` to the banned-words filter ({len(am["words"])} total).'}
    if sub == 'removeword':
        word = (args.get('word') or '').strip().lower()
        if word in am['words']:
            am['words'].remove(word)
            _save()
            return {'content': f'✅ Removed `{word}` from the filter.'}
        return {'content': f'⚠️ `{word}` is not in the filter.'}
    if sub == 'wordlist':
        return {'content': f"🛡️ Banned words: {', '.join(f'`{w}`' for w in am['words']) or '*(none)*'}"}
    status = 'ON' if am.get('enabled') else 'OFF'
    return {'content': (f"🛡️ **AutoMod** is **{status}**\n"
                        f"• Invite links: {'blocked' if am.get('block_invites') else 'allowed'}\n"
                        f"• Excessive caps: {'blocked' if am.get('block_caps') else 'allowed'}\n"
                        f"• Anti-spam: 8 messages / 8s → timeout\n"
                        f"• Banned words: {len(am.get('words', []))}\n"
                        f"Use `/automod toggle`, `/automod addword word:<text>` to configure.")}


async def cmd_welcome(args: dict, sub: str) -> dict:
    am = _amod()
    if sub == 'channel':
        am['welcome_channel'] = str(args.get('channel'))
        _save()
        return {'content': f"👋 Welcome messages will be sent to <#{am['welcome_channel']}>."}
    if sub == 'message':
        am['welcome_message'] = (args.get('message') or '').strip()[:500]
        _save()
        return {'content': f"✅ Welcome message set to:\n{am['welcome_message']}"}
    if sub == 'goodbye':
        gid = args.get('channel')
        am['goodbye_channel'] = str(gid) if gid else None
        _save()
        return {'content': f"👋 Goodbye messages {'enabled in <#' + str(gid) + '>' if gid else 'disabled'}."}
    if sub == 'test':
        member = await _member_from_id(args.get('_invoker', ''))
        if member:
            await on_member_join(member)
        return {'content': '✅ Test welcome sent (if a welcome channel is set).'}
    return {'content': 'Use `/welcome channel`, `/welcome message`, `/welcome goodbye`, or `/welcome test`.'}


async def cmd_autorole(arg: str) -> dict:
    role_id = _member_from_mention(arg) if arg else None
    _amod()['autorole'] = role_id
    _save()
    return {'content': f"✨ Auto-role {'set to <@' + role_id + '>' if role_id else 'disabled'}."}


def cmd_reminders(user_id: str) -> dict:
    mine = [r for r in _data['reminders'] if r['user_id'] == user_id]
    if not mine:
        return {'content': '📭 You have no active reminders. Use `/remind` to create one.'}
    lines = [f"`{i+1}.` <t:{int(r['at'])}:R> — {r['text']}" for i, r in enumerate(mine[:10])]
    return {'content': '⏰ **Your reminders**\n' + '\n'.join(lines)}


EIGHT_BALL = [
    'It is certain.', 'Without a doubt.', 'Yes, definitely.', 'Signs point to yes.',
    'Most likely.', 'Ask again later.', 'Better not tell you now.', 'Cannot predict now.',
    'Don\'t count on it.', 'My reply is no.', 'Very doubtful.', 'Absolutely not.',
    'Nah, but believe in yourself anyway.', 'Yes… but also no.',
]


def cmd_8ball(question: str) -> dict:
    if not question:
        return {'content': '🎱 Ask me a question! e.g. `!8ball will I win today?`'}
    return {'content': f'🎱 **{question}**\n>>> {random.choice(EIGHT_BALL)}'}


def cmd_roll(arg: str) -> dict:
    m = re.fullmatch(r'(\d*)d(\d+)', (arg or '').strip().lower())
    sides = int(m.group(2)) if m and 2 <= int(m.group(2)) <= 1000 else 6
    count = min(int(m.group(1)) if m and m.group(1) else 1, 20)
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = f' (total **{sum(rolls)}**)' if count > 1 else ''
    return {'content': f'🎲 {", ".join(map(str, rolls))}{total}'}


def cmd_flip() -> dict:
    return {'content': f'🪙 It\'s **{random.choice(["Heads", "Tails"])}**!'}


def cmd_choose(arg: str) -> dict:
    options = [o.strip() for o in re.split(r',|\bor\b|\|', arg or '') if o.strip()]
    if len(options) < 2:
        return {'content': '🤔 Give me at least two options separated by commas.'}
    return {'content': f'🤖 I choose **{random.choice(options)}**!'}


def cmd_help() -> dict:
    return {'content': (
        '🌊 **RippleBot Commands**\n'
        '**Music:** `!play <song|Spotify link>` `!skip` `!queue` `!np` `!volume` `!loop` `!shuffle` `!remove <n>` `!playtop` `!search` `!join` `!leave`\n'
        '**AI:** `!imagine <prompt>` `!ask <question>` `!tr <text>` (or reply `to spanish`)\n'
        '**Leveling:** `!rank` `!lb`\n'
        '**Moderation:** `!warn` `!warnings` `!kick` `!ban` `!unban` `!mute <min>` `!unmute` `!clear <n>` `!slowmode <s>` `!lock` `!unlock` `!automod`\n'
        '**Utility:** `!remind 10m <text>` `!reminders` `!poll <question>` `!welcome` `!autorole <role>`\n'
        '**Fun:** `!8ball` `!roll` `!flip` `!choose a, b` `!avatar` `!user` `!server`\n'
        '*Slash commands (/) work for everything too.*')}


# ---------------------------------------------------------------------------
# Dispatch: slash commands
# ---------------------------------------------------------------------------

PERM_BAN = str(1 << 2)
PERM_KICK = str(1 << 1)
PERM_MOD = str(1 << 40)     # ModerateMembers
PERM_MANAGE_MSGS = str(1 << 13)
PERM_MANAGE_GUILD = str(1 << 5)
PERM_MANAGE_CHANNELS = str(1 << 4)

SLASH_COMMANDS = [
    {'name': 'warn', 'description': 'Warn a member', 'default_member_permissions': PERM_MOD,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member to warn', 'required': True},
                 {'type': 3, 'name': 'reason', 'description': 'Reason', 'required': False}]},
    {'name': 'warnings', 'description': 'Show warnings for a member', 'default_member_permissions': PERM_MANAGE_MSGS,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member (blank = yourself)', 'required': False}]},
    {'name': 'kick', 'description': 'Kick a member', 'default_member_permissions': PERM_KICK,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member to kick', 'required': True},
                 {'type': 3, 'name': 'reason', 'description': 'Reason', 'required': False}]},
    {'name': 'ban', 'description': 'Ban a member', 'default_member_permissions': PERM_BAN,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member to ban', 'required': True},
                 {'type': 3, 'name': 'reason', 'description': 'Reason', 'required': False}]},
    {'name': 'unban', 'description': 'Unban a user by ID', 'default_member_permissions': PERM_BAN,
     'options': [{'type': 3, 'name': 'user_id', 'description': 'User ID to unban', 'required': True}]},
    {'name': 'timeout', 'description': 'Timeout (mute) a member', 'default_member_permissions': PERM_MOD,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member to mute', 'required': True},
                 {'type': 4, 'name': 'minutes', 'description': 'Minutes (default 10)', 'required': False},
                 {'type': 3, 'name': 'reason', 'description': 'Reason', 'required': False}]},
    {'name': 'untimeout', 'description': 'Remove a timeout', 'default_member_permissions': PERM_MOD,
     'options': [{'type': 6, 'name': 'user', 'description': 'Member to unmute', 'required': True}]},
    {'name': 'slowmode', 'description': 'Set channel slowmode', 'default_member_permissions': PERM_MANAGE_CHANNELS,
     'options': [{'type': 4, 'name': 'seconds', 'description': 'Seconds (0 to disable)', 'required': True}]},
    {'name': 'lock', 'description': 'Lock the current channel', 'default_member_permissions': PERM_MANAGE_CHANNELS},
    {'name': 'unlock', 'description': 'Unlock the current channel', 'default_member_permissions': PERM_MANAGE_CHANNELS},
    {'name': 'rank', 'description': 'Show your (or another member\'s) XP rank',
     'options': [{'type': 6, 'name': 'user', 'description': 'Member (blank = yourself)', 'required': False}]},
    {'name': 'leaderboard', 'description': 'Show the server XP leaderboard'},
    {'name': 'remind', 'description': 'Set a reminder',
     'options': [{'type': 3, 'name': 'time', 'description': 'When, e.g. 10m, 1h30m, 2d', 'required': True},
                 {'type': 3, 'name': 'text', 'description': 'What to remind you about', 'required': True}]},
    {'name': 'reminders', 'description': 'List your active reminders'},
    {'name': 'poll', 'description': 'Create a reaction poll',
     'options': [{'type': 3, 'name': 'question', 'description': 'Poll question', 'required': True}]},
    {'name': '8ball', 'description': 'Ask the magic 8-ball',
     'options': [{'type': 3, 'name': 'question', 'description': 'Your question', 'required': True}]},
    {'name': 'roll', 'description': 'Roll dice (e.g. d20 or 3d6)',
     'options': [{'type': 3, 'name': 'dice', 'description': 'Dice notation (default d6)', 'required': False}]},
    {'name': 'flip', 'description': 'Flip a coin'},
    {'name': 'choose', 'description': 'Let the bot choose for you',
     'options': [{'type': 3, 'name': 'options', 'description': 'Comma-separated options', 'required': True}]},
    {'name': 'avatar', 'description': 'Show a member\'s avatar',
     'options': [{'type': 6, 'name': 'user', 'description': 'Member (blank = yourself)', 'required': False}]},
    {'name': 'userinfo', 'description': 'Show info about a member',
     'options': [{'type': 6, 'name': 'user', 'description': 'Member (blank = yourself)', 'required': False}]},
    {'name': 'serverinfo', 'description': 'Show info about this server'},
    {'name': 'automod', 'description': 'Configure AutoMod', 'default_member_permissions': PERM_MANAGE_GUILD,
     'options': [{'type': 1, 'name': 'status', 'description': 'Show AutoMod settings'},
                 {'type': 1, 'name': 'toggle', 'description': 'Toggle AutoMod on/off'},
                 {'type': 1, 'name': 'addword', 'description': 'Add a banned word',
                  'options': [{'type': 3, 'name': 'word', 'description': 'Word to ban', 'required': True}]},
                 {'type': 1, 'name': 'removeword', 'description': 'Remove a banned word',
                  'options': [{'type': 3, 'name': 'word', 'description': 'Word to allow again', 'required': True}]},
                 {'type': 1, 'name': 'wordlist', 'description': 'List banned words'}]},
    {'name': 'welcome', 'description': 'Configure welcome messages', 'default_member_permissions': PERM_MANAGE_GUILD,
     'options': [{'type': 1, 'name': 'channel', 'description': 'Set the welcome channel',
                  'options': [{'type': 7, 'name': 'channel', 'description': 'Text channel', 'required': True}]},
                 {'type': 1, 'name': 'message', 'description': 'Set the welcome message ({user} {server} {count})',
                  'options': [{'type': 3, 'name': 'message', 'description': 'Message text', 'required': True}]},
                 {'type': 1, 'name': 'goodbye', 'description': 'Set the goodbye channel (blank to disable)',
                  'options': [{'type': 7, 'name': 'channel', 'description': 'Text channel (blank = disable)', 'required': False}]},
                 {'type': 1, 'name': 'test', 'description': 'Send a test welcome'}]},
    {'name': 'autorole', 'description': 'Role automatically given to new members', 'default_member_permissions': PERM_MANAGE_GUILD,
     'options': [{'type': 8, 'name': 'role', 'description': 'Role (leave blank to disable)', 'required': False}]},
    {'name': 'help', 'description': 'Show everything RippleBot can do'},
]


async def handle_slash(interaction: discord.Interaction, cname: str, options: dict) -> Optional[dict]:
    """Handles an extras slash command. Returns the payload to edit the original with."""
    user_id = str(interaction.user.id)
    user_name = interaction.user.display_name or interaction.user.name
    channel_id = str(interaction.channel_id)
    perms = interaction.permissions

    def need(permitted: bool) -> Optional[dict]:
        if not permitted:
            return {'content': '⛔ You don\'t have permission to use this command.'}
        return None

    if cname == 'warn':
        denied = need(perms.moderate_members)
        if denied:
            return denied
        return await cmd_warn(str(options['user']), options.get('reason') or '', user_name)

    if cname == 'warnings':
        target = str(options['user']) if options.get('user') else user_id
        if target != user_id and not perms.manage_messages:
            return {'content': '⛔ You can only check your own warnings.'}
        return _fmt_warnings(target)

    if cname == 'kick':
        denied = need(perms.kick_members)
        if denied:
            return denied
        return await cmd_kick(str(options['user']), options.get('reason') or '', user_name)

    if cname == 'ban':
        denied = need(perms.ban_members)
        if denied:
            return denied
        return await cmd_ban(str(options['user']), options.get('reason') or '', user_name)

    if cname == 'unban':
        denied = need(perms.ban_members)
        if denied:
            return denied
        return await cmd_unban((options.get('user_id') or '').strip())

    if cname == 'timeout':
        denied = need(perms.moderate_members)
        if denied:
            return denied
        minutes = int(options.get('minutes') or 10)
        return await cmd_timeout(str(options['user']), max(1, min(40320, minutes)), options.get('reason') or '', user_name)

    if cname == 'untimeout':
        denied = need(perms.moderate_members)
        if denied:
            return denied
        return await cmd_untimeout(str(options['user']))

    if cname == 'slowmode':
        denied = need(perms.manage_channels)
        if denied:
            return denied
        return await cmd_slowmode(channel_id, int(options.get('seconds') or 0))

    if cname == 'lock':
        denied = need(perms.manage_channels)
        if denied:
            return denied
        return await cmd_lock(channel_id)

    if cname == 'unlock':
        denied = need(perms.manage_channels)
        if denied:
            return denied
        return await cmd_unlock(channel_id)

    if cname == 'rank':
        return cmd_rank(str(options['user']) if options.get('user') else user_id)

    if cname == 'leaderboard':
        return cmd_leaderboard()

    if cname == 'remind':
        delay = parse_duration(options.get('time') or '')
        if not delay:
            return {'content': '⚠️ Invalid time. Use e.g. `10m`, `1h30m`, `2d`.'}
        await add_reminder(user_id, user_name, channel_id, delay, options.get('text') or '')
        return {'content': f'⏰ Got it! I\'ll remind you <t:{int(time.time()) + delay}:R>.'}

    if cname == 'reminders':
        return cmd_reminders(user_id)

    if cname == 'poll':
        question = (options.get('question') or '').strip()
        if not question:
            return {'content': '⚠️ Provide a poll question.'}
        message = await interaction.channel.send(f'📊 **{question}**\n*by {user_name}*')
        for emoji in ('👍', '👎', '🤷'):
            await message.add_reaction(emoji)
        return {'content': f'📊 Poll created: **{question}** — vote above!'}

    if cname == '8ball':
        return cmd_8ball(options.get('question') or '')

    if cname == 'roll':
        return cmd_roll(options.get('dice') or '')

    if cname == 'flip':
        return cmd_flip()

    if cname == 'choose':
        return cmd_choose(options.get('options') or '')

    if cname == 'avatar':
        member = interaction.guild.get_member(int(options['user'])) if options.get('user') else interaction.user
        if not member:
            return {'content': '⚠️ Could not find that member.'}
        return {'embeds': [{'title': f'🖼️ {member.display_name}\'s avatar',
                            'image': {'url': member.display_avatar.url}, 'color': 0x0ea5e9}]}

    if cname == 'userinfo':
        return cmd_userinfo(str(options['user']) if options.get('user') else user_id)

    if cname == 'serverinfo':
        return cmd_serverinfo()

    if cname == 'automod':
        sub = options.get('_sub') or 'status'
        return cmd_automod(options, sub)

    if cname == 'welcome':
        args = dict(options)
        args['_invoker'] = user_id
        return await cmd_welcome(args, options.get('_sub') or 'status')

    if cname == 'autorole':
        role_id = str(options['role']) if options.get('role') else None
        _amod()['autorole'] = role_id
        _save()
        return {'content': f"✨ Auto-role {'set to <@' + role_id + '>' if role_id else 'disabled'}."}

    if cname == 'help':
        return cmd_help()

    return None


# ---------------------------------------------------------------------------
# Dispatch: prefix commands
# ---------------------------------------------------------------------------

PREFIX_COMMANDS = {
    'warn', 'warnings', 'clearwarnings', 'kick', 'ban', 'unban', 'mute', 'untimeout', 'unmute',
    'clear', 'slowmode', 'lock', 'unlock', 'rank', 'lb', 'leaderboard', 'remind', 'remindme',
    'reminders', 'poll', '8ball', 'roll', 'flip', 'coinflip', 'choose', 'avatar', 'user',
    'userinfo', 'server', 'serverinfo', 'automod', 'welcome', 'autorole', 'help', 'commands', 'h',
}

_ALIASES = {
    'h': 'help', 'commands': 'help', 'remindme': 'remind', 'coinflip': 'flip',
    'lb': 'leaderboard', 'user': 'userinfo', 'server': 'serverinfo',
    'mute': 'timeout', 'unmute': 'untimeout',
}


async def handle_prefix(content: str, author_id: str, author_name: str, channel_id: str,
                        msg_id: str, member: Optional[discord.Member], channel) -> Optional[dict]:
    """Runs a ! prefix command. Returns the payload to post, or None if not an extras command."""
    m = re.match(r'^[!?](\w+)(?:\s+(.*))?$', content or '', re.DOTALL)
    if not m:
        return None
    cmd = m.group(1).lower()
    cmd = _ALIASES.get(cmd, cmd)
    if cmd not in PREFIX_COMMANDS:
        return None
    arg = (m.group(2) or '').strip()

    guild_perms = member.guild_permissions if member else None

    def need(check: bool) -> Optional[dict]:
        if not check:
            return {'content': '⛔ You don\'t have permission to use this command.'}
        return None

    if cmd == 'help':
        return cmd_help()

    if cmd == 'warn':
        target = _member_from_mention(arg.split()[0]) if arg else None
        if not target:
            return {'content': '⚠️ Usage: `!warn @user reason`'}
        denied = need(guild_perms and guild_perms.moderate_members)
        if denied:
            return denied
        reason = re.sub(r'<@!?\d+>\s*', '', arg).strip()
        return await cmd_warn(target, reason, author_name)

    if cmd in ('warnings', 'clearwarnings'):
        target = _member_from_mention(arg)
        if target and cmd == 'warnings':
            denied = need(guild_perms and guild_perms.manage_messages)
            if denied:
                return denied
            return _fmt_warnings(target)
        if target and cmd == 'clearwarnings':
            denied = need(guild_perms and guild_perms.manage_messages)
            if denied:
                return denied
            _data['warnings'].pop(target, None)
            _save()
            return {'content': f'✅ Cleared warnings for **{_user_display(target)}**.'}
        return _fmt_warnings(author_id)

    if cmd in ('kick', 'ban'):
        target = _member_from_mention(arg.split()[0]) if arg else None
        if not target:
            return {'content': f'⚠️ Usage: `!{cmd} @user reason`'}
        denied = need(guild_perms and (guild_perms.kick_members if cmd == 'kick' else guild_perms.ban_members))
        if denied:
            return denied
        reason = re.sub(r'<@!?\d+>\s*', '', arg).strip()
        return await (cmd_kick if cmd == 'kick' else cmd_ban)(target, reason, author_name)

    if cmd == 'unban':
        denied = need(guild_perms and guild_perms.ban_members)
        if denied:
            return denied
        return await cmd_unban(arg.split()[0] if arg else '')

    if cmd in ('timeout', 'untimeout'):
        denied = need(guild_perms and guild_perms.moderate_members)
        if denied:
            return denied
        if cmd == 'untimeout':
            target = _member_from_mention(arg.split()[0]) if arg else None
            return await cmd_untimeout(target) if target else {'content': '⚠️ Usage: `!unmute @user`'}
        parts = arg.split(maxsplit=1)
        target = _member_from_mention(parts[0]) if parts else None
        if not target:
            return {'content': '⚠️ Usage: `!mute @user [minutes] [reason]`'}
        minutes = 10
        reason = ''
        if len(parts) > 1:
            tail = parts[1].split(maxsplit=1)
            if tail and tail[0].isdigit():
                minutes = int(tail[0])
                reason = tail[1] if len(tail) > 1 else ''
            else:
                reason = parts[1]
        return await cmd_timeout(target, max(1, min(40320, minutes)), reason, author_name)

    if cmd == 'clear':
        if not (guild_perms and guild_perms.manage_messages):
            return {'content': '⛔ You need **Manage Messages** to use this.'}
        amount = int(re.match(r'^(\d+)', arg).group(1)) if re.match(r'^\d+', arg) else 5
        return await cmd_clear(channel_id, amount, author_name)

    if cmd == 'slowmode':
        denied = need(guild_perms and guild_perms.manage_channels)
        if denied:
            return denied
        return await cmd_slowmode(channel_id, int(arg) if arg.isdigit() else 0)

    if cmd in ('lock', 'unlock'):
        denied = need(guild_perms and guild_perms.manage_channels)
        if denied:
            return denied
        return await (cmd_lock if cmd == 'lock' else cmd_unlock)(channel_id)

    if cmd == 'rank':
        target = _member_from_mention(arg) or author_id
        return cmd_rank(target)

    if cmd in ('leaderboard',):
        return cmd_leaderboard()

    if cmd in ('remind',):
        dm = re.match(r'^(\S+)\s+(.*)$', arg, re.DOTALL)
        if not dm:
            return {'content': '⚠️ Usage: `!remind 10m take out the trash`'}
        delay = parse_duration(dm.group(1))
        if not delay:
            return {'content': '⚠️ Invalid time. Use e.g. `10m`, `1h30m`, `2d`.'}
        await add_reminder(author_id, author_name, channel_id, delay, dm.group(2))
        return {'content': f'⏰ Got it! I\'ll remind you <t:{int(time.time()) + delay}:R>.'}

    if cmd == 'reminders':
        return cmd_reminders(author_id)

    if cmd == 'poll':
        if not arg:
            return {'content': '⚠️ Usage: `!poll Should we play games tonight?`'}
        return {'__poll__': arg}

    if cmd == '8ball':
        return cmd_8ball(arg)

    if cmd == 'roll':
        return cmd_roll(arg)

    if cmd == 'flip':
        return cmd_flip()

    if cmd == 'choose':
        return cmd_choose(arg)

    if cmd == 'avatar':
        target = _member_from_mention(arg) or author_id
        member_obj = await _member_from_id(target)
        if not member_obj:
            return {'content': '⚠️ Could not find that member.'}
        return {'embeds': [{'title': f"🖼️ {member_obj.display_name}'s avatar",
                            'image': {'url': member_obj.display_avatar.url}, 'color': 0x0ea5e9}]}

    if cmd == 'userinfo':
        return cmd_userinfo(_member_from_mention(arg) or author_id)

    if cmd == 'serverinfo':
        return cmd_serverinfo()

    if cmd == 'automod':
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else 'status'
        if sub not in ('status', 'toggle', 'addword', 'removeword', 'wordlist'):
            sub = 'status'
        if sub in ('addword', 'removeword', 'toggle'):
            denied = need(guild_perms and guild_perms.manage_guild)
            if denied:
                return denied
        return cmd_automod({'word': parts[1] if len(parts) > 1 else ''}, sub)

    if cmd == 'welcome':
        denied = need(guild_perms and guild_perms.manage_guild)
        if denied:
            return denied
        return {'content': '👋 Prefix welcome setup is limited — use `/welcome channel`, `/welcome message`, `/welcome goodbye`.'}

    if cmd == 'autorole':
        denied = need(guild_perms and guild_perms.manage_guild)
        if denied:
            return denied
        return await cmd_autorole(arg)

    return None
