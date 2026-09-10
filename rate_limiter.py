import time

user_requests = {}
global_requests = []
purge_cooldowns = {}

USER_LIMIT = 5       # Max 5 requests per 30 seconds per user
USER_WINDOW = 30
GLOBAL_LIMIT = 25    # Max 25 requests per 60 seconds globally (safely under Groq 30 RPM limit)
GLOBAL_WINDOW = 60

def check_rate_limit(user_id: str) -> tuple[bool, str]:
    now = time.time()

    # 1. Global limit
    global global_requests
    global_requests = [t for t in global_requests if now - t < GLOBAL_WINDOW]
    if len(global_requests) >= GLOBAL_LIMIT:
        wait = max(1, int(GLOBAL_WINDOW - (now - global_requests[0])))
        return False, f"⏳ High traffic! The bot is cooling down to protect API limits. Try again in **{wait}s**."

    # 2. Per-user limit
    u_history = user_requests.get(user_id, [])
    u_history = [t for t in u_history if now - t < USER_WINDOW]
    user_requests[user_id] = u_history

    if len(u_history) >= USER_LIMIT:
        wait = max(1, int(USER_WINDOW - (now - u_history[0])))
        return False, f"⏳ Whoa slow down, you are sending requests too fast! Cooldown: **{wait}s**."

    u_history.append(now)
    global_requests.append(now)
    return True, ""

def check_purge_cooldown(channel_id: str) -> tuple[bool, str]:
    now = time.time()
    last_purge = purge_cooldowns.get(channel_id, 0)
    if now - last_purge < 3:
        wait = max(1, int(3 - (now - last_purge)))
        return False, f"⏳ Purge cooldown active in this channel. Wait **{wait}s**."
    purge_cooldowns[channel_id] = now
    return True, ""
