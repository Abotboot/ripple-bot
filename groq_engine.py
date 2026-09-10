import urllib.request
import json
import os
import re
import io
import uuid
import base64
from PIL import Image
import water_knowledge

KEY_FILE = os.path.join(os.path.dirname(__file__), "groq_key.txt")

def get_groq_key():
    env_key = os.environ.get("GROQ_API_KEY", "").strip()
    if env_key:
        return env_key
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                k = f.read().strip()
                if k.startswith("gsk_"):
                    return k
        except Exception:
            pass
    return ""

def format_for_discord(text: str) -> str:
    """Bulletproof Discord math and formatting cleaner. Strips reasoning tokens, raw LaTeX, $, $$, and converts to clean text/code blocks."""
    if not text:
        return ""

    # 0. Strip reasoning / thinking tokens (closed, unclosed, or orphaned)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"^<think>.*?(?:\n\n|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)

    # 1. Unpack any \boxed{...} or unclosed \boxed{
    for _ in range(4):
        text = re.sub(r"\\boxed\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\boxed\{?", "", text)

    # 2. Standardize math, logic, and calculus function macros
    funcs = [
        ("arctan", "arctan"), ("arcsin", "arcsin"), ("arccos", "arccos"),
        ("tan", "tan"), ("sin", "sin"), ("cos", "cos"), ("cot", "cot"),
        ("sec", "sec"), ("csc", "csc"), ("ln", "ln"), ("log", "log"),
        ("exp", "exp"), ("int", "∫"), ("cdot", "·"), ("pm", "±"),
        ("times", "×"), ("div", "÷"), ("approx", "≈"), ("neq", "≠"),
        ("leq", "≤"), ("geq", "≥"), ("infty", "∞"), ("pi", "π"),
        ("alpha", "α"), ("beta", "β"), ("theta", "θ"), ("lambda", "λ"),
        ("partial", "∂"), ("Longrightarrow", "⇒"), ("longrightarrow", "⟶"),
        ("rightarrow", "→"), ("to", "→"), ("longleftarrow", "⟵"),
        ("leftarrow", "←"), ("iff", "⟺"), ("Longleftrightarrow", "⟺"),
        ("therefore", "∴"), ("because", "∵"), ("forall", "∀"), ("exists", "∃")
    ]
    for macro, rep in funcs:
        text = re.sub(rf"\\[\s]*{macro}\b", rep, text)

    # 3. Square roots: \sqrt{x} -> √(x), \sqrt[n]{x} -> n√(x)
    for _ in range(6):
        if r"\sqrt" not in text:
            break
        text = re.sub(r"\\sqrt\[([^\]]+)\]\{([^{}]+)\}", r"\1√(\2)", text)
        text = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", text)
    text = re.sub(r"\\sqrt\{?", "√", text)

    # 4. Fractions: convert \frac{a}{b} -> (a)/(b)
    for _ in range(8):
        if r"\frac" not in text:
            break
        text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\frac\{?", "", text)

    # 5. Clean sizing / bracket macros: \left, \right, \big, etc.
    text = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\s*", "", text)

    # 6. Spacing macros
    text = re.sub(r"\\(?:,|;|!|quad|qquad)\s*", " ", text)

    # 7. Exponents and subscripts inside brackets
    text = re.sub(r"\^\{([^{}]+)\}", r"^\1", text)
    text = re.sub(r"_\{([^{}]+)\}", r"_\1", text)

    # 8. Convert display math blocks \[ ... \] or $$ ... $$ to code blocks
    text = re.sub(r"(?:\\\[|\$\$)\s*(.*?)\s*(?:\\\]|\$\$)", lambda m: f"```text\n{m.group(1).strip()}\n```", text, flags=re.DOTALL)

    # 9. Convert inline math \( ... \) or $ ... $ to inline code
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)", lambda m: f"`{m.group(1).strip()}`", text, flags=re.DOTALL)
    text = re.sub(r"\$([^$\n]+)\$", lambda m: f"`{m.group(1).strip()}`", text)

    # 10. Kill any remaining unclosed or lone $, $$, \[, \], \(, \) completely
    text = re.sub(r"\\\[|\\\]|\\\(|\\\)", "", text)
    text = text.replace("$$", "").replace("$", "")

    # 11. Clean backslashes before plain letters
    text = re.sub(r"\\([a-zA-Z])", r"\1", text)

    return text.strip()

