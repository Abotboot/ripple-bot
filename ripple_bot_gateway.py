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
import time
import re
import sys
import os
import io
import logging
from contextlib import closing
import discord
from voice_capture import MeetingRecorder

from deep_translator import GoogleTranslator, MyMemoryTranslator
import groq_engine
import water_knowledge
import rate_limiter
import image_gen
import meeting_tracker

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
if not TOKEN:
    token_file = os.path.join(os.path.dirname(__file__), "bot_token.txt")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r") as f:
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
                await interaction_edit_original(i_token, {'content': f"✅ Sent!"})
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
            answer = await loop.run_in_executor(None, groq_engine.groq_water_chat, question, user_name)

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
                await interaction_edit_original(i_token, {'content': 'Use meeting commands inside the Founders channels.'})
                return
            options = data.get('options', [])
            subcmd = options[0].get('name') if options else 'status'
            result = await meeting_command(subcmd, user_name)
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

        # Meeting commands share the same serialized lifecycle as slash commands.
        match = re.fullmatch(r'(?:!meeting|<@!?1546333781764345936>\s*meeting)(?:\s+(start|join|end|stop|status|stats|leaderboard))?|!(startmeeting|endmeeting)|!note\s+(.+)', content, re.IGNORECASE | re.DOTALL)
        if match:
            channel = client.get_channel(int(channel_id))
            guild = getattr(channel, 'guild', None)
            member = guild.get_member(int(author_id)) if guild else None
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
                result = await meeting_command(command.lower(), author_name)
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
                answer = await loop.run_in_executor(None, groq_engine.groq_water_chat, cleaned_prompt, user_display)

            if not answer:
                answer = "Hit a quick hiccup. Try asking again!"

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
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()

def message_options(payload):
    result = {}
    for key in ('content',):
        if key in payload:
            result[key] = payload[key]
    if 'embeds' in payload:
        result['embeds'] = [discord.Embed.from_dict(embed) for embed in payload['embeds']]
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
    channel = client.get_channel(int(channel_id)) if channel_id else None
    if not isinstance(member, discord.Member) or str(member.guild.id) != GUILD_ID:
        return False
    vc = client.get_channel(int(meeting_tracker.FOUNDERS_VC_ID))
    return bool(vc and channel and channel.category_id == int(meeting_tracker.FOUNDERS_CATEGORY_ID)
                and vc.permissions_for(member).view_channel and vc.permissions_for(member).connect)


async def meeting_command(command, username):
    global recorder
    async with _meeting_lock:
        if command in ('start', 'join'):
            if tracker.is_active:
                return {'content': 'A meeting is already active. Use /meeting status.'}
            channel = client.get_channel(int(meeting_tracker.FOUNDERS_VC_ID))
            if channel is None:
                return {'content': 'Founders VC is unavailable. Check the bot’s View Channel and Connect permissions.'}
            members = [{'user_id': str(m.id), 'username': m.name, 'display_name': m.display_name}
                       for m in channel.members if not m.bot]
            if not members:
                return {'content': 'Join Founders VC before starting a meeting.'}
            if not groq_engine.get_groq_key():
                return {'content': 'GROQ_API_KEY is missing; transcription cannot start.'}
            # Announce capture in the actual voice channel before receiving any audio.
            await channel.send('🎙️ Meeting recording is starting. Voice audio is sent to Groq for transcription; notes and summaries go to meeting-reports. Use /meeting end to stop.', allowed_mentions=discord.AllowedMentions.none())
            tracker.start_meeting(username, members)
            recorder = MeetingRecorder(tracker)
            try:
                await recorder.start(channel)
            except Exception as exc:
                tracker.is_active = False
                recorder = None
                logging.error('Voice connection failed: %s', type(exc).__name__)
                return {'content': 'Voice connection failed; recording did not start. Check View Channel/Connect permissions and host UDP access.'}
            return {'content': '🎙️ Meeting started. Attendance tracking and voice reception are active. /meeting status shows received audio and transcript counts.'}
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


class RippleClient(discord.Client):
    async def setup_hook(self):
        self.monitor = asyncio.create_task(meeting_monitor_loop())

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

    async def on_interaction(self, interaction):
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

    async def on_socket_response(self, payload):
        # Existing message handlers consume Discord's raw payload, after SDK parsing/caching.
        if payload.get('t') == 'MESSAGE_CREATE':
            await handle_message(payload['d'])

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


async def run_bot():
    if not TOKEN:
        raise RuntimeError('DISCORD_BOT_TOKEN is required')
    await start_health_server()
    async with client:
        await client.start(TOKEN)

if __name__ == '__main__':
    print(f"RippleBot SDK runtime {os.environ.get('RENDER_GIT_COMMIT', 'local')}", flush=True)
    print("=" * 60)
    print("             🌊 RIPPLEBOT SERVICE (AI-POWERED) 🌊")
    print("=" * 60)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())
