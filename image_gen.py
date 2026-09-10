import urllib.request
import urllib.parse
import random
import re
import groq_engine

MODEL_MAP = {
    "flux": "flux",
    "sana": "sana",
    "turbo": "turbo",
    "realism": "flux-realism",
    "anime": "flux-anime"
}

def extract_model_and_prompt(prompt: str) -> tuple[str, str]:
    clean = prompt.strip()
    m = re.search(r'--(model\s+)?(flux|sana|turbo|realism|anime)\b', clean, re.IGNORECASE)
    model = "flux"
    if m:
        chosen = m.group(2).lower()
        model = MODEL_MAP.get(chosen, "flux")
        clean = re.sub(r'--(model\s+)?(flux|sana|turbo|realism|anime)\b', '', clean, flags=re.IGNORECASE).strip()
    return clean, model

def fetch_generated_image(prompt: str) -> tuple[bytes, str, str, str]:
    clean_p, model = extract_model_and_prompt(prompt)
    
    # Enrich prompt with Groq for dramatically higher quality
    enhanced_p = groq_engine.groq_enhance_image_prompt(clean_p)
    
    seed = random.randint(100000, 99999999)
    encoded = urllib.parse.quote(enhanced_p)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&model={model}&nologo=true&seed={seed}"
    
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=35) as resp:
        img_bytes = resp.read()
    
    return img_bytes, clean_p, model, enhanced_p