def prepare_image_base64(img_bytes: bytes) -> str:
    im = Image.open(io.BytesIO(img_bytes))
    if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
        target_frame = min(im.n_frames - 1, max(1, im.n_frames // 3))
        try:
            im.seek(target_frame)
        except Exception:
            pass

    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    if max(im.size) > 400:
        im.thumbnail((400, 400), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=75)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

def query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.7, max_tokens=900, is_vision=False):
    key = get_groq_key()
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_format": "hidden"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "RippleBot/1.0"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content") or ""
            return content.strip()
    except urllib.error.HTTPError as e:
        # Cascade fallback: 3.8-27b (peak reasoning) -> 3.6-27b (higher token quota) -> gpt-oss-120b
        if model == "qwen/qwen3.8-27b":
            return query_groq(messages, model="qwen/qwen3.6-27b", temperature=temperature, max_tokens=min(max_tokens, 1500), is_vision=is_vision)
        elif model == "qwen/qwen3.6-27b" and not is_vision:
            return query_groq(messages, model="openai/gpt-oss-120b", temperature=temperature, max_tokens=min(max_tokens, 1000), is_vision=False)
        raise

def groq_translate(text: str, target_lang: str, source_lang: str = "auto") -> str:
    system_prompt = (
        f"You are an expert bilingual translator for Discord. "
        f"Translate the text into {target_lang}. "
        f"Preserve conversational nuance, slang, and emojis. "
        f"Output ONLY the translated text, no quotes or explanations."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ]
    try:
        res = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.2, max_tokens=600)
        return res.strip('`"\'* \n')
    except Exception as e:
        print(f"[Groq Translate Error]: {e}")
        return ""

def groq_summarize_chat(transcript: str, requester: str = "Friend") -> str:
    system_prompt = (
        "You are RippleBot on Discord. Summarize recent channel chat in 2-3 short, casual, and punchy bullet points. "
        "Mention what key people talked about. Use friendly Discord tone. Do NOT mention water quality unless people were actually talking about water."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Recent channel messages:\n{transcript}\n\nSummarize for {requester}:"}
    ]
    try:
        raw = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.5, max_tokens=600)
        return format_for_discord(raw)
    except Exception as e:
        return f"Couldn't recap right now: {e}"

def groq_enhance_image_prompt(raw_prompt: str) -> str:
    """Enrich simple prompts into detailed visual descriptions for high-quality image gen."""
    if len(raw_prompt.split()) > 15:
        return raw_prompt
    sys_p = (
        "You are an expert image prompt engineer. Convert user prompt into a visually rich, high-detail prompt "
        "specifying lighting, artistic style, perspective, and atmosphere under 30 words. "
        "Output ONLY the prompt text, no quotes or filler."
    )
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": raw_prompt}
    ]
    try:
        enhanced = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.7, max_tokens=150)
        enhanced = re.sub(r"<think>.*?</think>", "", enhanced, flags=re.DOTALL).strip('`"\'* \n')
        return enhanced if enhanced else raw_prompt
    except Exception:
        return raw_prompt

def groq_vision_chat(prompt: str, image_data_url: str, user_name: str = "Friend") -> str:
    instruction = (
        "You are RippleBot on Discord with full vision capability. "
        "Inspect this image, animated GIF frame, meme, anime scene, math problem, code, or chart. "
        "Be friendly, sharp, and accurate.\n"
        "- FOR MATH, CODE, OR HOMEWORK: Provide the FULL, COMPLETE solution step by step until completely finished. "
        "Do NOT truncate, do NOT skip steps, and do NOT stop halfway.\n"
        "- NO RAW LATEX: Discord does NOT render LaTeX ($ or $$). Write all formulas in clean code blocks (```text) or plain Unicode symbols (∫, ·, √, ², ±).\n\n"
        f"{user_name}: {prompt if prompt else 'What is this image/GIF? Solve and explain in full.'}"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_data_url}}
            ]
        }
    ]
    try:
        raw = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.5, max_tokens=850, is_vision=True)
    except Exception:
        raw = query_groq(messages, model="qwen/qwen3.6-27b", temperature=0.5, max_tokens=1500, is_vision=True)
    return format_for_discord(raw)

