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
import re
import sys
import os
import uuid

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
BASE_URL = 'https://discord.com/api/v10'

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

HEADERS = {
    'Authorization': f'Bot {TOKEN}',
    'User-Agent': 'DiscordBot (RippleBot, 1.0)',
    'Content-Type': 'application/json'
}

_webhook_cache = {}

def api_call(endpoint, method='GET', data=None):
    url = f'{BASE_URL}{endpoint}'
    payload = json.dumps(data).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method=method)
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            return None
        return json.loads(resp.read().decode('utf-8'))

tracker = meeting_tracker.MeetingTracker(BOT_ID, api_call)
_active_gateway_ws = None
_guild_voice_states = {}  # uid -> {'channel_id': cid, 'username': str, 'display_name': str}

async def send_voice_state_update(guild_id: str, channel_id: str = None, self_mute: bool = False, self_deaf: bool = True):
    global _active_gateway_ws
    if _active_gateway_ws:
        payload = {
            'op': 4,
            'd': {
                'guild_id': guild_id,
                'channel_id': channel_id,
                'self_mute': self_mute,
                'self_deaf': self_deaf
            }
        }
        await _active_gateway_ws.send(json.dumps(payload))
        print(f"[VoiceGateway] Opcode 4 sent: channel_id={channel_id}")

async def meeting_monitor_loop():
    while True:
        await asyncio.sleep(10)
        try:
            if tracker.check_auto_end(grace_period_secs=60):
                print("[MeetingTracker] Auto-ending meeting after 60s of empty VC...")
                await send_voice_state_update(GUILD_ID, None)
                ok, embed, summary = tracker.end_meeting()
                if ok and embed:
                    api_call(
                        f'/channels/{tracker.reports_channel_id}/messages',
                        method='POST',
                        data={
                            'content': "⚠️ *Founders meeting ended automatically after 60 seconds of empty VC.*",
                            'embeds': [embed]
                        }
                    )
        except Exception as e:
            print(f"[MeetingMonitor] Error: {e}")

