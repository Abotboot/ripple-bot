"""
One-shot team role setup for the Ripple server.

Creates the team roles (if they do not exist yet) and assigns them to
members by username / display-name match. Triggered with /setup-roles,
which only the guild owner can run, so the bot token never leaves the
Space and no extra credentials are needed.

"Founder & Executive Director" is created but intentionally left
unassigned - the founder can hand it out manually later.
"""

import re

ROLE_COLORS = {
    'Coders': 0x3498DB,
    'Lead Coder': 0x1F6FEB,
    'Engineers': 0xE67E22,
    'Lead Engineer': 0xD35400,
    'Lead Social Media': 0xE91E63,
    'Lead Event Coordinator': 0x9B59B6,
    'Lead Public Relations': 0x1ABC9C,
    'Finance Team': 0x2ECC71,
    'Founder & Executive Director': 0xF1C40F,
}

# name -> roles to grant. Lead roles also grant the base team role.
ASSIGNMENTS = {
    'Diwash': ['Coders'],
    'Abod': ['Lead Coder', 'Coders'],
    'Aryan': ['Lead Coder', 'Coders'],
    'Akshat': ['Engineers'],
    'Brian': ['Lead Engineer', 'Engineers'],
    'Ayaz': ['Lead Engineer', 'Engineers'],
    'Giamy': ['Lead Social Media'],
    'Zahra': ['Lead Event Coordinator'],
    'Abby': ['Lead Public Relations'],
    'Sujhav': ['Finance Team'],
    'Kenny': ['Finance Team'],
    # Founder & Executive Director: created, never auto-assigned.
}

SLASH_COMMANDS = [
    {
        'name': 'setup-roles',
        'description': 'Owner only: create the team roles and assign them by name (one-shot)',
    }
]


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').strip()).lower()


async def setup_roles(api_call, guild_id: str, requester_id: str | None = None):
    """Create missing roles and assign members. Returns a human-readable report."""
    guild = await api_call(f'/guilds/{guild_id}')
    if requester_id is not None and str(guild.get('owner_id')) != str(requester_id):
        return 'Only the server owner can run /setup-roles.'

    # 1. Ensure every role exists (resolve by exact name, create if missing).
    existing = {r['name']: r for r in await api_call(f'/guilds/{guild_id}/roles')}
    role_ids = {}
    created, skipped = [], []
    for name, color in ROLE_COLORS.items():
        role = existing.get(name)
        if role is None:
            try:
                role = await api_call(
                    f'/guilds/{guild_id}/roles',
                    method='POST',
                    data={'name': name, 'color': color, 'mentionable': True},
                )
                created.append(name)
            except Exception as e:
                skipped.append(f'{name} (create failed: {e})')
                continue
        role_ids[name] = role['id']

    # 2. Fetch members and build a lookup of normalised names -> ids.
    members = []
    after = '0'
    for _ in range(10):  # 1000 per page, hard stop as a safety net
        page = await api_call(f'/guilds/{guild_id}/members?limit=1000&after={after}')
        if not page:
            break
        members.extend(page)
        if len(page) < 1000:
            break
        after = page[-1]['user']['id']

    lookup: dict = {}
    for m in members:
        user = m.get('user', {})
        names = {
            _norm(user.get('username', '')),
            _norm(user.get('global_name', '') or ''),
            _norm(m.get('nick', '') or ''),
        }
        names.discard('')
        for n in names:
            lookup.setdefault(n, []).append(m)

    # 3. Assign roles; leads also get the base team role (already in the lists).
    assigned, missing, ambiguous, failed = {}, [], [], []
    seen_members = set()
    for target, roles in ASSIGNMENTS.items():
        matches = lookup.get(_norm(target), [])
        if len(matches) == 0:
            # Fall back to a unique prefix match (e.g. "abod" vs "abod123").
            candidates = [m for n, ms in lookup.items() if n.startswith(_norm(target)) for m in ms]
            matches = candidates
        if len(matches) == 0:
            missing.append(target)
            continue
        if len(matches) > 1:
            names = ', '.join(
                '@' + (m.get('user', {}).get('username') or '?') for m in matches
            )
            ambiguous.append(f'{target} -> {names}')
            continue
        m = matches[0]
        uid = m['user']['id']
        granted = []
        for role_name in roles:
            rid = role_ids.get(role_name)
            if not rid:
                continue
            try:
                await api_call(f'/guilds/{guild_id}/members/{uid}/roles/{rid}', method='PUT')
                granted.append(role_name)
            except Exception as e:
                failed.append(f'{target} + {role_name} ({e})')
        if granted:
            assigned[target] = granted
            seen_members.add(uid)

    lines = ['**Team role setup**']
    if created:
        lines.append('Created roles: ' + ', '.join(created))
    if assigned:
        lines.append('Assigned:')
        for target, granted in sorted(assigned.items()):
            lines.append(f'  - {target}: {", ".join(granted)}')
    if 'Founder & Executive Director' in role_ids:
        lines.append('Founder & Executive Director role exists but was left unassigned, as requested.')
    if missing:
        lines.append('Not found in the server (assign manually): ' + ', '.join(missing))
    if ambiguous:
        lines.append('Ambiguous matches (assign manually):')
        for a in ambiguous:
            lines.append(f'  - {a}')
    if failed:
        lines.append('Failed (bot needs Manage Roles and to sit above these members):')
        for f in failed:
            lines.append(f'  - {f}')
    if not (created or assigned or missing or ambiguous or failed):
        lines.append('Nothing to change - everything was already in place.')
    return '\n'.join(lines)