def groq_water_chat(user_message: str, user_name: str = "Friend", history: list = None) -> str:
    grounding_data = water_knowledge.search_water_data(user_message)

    system_prompt = (
        "You are RippleBot on Discord. You are the official bot and loyal companion of A Ripple Effect — "
        "the clean water platform tracking tap water quality, EPA legal limits, and health guidelines "
        "across 30 utilities and 23 states.\n\n"
        "GUIDELINES:\n"
        "1. LOYALTY & IDENTITY: You rep A Ripple Effect first and foremost. If asked who made you, your purpose, "
        "or what you do, you proudly state that you're built for A Ripple Effect to protect the community with "
        "tap water data and keep the server running smooth.\n"
        "2. LOWKEY & CHILL: Talk like a sharp, natural Discord homie. No corporate customer service fluff, no fake enthusiasm, "
        "no 'Hey there! 👋', no 'How can I assist you today?'.\n"
        "3. NATURAL PACING: For casual banter, stay short and natural. But for math, homework, coding, explanations, "
        "or deep questions, give the FULL, COMPLETE solution step-by-step until finished. Never stop halfway or leave calculations uncompleted.\n"
        "4. GENERAL UTILITY: You can chat about anime, gaming, memes, code, or translate, but you stay loyal to the Ripple Effect team.\n"
        "5. WATER QUERIES: If the user asks about water, utilities, or tap safety, cite our platform data concisely.\n"
        "6. NO RAW LATEX: Discord has no LaTeX ($ or $$). Write formulas in clean plain text or code blocks."
    )

    if grounding_data:
        system_prompt += f"\n\n--- PLATFORM WATER DATA (cite briefly) ---\n{grounding_data}\n------------------------------------------"

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-4:])
    messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

    raw = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.6, max_tokens=900)
    return format_for_discord(raw)

def groq_transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """Transcribes audio file using Groq Whisper-large-v3-turbo."""
    key = get_groq_key()
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    boundary = "----GroqAudioBoundary" + uuid.uuid4().hex

    parts = []
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n'.encode('utf-8'))
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'.encode('utf-8'))
    parts.append(audio_bytes)
    parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    body = b''.join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'User-Agent': 'RippleBot/1.0'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('text', '').strip()
    except Exception as e:
        print(f"[Groq Whisper Error]: {e}")
        return ""

def groq_meeting_summary(transcript: str, meeting_duration_str: str = "", attendees_str: str = "") -> str:
    """Generates an executive-level meeting summary with decisions and action items using Qwen 3.8 27B."""
    if not transcript or not transcript.strip():
        return ""

    system_prompt = (
        "You are an executive AI secretary and operational partner for A Ripple Effect founders. "
        "Your task is to analyze the meeting transcript, discussion notes, and voice transcripts, "
        "and produce a clean, punchy, high-impact meeting report formatted specifically for Discord.\n\n"
        "FORMATTING GUIDELINES:\n"
        "- Use bold headers and clean bullet points.\n"
        "- Do NOT invent details that weren't discussed.\n"
        "- Prioritize: Key Decisions, Action Items (with owners if mentioned), and Main Discussion Topics.\n"
        "- Tone: sharp, professional, direct.\n\n"
        "OUTPUT TEMPLATE:\n"
        "🎯 **Executive Summary**\n"
        "(1-2 punchy sentences summarizing the core focus of the meeting)\n\n"
        "📌 **Key Decisions Made**\n"
        "* Decision 1\n"
        "* Decision 2\n\n"
        "📋 **Action Items & Owners**\n"
        "* **Owner**: Task description [Deadline if mentioned]\n\n"
        "💡 **Key Topics & Discussion**\n"
        "* Topic 1: context\n"
        "* Topic 2: context"
    )

    user_content = f"Meeting Duration: {meeting_duration_str}\nAttendees: {attendees_str}\n\nTranscript & Notes:\n{transcript}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]
    try:
        raw = query_groq(messages, model="qwen/qwen3.8-27b", temperature=0.3, max_tokens=900)
        return format_for_discord(raw)
    except Exception as e:
        print(f"[Groq Meeting Summary Error]: {e}")
        return ""