def send_channel_file(channel_id, file_bytes, filename="image.jpg", payload=None):
    boundary = "----DiscordBotBoundary" + uuid.uuid4().hex
    body = bytearray()
    if payload:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="payload_json"\r\n')
        body.extend(b'Content-Type: application/json\r\n\r\n')
        body.extend(json.dumps(payload).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"{BASE_URL}/channels/{channel_id}/messages"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "User-Agent": "DiscordBot (RippleBot, 1.0)",
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error sending channel file: {e}")
        return None

def interaction_edit_original_file(i_token, file_bytes, filename="image.jpg", payload=None):
    boundary = "----DiscordBotBoundary" + uuid.uuid4().hex
    body = bytearray()
    if payload:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="payload_json"\r\n')
        body.extend(b'Content-Type: application/json\r\n\r\n')
        body.extend(json.dumps(payload).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"{BASE_URL}/webhooks/{BOT_ID}/{i_token}/messages/@original"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "DiscordBot (RippleBot, 1.0)",
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        },
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return True
    except Exception as e:
        print(f"[Interaction] Edit original file error: {e}")
        return False

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

def modify_role(user_id, role_id, action='PUT'):
    url = f'/guilds/{GUILD_ID}/members/{user_id}/roles/{role_id}'
    try:
        api_call(url, method=action)
        print(f"[{action}] Role {role_id} for user {user_id}")
    except Exception as e:
        print(f"Error {action} role {role_id} for user {user_id}: {e}")

def get_channel_webhook(channel_id):
    if channel_id in _webhook_cache:
        return _webhook_cache[channel_id]

    try:
        webhooks = api_call(f'/channels/{channel_id}/webhooks')
        for w in webhooks:
            if w.get('name') == 'RippleProxy' and w.get('token'):
                _webhook_cache[channel_id] = (w['id'], w['token'])
                return _webhook_cache[channel_id]

        new_wh = api_call(f'/channels/{channel_id}/webhooks', method='POST', data={'name': 'RippleProxy'})
        _webhook_cache[channel_id] = (new_wh['id'], new_wh['token'])
        return _webhook_cache[channel_id]
    except Exception as e:
        print(f"Error retrieving/creating webhook for channel {channel_id}: {e}")
        return None

async def send_heartbeat(ws, interval):
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(json.dumps({'op': 1, 'd': None}))
        except Exception:
            break

def interaction_callback(i_id, i_token, payload):
    url = f'{BASE_URL}/interactions/{i_id}/{i_token}/callback'
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (RippleBot, 1.0)'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return True
    except Exception as e:
        print(f"[Interaction] Callback error: {e}")
        return False

def interaction_edit_original(i_token, payload):
    url = f'{BASE_URL}/webhooks/{BOT_ID}/{i_token}/messages/@original'
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (RippleBot, 1.0)'},
        method='PATCH'
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return True
    except Exception as e:
        print(f"[Interaction] Edit original error: {e}")
        return False

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
            ok = interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            if not ok:
                return

            p_ok, p_msg = rate_limiter.check_purge_cooldown(channel_id)
            if not p_ok:
                interaction_edit_original(i_token, {'content': p_msg})
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            amount = min(max(int(options.get('amount', 5)), 1), 100)

            try:
                msgs = api_call(f'/channels/{channel_id}/messages?limit={amount}')
                if msgs:
                    msg_ids = [m['id'] for m in msgs]
                    if len(msg_ids) == 1:
                        api_call(f'/channels/{channel_id}/messages/{msg_ids[0]}', method='DELETE')
                    else:
                        api_call(f'/channels/{channel_id}/messages/bulk-delete', method='POST', data={'messages': msg_ids})
                    interaction_edit_original(i_token, {'content': f"🧹 Purged **{len(msg_ids)}** messages from this channel!"})
                else:
                    interaction_edit_original(i_token, {'content': "No messages found to purge."})
            except Exception as e:
                interaction_edit_original(i_token, {'content': f"⚠️ Error purging messages: {e}"})
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
            interaction_callback(i_id, i_token, {
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
                interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': limit_msg, 'flags': 64}})
                return

            ok = interaction_callback(i_id, i_token, {'type': 5})
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
                interaction_edit_original_file(i_token, img_bytes, filename="generated.jpg", payload=payload)
            except Exception as e:
                interaction_edit_original(i_token, {'content': f"⚠️ Failed to generate image: {e}"})
            return

        # Rate limit check for text/AI commands
        if cname in ('translate', 'speak', 'Translate to English', 'ask'):
            allowed, limit_msg = rate_limiter.check_rate_limit(user_id)
            if not allowed:
                interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': limit_msg, 'flags': 64}})
                return

        # 4. /translate
        if cname == 'translate':
            ok = interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            text_to_translate = options.get('text', '').strip()
            target_input = options.get('to', 'en')
            source_input = options.get('from', 'auto')

            if not text_to_translate:
                interaction_edit_original(i_token, {'content': "Provide text to translate: `/translate text: <text> to: <lang>`"})
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
            interaction_edit_original(i_token, {'embeds': [embed]})
            return

        # 5. /speak
        if cname == 'speak':
            ok = interaction_callback(i_id, i_token, {'type': 5, 'data': {'flags': 64}})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            lang_input = options.get('language', 'es')
            raw_message = options.get('message', '')

            tgt_code, tgt_name = resolve_lang(lang_input)
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(None, do_translate, raw_message, tgt_code, 'auto')

            wh_info = get_channel_webhook(channel_id)
            if wh_info:
                wh_id, wh_token = wh_info
                wh_url = f'{BASE_URL}/webhooks/{wh_id}/{wh_token}'
                wh_payload = {
                    'content': translated,
                    'username': f"{user_name} ({tgt_name})",
                    'avatar_url': user_avatar
                }
                req = urllib.request.Request(
                    wh_url,
                    data=json.dumps(wh_payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (RippleBot, 1.0)'},
                    method='POST'
                )
                try:
                    urllib.request.urlopen(req)
                except Exception as e:
                    print(f"Webhook error: {e}")

                interaction_edit_original(i_token, {'content': f"✅ Sent in **{tgt_name}**!"})
            else:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f"**{user_name}** ({tgt_name}): {translated}"
                })
                interaction_edit_original(i_token, {'content': f"✅ Sent!"})
            return

        # 6. Translate to English (Context Menu)
        if cname == 'Translate to English':
            ok = interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            resolved = data.get('resolved', {})
            messages = resolved.get('messages', {})
            target_id = data.get('target_id')
            target_msg = messages.get(target_id, {})
            text_to_translate = target_msg.get('content', '')
            author_name = target_msg.get('author', {}).get('username', 'Original Author')

            if not text_to_translate:
                interaction_edit_original(i_token, {'content': "No text found to translate."})
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
            interaction_edit_original(i_token, {'embeds': [embed]})
            return

        # 7. /ask
        if cname == 'ask':
            ok = interaction_callback(i_id, i_token, {'type': 5})
            if not ok:
                return

            options = {opt['name']: opt['value'] for opt in data.get('options', [])}
            question = options.get('question', '').strip()

            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, groq_engine.groq_water_chat, question, user_name)

            chunks = split_discord_chunks(answer, max_len=1900)
            if chunks:
                interaction_edit_original(i_token, {'content': chunks[0]})
                for ch in chunks[1:]:
                    api_call(f'/channels/{channel_id}/messages', method='POST', data={'content': ch})
            return

        # 8. /meeting
        if cname == 'meeting':
            options = data.get('options', [])
            subcmd = options[0].get('name') if options else 'status'

            if subcmd == 'start':
                current_in_vc = [
                    {'user_id': uid, 'username': info['username'], 'display_name': info['display_name']}
                    for uid, info in _guild_voice_states.items()
                    if info.get('channel_id') == meeting_tracker.FOUNDERS_VC_ID and uid != BOT_ID
                ]
                ok, msg = tracker.start_meeting(user_name, current_in_vc)
                if ok:
                    await send_voice_state_update(GUILD_ID, meeting_tracker.FOUNDERS_VC_ID)
                interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': msg}})
                return

            elif subcmd == 'end':
                await send_voice_state_update(GUILD_ID, None)
                ok, embeds, summary = tracker.end_meeting()
                if ok and embeds:
                    api_call(f'/channels/{tracker.reports_channel_id}/messages', method='POST', data={'embeds': embeds})
                    interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': summary}})
                else:
                    interaction_callback(i_id, i_token, {'type': 4, 'data': {'content': summary}})
                return

            elif subcmd == 'status':
                embed = tracker.get_status_embed()
                interaction_callback(i_id, i_token, {'type': 4, 'data': {'embeds': [embed]}})
                return

            elif subcmd == 'stats':
                embed = tracker.get_stats_embed()
                interaction_callback(i_id, i_token, {'type': 4, 'data': {'embeds': [embed]}})
                return

    except Exception as e:
        print(f"[Error] in handle_interaction: {e}")

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

def send_discord_reply(channel_id: str, content: str, reply_to_id: str = None):
    chunks = split_discord_chunks(content, max_len=1900)
    for i, chunk in enumerate(chunks):
        data = {'content': chunk}
        if i == 0 and reply_to_id:
            data['message_reference'] = {'message_id': reply_to_id}
        api_call(f'/channels/{channel_id}/messages', method='POST', data=data)

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

        # 0. Meeting text commands: !meeting start/end/status/stats, !note, !startmeeting, !endmeeting
        m_meeting = re.match(r'^(?:!meeting\s+(start|join|end|stop|status|stats|leaderboard)|!startmeeting|!endmeeting|!note\s+(.*))', content, re.IGNORECASE)
        if m_meeting:
            raw_match = m_meeting.group(0).lower()
            if raw_match.startswith('!note'):
                note_text = m_meeting.group(2) or ''
                if note_text.strip():
                    if tracker.is_active:
                        tracker.add_transcript(author_name, note_text.strip())
                        api_call(f'/channels/{channel_id}/messages', method='POST', data={
                            'content': f"📝 *Note added to meeting brief by {author_name}*",
                            'message_reference': {'message_id': msg_id}
                        })
                    else:
                        api_call(f'/channels/{channel_id}/messages', method='POST', data={
                            'content': "⚠️ No meeting is currently active. Start one with `!meeting start` or `/meeting start`.",
                            'message_reference': {'message_id': msg_id}
                        })
                return

            cmd = (m_meeting.group(1) or '').lower()
            if not cmd:
                if '!startmeeting' in raw_match:
                    cmd = 'start'
                elif '!endmeeting' in raw_match:
                    cmd = 'end'
                else:
                    cmd = 'status'

            if cmd in ('start', 'join'):
                current_in_vc = [
                    {'user_id': uid, 'username': info['username'], 'display_name': info['display_name']}
                    for uid, info in _guild_voice_states.items()
                    if info.get('channel_id') == meeting_tracker.FOUNDERS_VC_ID and uid != BOT_ID
                ]
                ok, msg = tracker.start_meeting(author_name, current_in_vc)
                if ok:
                    await send_voice_state_update(GUILD_ID, meeting_tracker.FOUNDERS_VC_ID)
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            elif cmd in ('end', 'stop'):
                await send_voice_state_update(GUILD_ID, None)
                ok, embeds, summary = tracker.end_meeting()
                if ok and embeds:
                    api_call(f'/channels/{tracker.reports_channel_id}/messages', method='POST', data={'embeds': embeds})
                    api_call(f'/channels/{channel_id}/messages', method='POST', data={
                        'content': summary,
                        'message_reference': {'message_id': msg_id}
                    })
                else:
                    api_call(f'/channels/{channel_id}/messages', method='POST', data={
                        'content': summary,
                        'message_reference': {'message_id': msg_id}
                    })
                return

            elif cmd == 'status':
                embed = tracker.get_status_embed()
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
                return

            elif cmd in ('stats', 'leaderboard'):
                embed = tracker.get_stats_embed()
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
                return

        # 0.1 In-meeting live discussion & voice notes capture
        if tracker.is_active and channel_id in (meeting_tracker.FOUNDERS_VC_ID, '1545542223381274634'):
            # Check for audio attachments / voice messages
            for att in d.get('attachments') or []:
                fname = (att.get('filename') or '').lower()
                ctype = att.get('content_type') or ''
                if any(ext in fname for ext in ('.ogg', '.wav', '.mp3', '.m4a')) or 'audio/' in ctype:
                    try:
                        req_a = urllib.request.Request(att['url'], headers={'User-Agent': 'RippleBot/1.0'})
                        with urllib.request.urlopen(req_a, timeout=15) as ra:
                            a_bytes = ra.read()
                        transcribed = groq_engine.groq_transcribe_audio(a_bytes, fname)
                        if transcribed:
                            tracker.add_transcript(author_name, f"[Voice Note]: {transcribed}")
                            api_call(f'/channels/{channel_id}/messages', method='POST', data={
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
            p_ok, p_msg = rate_limiter.check_purge_cooldown(channel_id)
            if not p_ok:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': p_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            amount_str = m_purge.group(1)
            amount = min(max(int(amount_str) if amount_str else 5, 1), 100)

            try:
                api_call(f'/channels/{channel_id}/messages/{msg_id}', method='DELETE')
            except Exception:
                pass

            msgs = api_call(f'/channels/{channel_id}/messages?limit={amount}')
            if msgs:
                msg_ids = [m['id'] for m in msgs]
                if len(msg_ids) == 1:
                    api_call(f'/channels/{channel_id}/messages/{msg_ids[0]}', method='DELETE')
                else:
                    api_call(f'/channels/{channel_id}/messages/bulk-delete', method='POST', data={'messages': msg_ids})

                sent = api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': f"🧹 Purged **{len(msg_ids)}** messages!"
                })
                if sent and sent.get('id'):
                    async def auto_delete(c_id, m_id):
                        await asyncio.sleep(3)
                        try:
                            api_call(f'/channels/{c_id}/messages/{m_id}', method='DELETE')
                        except Exception:
                            pass
                    asyncio.create_task(auto_delete(channel_id, sent['id']))
            return

        # 2. Image generation command: !imagine <prompt>, !image <prompt>, !draw <prompt>, @RippleBot imagine/draw/generate image
        m_img = re.match(r'^(?:!(?:imagine|image|draw)|<@!?1546333781764345936>\s*(?:imagine|draw|image|generate\s+(?:an?\s+)?image(?:\s+of)?))\s+(.+)$', content, re.IGNORECASE | re.DOTALL)
        if m_img:
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': limit_msg,
                    'message_reference': {'message_id': msg_id}
                })
                return

            prompt = m_img.group(1).strip()
            try:
                api_call(f'/channels/{channel_id}/typing', method='POST')
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
                send_channel_file(channel_id, img_bytes, filename="generated.jpg", payload=payload)
            except Exception as e:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
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
                    api_call(f'/channels/{channel_id}/messages', method='POST', data={
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
                        m = api_call(f'/channels/{channel_id}/messages/{ref_id}')
                        target_text = m.get('content', '')
                        ref_author = m.get('author', {}).get('username', 'User')

                if not target_text:
                    api_call(f'/channels/{channel_id}/messages', method='POST', data={
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

                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
                return

        # 4. Direct Translation Command
        if is_tr_cmd or (is_bot_mentioned and any(w in content.lower() for w in ['translate ', 'translate\n', 'tr '])):
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
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
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'embeds': [embed],
                    'message_reference': {'message_id': msg_id}
                })
            return

        # 5. Vision & Conversational AI (@RippleBot or reply to RippleBot)
        if is_bot_mentioned or is_reply_to_bot:
            allowed, limit_msg = rate_limiter.check_rate_limit(author_id)
            if not allowed:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
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
                    m = api_call(f'/channels/{channel_id}/messages/{ref_id}')
                    if isinstance(m, dict):
                        image_url = extract_media_from_message(m)

            # Only scan immediate previous message if current prompt clearly refers to media
            media_triggers = ['this', 'look', 'see', 'who is', 'what is this', 'what is that', 'pic', 'photo', 'gif', 'image', 'meme', 'view', 'read']
            if not image_url and channel_id and (any(t in prompt_lower for t in media_triggers) or len(cleaned_prompt.split()) <= 3):
                try:
                    recent = api_call(f'/channels/{channel_id}/messages?limit=3')
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
                api_call(f'/channels/{channel_id}/typing', method='POST')
            except Exception:
                pass

            user_display = author.get('global_name') or author.get('username') or 'Friend'
            loop = asyncio.get_event_loop()

            # A. Vision flow
            if image_url:
                try:
                    req = urllib.request.Request(image_url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Connection': 'close'
                    })
                    with urllib.request.urlopen(req, timeout=12) as r:
                        img_bytes = r.read()
                    data_url = groq_engine.prepare_image_base64(img_bytes)
                    answer = await loop.run_in_executor(None, groq_engine.groq_vision_chat, cleaned_prompt, data_url, user_display)
                except Exception as e:
                    answer = f"⚠️ Couldn't process image/GIF: {e}"

                send_discord_reply(channel_id, answer, reply_to_id=msg_id)
                return

            # B. Empty mention
            if not cleaned_prompt:
                api_call(f'/channels/{channel_id}/messages', method='POST', data={
                    'content': "Yo! What's up? Ask me anything, generate images with `!imagine <prompt>`, or clean chat with `/purge`.",
                    'message_reference': {'message_id': msg_id}
                })
                return

            # C. Chat summarization
            if any(k in prompt_lower for k in ['summarize', 'summary', 'what i miss', 'what did i miss', 'recap', 'catch me up']):
                recent_msgs = api_call(f'/channels/{channel_id}/messages?limit=25')
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

            send_discord_reply(channel_id, answer, reply_to_id=msg_id)
            return

    except Exception as e:
        print(f"[Error] in handle_message: {e}")

async def start_health_server():
    try:
        from aiohttp import web
        async def handle_health(request):
            return web.Response(text="RippleBot is running healthy!", content_type="text/plain")
        base_port = int(os.environ.get('PORT', 8080))
        for p in [base_port, 8081, 8082, 0]:
            try:
                app = web.Application()
                app.router.add_get('/', handle_health)
                app.router.add_get('/health', handle_health)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, '0.0.0.0', p)
                await site.start()
                print(f"[HealthServer] Cloud healthcheck listening on port {p}")
                break
            except Exception:
                continue
    except Exception as e:
        print(f"[HealthServer] Notice: {e}")

