"""
Per-channel conversation memory for RippleBot.

Keeps a rolling buffer of what everyone says in each channel (including the
bot's own replies) so the Groq brain can hold a real conversation — follow-up
messages like "wait what about subaru" build on what was already said instead
of starting from zero every mention. Context is in-memory only, pruned by age
and count so prompts stay small and fast.
"""

import re
import time
from collections import defaultdict, deque

# Rolling buffer per channel (messages kept per channel).
MAX_MESSAGES = 40
# Context older than this is dropped even if the buffer is not full.
MAX_AGE_SECONDS = 45 * 60
# Per-message cap fed into the prompt (Groq prompt stays bounded).
MAX_SNIPPET_CHARS = 500
# How many recent exchanges get passed to the model.
HISTORY_LIMIT = 10

_MENTION_RE = re.compile(r'<@!?\d+>')

# channel_id -> deque of (timestamp, message_id, name, content, is_bot)
_channels = defaultdict(deque)


def _clean(content: str) -> str:
    """Strip mention tags and collapse whitespace for prompt-friendly text."""
    text = _MENTION_RE.sub('', content or '').strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:MAX_SNIPPET_CHARS]


def record(channel_id, message_id, name, content, is_bot=False):
    """Store one chat message for a channel. Bot replies use is_bot=True."""
    if not channel_id:
        return
    text = _clean(content)
    if not text:
        return
    buf = _channels[str(channel_id)]
    buf.append((time.time(), str(message_id or ''), name or 'User', text, bool(is_bot)))
    # Trim by count and by age.
    while len(buf) > MAX_MESSAGES:
        buf.popleft()
    cutoff = time.time() - MAX_AGE_SECONDS
    while buf and buf[0][0] < cutoff:
        buf.popleft()


def build_history(channel_id, exclude_message_id=None, limit=HISTORY_LIMIT):
    """
    Return the recent conversation as OpenAI-style chat messages
    ([{'role': 'user'|'assistant', 'content': ...}]) for the Groq prompt.
    User messages are prefixed with their speaker name so multi-person
    banter stays attributable. The triggering message is excluded so it is
    not duplicated (it is appended fresh by the caller).
    """
    if not channel_id:
        return []
    buf = _channels.get(str(channel_id))
    if not buf:
        return []
    cutoff = time.time() - MAX_AGE_SECONDS
    fresh = [m for m in buf if m[0] >= cutoff and m[1] != str(exclude_message_id or '')]
    recent = fresh[-limit:]
    history = []
    for _ts, _mid, name, text, is_bot in recent:
        if is_bot:
            history.append({'role': 'assistant', 'content': text})
        else:
            history.append({'role': 'user', 'content': f"{name}: {text}"})
    return history


def clear(channel_id=None):
    """Wipe memory for one channel (or all channels when channel_id is None)."""
    if channel_id is None:
        _channels.clear()
    else:
        _channels.pop(str(channel_id), None)
