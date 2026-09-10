import json
import os
import re

CACHE_FILE = os.path.join(os.path.dirname(__file__), "water_cache.json")

STATE_ALIASES = {
    'cali': 'CA', 'california': 'CA', 'socal': 'CA', 'norcal': 'CA', 'ca': 'CA',
    'texas': 'TX', 'tx': 'TX',
    'new york': 'NY', 'nyc': 'NY', 'ny': 'NY',
    'illinois': 'IL', 'il': 'IL',
    'florida': 'FL', 'fl': 'FL',
    'michigan': 'MI', 'mi': 'MI',
    'arizona': 'AZ', 'az': 'AZ',
    'colorado': 'CO', 'co': 'CO',
    'georgia': 'GA', 'ga': 'GA',
    'massachusetts': 'MA', 'ma': 'MA',
    'nevada': 'NV', 'nv': 'NV',
    'ohio': 'OH', 'oh': 'OH',
    'pennsylvania': 'PA', 'pa': 'PA',
    'washington': 'WA', 'wa': 'WA',
    'wisconsin': 'WI', 'wi': 'WI',
    'indiana': 'IN',
    'louisiana': 'LA',
    'minnesota': 'MN',
    'missouri': 'MO',
    'north carolina': 'NC',
    'new jersey': 'NJ',
    'oregon': 'OR',
    'tennessee': 'TN'
}

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

_DATA = load_cache()

def search_water_data(query: str) -> str:
    q_lower = query.lower()
    results = []

    # 1. Check for explicit nationwide stats request ONLY
    if any(k in q_lower for k in ["nationwide stats", "platform stats", "total samples", "how many samples total", "overall stats", "how many utilities"]):
        s = _DATA.get("stats", {})
        return (
            f"A Ripple Effect totals: {s.get('utilitiesCount', 30)} utilities across {s.get('statesCovered', 23)} states, "
            f"{s.get('samplesCount', 3074):,} samples tested. Avg microplastics: {s.get('microplasticsAvg', 5.99)} particles/L."
        )

    # 2. Check for zip code match in utilities
    zip_match = re.search(r'\b(\d{5})\b', query)
    if zip_match:
        target_zip = zip_match.group(1)
        found_u = [u for u in _DATA.get("utilities", []) if target_zip in (u.get("zipCodes") or "")]
        if found_u:
            u = found_u[0]
            return f"ZIP {target_zip}: Served by {u['name']} ({u['city']}, {u['state']}). Water Source: {u.get('sourceType')}, Pop served: {u.get('population', 0):,}."
        else:
            return f"ZIP {target_zip} is not in our database yet (we track 30 major municipal systems)."

    # 3. Check for state match
    matched_state = None
    for alias, st_code in STATE_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', q_lower):
            matched_state = st_code
            break

    if matched_state:
        state_u = [u for u in _DATA.get("utilities", []) if u.get("state") == matched_state]
        if state_u:
            lines = [f"{matched_state} Water Utilities in database:"]
            for u in state_u:
                lines.append(f"- {u['name']} ({u['city']}): {u.get('sourceType')} water, serves {u.get('population', 0):,} people. Notes: {u.get('notes', 'Treated')}")
            return "\n".join(lines)

    # 4. Check for city / utility name match
    matched_u = []
    for u in _DATA.get("utilities", []):
        city = (u.get("city") or "").lower()
        name = (u.get("name") or "").lower()
        if (len(city) >= 3 and re.search(r'\b' + re.escape(city) + r'\b', q_lower)) or name in q_lower:
            matched_u.append(u)

    if matched_u:
        lines = []
        for u in matched_u[:2]:
            lines.append(f"- {u['name']} ({u['city']}, {u['state']}): {u.get('sourceType')} source, {u.get('population', 0):,} pop. Notes: {u.get('notes', 'Treated')}")
        return "\n".join(lines)

    # 5. Check for contaminant match
    matched_c = []
    for c in _DATA.get("contaminants", []):
        slug = c.get("slug", "").lower()
        name = c.get("name", "").lower()
        if slug in q_lower or name in q_lower or (slug == 'microplastics' and 'plastic' in q_lower) or (slug in ('pfoa', 'pfos') and 'pfas' in q_lower):
            matched_c.append(c)

    if matched_c:
        lines = []
        for c in matched_c[:2]:
            legal = f"{c.get('legalLimit')} {c.get('legalLimitUnit')}" if c.get('legalLimit') is not None else "Unregulated by EPA"
            health = f"{c.get('healthGuideline')} {c.get('healthGuidelineUnit')}" if c.get('healthGuideline') is not None else "0"
            lines.append(f"- {c['name']}: Legal Limit: {legal} | Health Guideline: {health}. Effects: {c.get('healthEffects')}")
        return "\n".join(lines)

    return ""