async def run_bot():
    global _active_gateway_ws
    gateway_url = 'wss://gateway.discord.gg/?v=10&encoding=json'
    asyncio.create_task(meeting_monitor_loop())
    asyncio.create_task(start_health_server())

    while True:
        try:
            print("Connecting to Discord Gateway...")
            import websockets
            async with websockets.connect(gateway_url) as ws:
                _active_gateway_ws = ws
                hello = json.loads(await ws.recv())
                heartbeat_interval = hello['d']['heartbeat_interval'] / 1000.0
                print(f"Received HELLO. Heartbeat: {heartbeat_interval}s")

                asyncio.create_task(send_heartbeat(ws, heartbeat_interval))

                identify = {
                    'op': 2,
                    'd': {
                        'token': TOKEN,
                        'intents': 1665,  # 1537 | (1 << 7) for GUILD_VOICE_STATES
                        'properties': {
                            'os': 'windows',
                            'browser': 'ripplebot',
                            'device': 'ripplebot'
                        }
                    }
                }
                await ws.send(json.dumps(identify))
                print("Identified with Gateway. Active and listening for chat, vision, imagine, purge, rate limiting, translation, and meetings...")

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    t = data.get('t')
                    d = data.get('d')

                    if t == 'INTERACTION_CREATE':
                        asyncio.create_task(handle_interaction(d))
                    elif t == 'MESSAGE_CREATE':
                        asyncio.create_task(handle_message(d))
                    elif t == 'VOICE_STATE_UPDATE':
                        uid = d.get('user_id')
                        new_cid = d.get('channel_id')
                        old_info = _guild_voice_states.get(uid) or {}
                        old_cid = old_info.get('channel_id')
                        member = d.get('member') or {}
                        user = member.get('user') or {}
                        u_name = user.get('username', 'Member')
                        disp_name = member.get('nick') or user.get('global_name') or u_name

                        if new_cid:
                            _guild_voice_states[uid] = {
                                'channel_id': new_cid,
                                'username': u_name,
                                'display_name': disp_name
                            }
                        else:
                            _guild_voice_states.pop(uid, None)

                        tracker.on_voice_state_update(
                            user_id=uid,
                            username=u_name,
                            display_name=disp_name,
                            old_channel_id=old_cid,
                            new_channel_id=new_cid
                        )
                    elif t == 'MESSAGE_REACTION_ADD':
                        mid = d.get('message_id')
                        uid = d.get('user_id')
                        channel_id = d.get('channel_id')
                        emoji = d.get('emoji', {}).get('name')

                        if uid == BOT_ID:
                            continue

                        if mid in ROLE_MAP:
                            role_id = ROLE_MAP[mid].get(emoji)
                            if role_id:
                                print(f"Adding role {emoji} -> {role_id} for user {uid}")
                                modify_role(uid, role_id, 'PUT')
                            continue

                        if emoji in FLAG_TO_LANG:
                            tgt_code, tgt_name = FLAG_TO_LANG[emoji]
                            try:
                                msg_data = api_call(f'/channels/{channel_id}/messages/{mid}')
                                text = msg_data.get('content', '')
                                if text:
                                    translated = do_translate(text, tgt_code, 'auto')
                                    author_name = msg_data.get('author', {}).get('username', 'User')
                                    embed = {
                                        'description': f"**{emoji} {tgt_name} Translation** (by {author_name}):\n\n{translated}",
                                        'color': 0x0ea5e9,
                                        'footer': {'text': 'RippleBot Flag Translator'}
                                    }
                                    api_call(f'/channels/{channel_id}/messages', method='POST', data={
                                        'embeds': [embed],
                                        'message_reference': {'message_id': mid}
                                    })
                            except Exception as e:
                                print(f"Flag translation failed: {e}")

                    elif t == 'MESSAGE_REACTION_REMOVE':
                        mid = d.get('message_id')
                        uid = d.get('user_id')
                        emoji = d.get('emoji', {}).get('name')
                        if uid != BOT_ID and mid in ROLE_MAP:
                            role_id = ROLE_MAP[mid].get(emoji)
                            if role_id:
                                print(f"Removing role {emoji} -> {role_id} for user {uid}")
                                modify_role(uid, role_id, 'DELETE')

        except Exception as e:
            _active_gateway_ws = None
            print(f"Gateway connection lost: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

if __name__ == '__main__':
    print("=" * 60)
    print("             🌊 RIPPLEBOT SERVICE (AI-POWERED) 🌊")
    print("=" * 60)
    asyncio.run(run_bot())
