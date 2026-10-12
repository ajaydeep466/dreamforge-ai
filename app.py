"""
TechGurusar Auto-Post Bot  -  V15
- 18 random poster looks (6 palettes x 3 layouts), never same look twice in a row
- New topic every post, no topic repeats until the whole list is used
- Title / comment / reply de-duplication (history saved on disk)
- Replies are based on the exact post the person commented on
"""
from flask import Flask, request, jsonify, send_file
import requests, os, random, json, time, re, hmac, hashlib, threading, difflib
from datetime import datetime
import pytz
from PIL import Image, ImageDraw, ImageFont
import textwrap

app = Flask(__name__)

PAGE_ID = os.getenv("FB_PAGE_ID", "")
PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("FB_APP_SECRET", "")          # optional: verifies webhook signature
# Cloudflare Workers AI credentials (configure as environment variables)
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
# Use a current Workers AI model by default. If Render still has an obsolete
# CLOUDFLARE_MODEL value configured, ignore it so old IDs are not retried.
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/zai-org/glm-4.7-flash").strip()
OBSOLETE_MODELS = {
    "@cf/meta/llama-3.3-70b-instruct",
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/mistral/mistral-7b-instruct-v0.2",
}
if not CLOUDFLARE_MODEL or CLOUDFLARE_MODEL in OBSOLETE_MODELS:
    CLOUDFLARE_MODEL = "@cf/zai-org/glm-4.7-flash"
CLOUDFLARE_AI_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/"
)
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "sukhkkp@gmail.com")
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "6"))
MIN_GAP_SECONDS = 90
GRAPH = "https://graph.facebook.com/v20.0"

IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = os.getenv("DATA_DIR", "/tmp")
STATE_FILE = os.path.join(DATA_DIR, "techgurusar_state.json")
POSTER_PATH = os.path.join(DATA_DIR, "poster.jpg")
_lock = threading.Lock()
# Prevent overlapping background posting jobs in this process.
_post_guard = threading.Lock()
_post_running = False

# ------------------------------------------------------------------ STATE
def _default_state():
    return {"used_topics": [], "titles": [], "comments": [], "templates": [],
            "posts": {}, "replied": [], "last_post": 0, "daily": {}}

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            s = json.load(f)
        for k, v in _default_state().items():
            s.setdefault(k, v)
        return s
    except Exception:
        return _default_state()

def save_state(s):
    s["titles"] = s["titles"][-100:]
    s["comments"] = s["comments"][-100:]
    s["templates"] = s["templates"][-10:]
    s["replied"] = s["replied"][-1500:]
    if len(s["posts"]) > 80:
        for k in sorted(s["posts"], key=lambda x: s["posts"][x].get("ts", 0))[:-80]:
            s["posts"].pop(k, None)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)

def too_similar(text, history, limit=0.6):
    t = (text or "").lower()
    return any(difflib.SequenceMatcher(None, t, h.lower()).ratio() > limit for h in history[-40:])

# ------------------------------------------------------------------ TOPICS
TOPICS = [
    "WhatsApp Hidden Features", "WhatsApp Privacy Settings", "WhatsApp Chat Tricks",
    "Phone Battery Saving Tricks", "Fast Charging Tips", "Secret Android Settings",
    "Android Developer Options Tricks", "Instagram Hidden Features", "Instagram Reels Tricks",
    "Instagram Privacy Settings", "Phone Speed Boost Trick", "Phone Hang Problem Fix",
    "YouTube Secret Tricks", "YouTube Data Saving Tricks", "Facebook Hidden Settings",
    "Facebook Privacy Lock Tricks", "Google Maps Secret Tricks", "Google Maps Offline Tricks",
    "Phone Storage Clean Tricks", "WiFi Speed Boost Tricks", "Mobile Data Saving Tricks",
    "Gmail Smart Tricks", "Google Photos Hidden Tricks", "Google Assistant Tricks",
    "Phone Camera Pro Tricks", "Phone Security Tips", "Online Fraud Se Bachne Ke Tricks",
    "UPI Payment Safety Tips", "Telegram Hidden Features", "Chrome Browser Secret Tricks",
    "Screenshot & Screen Record Tricks", "Phone Heating Problem Fix", "Free Useful Android Apps",
    "Keyboard Typing Speed Tricks", "Hotspot & Tethering Tricks", "Phone Lock Screen Tricks",
    "Earphone & Bluetooth Tricks", "Google Drive Storage Tricks", "Gaming Phone Performance Tricks",
    "Laptop Speed Boost Tricks", "Windows Shortcut Tricks", "Excel Basic Tricks",
    "AI Tools For Daily Work", "ChatGPT Prompt Tricks", "Phone Backup & Restore Tricks",
    "Aadhaar & DigiLocker Tips", "Smartwatch Hidden Features",
]
ANGLES = ["beginners ke liye", "privacy focus", "time bachane wale", "most people ko nahi pata",
          "daily use", "paise bachane wale", "pro user level", "quick 1-minute"]
COMMENT_STYLES = [
    "numbered steps with 1️⃣ 2️⃣ 3️⃣ 4️⃣ emojis",
    "short checklist using ✅ bullets",
    "friendly paragraph style followed by 3 quick steps",
    "Q&A style: 3 questions with short answers",
    "step by step using ➡️ arrows",
]

def pick_topic(state):
    unused = [t for t in TOPICS if t not in state["used_topics"]]
    if not unused:
        state["used_topics"] = []
        unused = TOPICS[:]
    topic = random.choice(unused)
    state["used_topics"].append(topic)
    return f"{topic} {datetime.now(IST).year}"

# ------------------------------------------------------------------ POSTER TEMPLATES
PALETTES = {
    "neon_night": dict(bg1=(10, 5, 35), bg2=(35, 5, 60), a1=(0, 255, 255), a2=(255, 0, 255), text=(255, 255, 255), card=(20, 12, 60)),
    "sunset":     dict(bg1=(255, 94, 98), bg2=(255, 153, 102), a1=(255, 255, 255), a2=(70, 20, 60), text=(255, 255, 255), card=(190, 50, 70)),
    "matrix":     dict(bg1=(0, 12, 0), bg2=(0, 40, 15), a1=(0, 255, 90), a2=(190, 255, 0), text=(225, 255, 225), card=(0, 28, 10)),
    "ocean":      dict(bg1=(0, 40, 90), bg2=(0, 120, 160), a1=(255, 214, 10), a2=(120, 255, 235), text=(255, 255, 255), card=(0, 60, 110)),
    "royal":      dict(bg1=(25, 10, 50), bg2=(80, 20, 100), a1=(255, 200, 60), a2=(255, 120, 200), text=(255, 255, 255), card=(55, 20, 85)),
    "clean_light": dict(bg1=(245, 247, 255), bg2=(215, 225, 250), a1=(40, 80, 230), a2=(230, 60, 110), text=(20, 25, 45), card=(255, 255, 255)),
}
LAYOUTS = ["outline_boxes", "filled_cards", "minimal_lines"]
FONT_BOLD = ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"]
FONT_REG = ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"]

def _font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

def clean_text(s):
    """Poster font can't draw emojis, so strip them."""
    return re.sub(r"[^\x00-\u024F]", "", str(s)).strip()

def _gradient(W, H, c1, c2, diagonal):
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)
    steps = H + (W if diagonal else 0)
    for i in range(H):
        t = i / H
        col = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
        d.line([(0, i), (W, i)], fill=col)
    if diagonal:
        overlay = Image.new("RGB", (W, H), c2)
        mask = Image.linear_gradient("L").rotate(random.choice([0, 90, 270]), expand=True).resize((W, H))
        img = Image.composite(overlay, img, mask.point(lambda v: int(v * 0.35)))
    return img

def _decor(draw, W, H, pal):
    kind = random.choice(["dots", "grid", "circles", "none"])
    col = pal["a1"]
    soft = tuple(int(c * 0.25 + b * 0.75) for c, b in zip(col, pal["bg1"]))
    if kind == "dots":
        for _ in range(60):
            x, y, r = random.randint(0, W), random.randint(0, H), random.randint(2, 6)
            draw.ellipse([x, y, x + r, y + r], fill=soft)
    elif kind == "grid":
        for x in range(0, W, 90):
            draw.line([(x, 0), (x, H)], fill=soft, width=1)
        for y in range(0, H, 90):
            draw.line([(0, y), (W, y)], fill=soft, width=1)
    elif kind == "circles":
        for _ in range(5):
            x, y, r = random.randint(0, W), random.randint(0, H), random.randint(80, 260)
            draw.ellipse([x - r, y - r, x + r, y + r], outline=soft, width=3)

def create_tech_poster(main_title, points_list, state=None, post_data=None):
    """Create a fresh neon-tech infographic whose content matches the current post."""
    W, H = 1080, 1350
    post_data = post_data or {}
    recent = (state or {}).get("templates", [])
    styles = [
        ("cyber_cyan", (3, 8, 28), (0, 255, 245), (255, 45, 210)),
        ("cyber_purple", (10, 4, 32), (180, 100, 255), (255, 60, 190)),
        ("cyber_green", (2, 17, 18), (50, 255, 150), (0, 220, 255)),
        ("cyber_gold", (14, 8, 22), (255, 225, 50), (255, 65, 180)),
        ("cyber_blue", (2, 12, 35), (60, 190, 255), (255, 70, 180)),
        ("cyber_red", (20, 4, 20), (255, 90, 120), (90, 240, 255)),
    ]
    available = [x for x in styles if x[0] not in recent[-6:]]
    style_name, bg, accent, pink = random.choice(available or styles)
    if state is not None:
        state.setdefault("templates", []).append(style_name)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    white = (245, 250, 255)
    muted = (130, 170, 200)

    # Circuit-board grid and neon traces, with a different arrangement each time.
    for x in range(0, W, 90):
        draw.line((x, 0, x, H), fill=(15, 35, 65), width=1)
    for y in range(0, H, 90):
        draw.line((0, y, W, y), fill=(15, 35, 65), width=1)
    for _ in range(36):
        x, y = random.randint(10, W-10), random.randint(10, H-10)
        dx = random.choice([-1, 1]) * random.randint(18, 65)
        dy = random.choice([-1, 1]) * random.randint(12, 45)
        draw.line((x, y, x+dx, y, x+dx, y+dy), fill=random.choice([accent, pink, (25, 70, 105)]), width=2)
        draw.ellipse((x-3, y-3, x+3, y+3), fill=accent)

    def fit_font(text, paths, start_size, max_width, min_size=22):
        for size in range(start_size, min_size-1, -2):
            font = _font(paths, size)
            if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
                return font
        return _font(paths, min_size)

    def wrapped(text, font, width, max_lines=3):
        lines = textwrap.wrap(clean_text(text), width=width) or [""]
        return "\n".join(lines[:max_lines])

    # Top HUD header.
    draw.rounded_rectangle((35, 28, 1045, 150), radius=28, outline=accent, width=3)
    draw.rounded_rectangle((55, 48, 145, 130), radius=18, outline=accent, width=3)
    draw.rectangle((78, 69, 122, 108), outline=accent, width=3)
    draw.line((85, 89, 97, 101, 119, 75), fill=accent, width=6)
    draw.text((170, 78), "TechGurusar", font=_font(FONT_BOLD, 54), fill=accent, anchor="lm")
    topic = clean_text(post_data.get("topic") or post_data.get("category") or "DAILY TECH TIP").upper()
    topic = textwrap.shorten(topic, width=26, placeholder="...")
    draw.text((170, 121), topic, font=_font(FONT_BOLD, 20), fill=pink, anchor="lm")

    # Headline with neon bands; wraps according to actual title length.
    title = clean_text(main_title)
    title_font = fit_font(title, FONT_BOLD, 68, 960, 38)
    title_lines = textwrap.wrap(title, width=25)[:3]
    y = 180
    for i, line in enumerate(title_lines):
        f = fit_font(line, FONT_BOLD, 68, 980, 36)
        draw.text((W//2, y), line, font=f, fill=(pink if i == 0 else accent), anchor="mt",
                  stroke_width=1, stroke_fill=(20, 25, 65))
        y += 78
    subtitle = clean_text(post_data.get("subtitle") or post_data.get("hook") or
                          "SMART TIP • SAVE THIS POST")
    subtitle = textwrap.shorten(subtitle, width=72, placeholder="...")
    draw.text((W//2, max(y+5, 400)), subtitle, font=_font(FONT_BOLD, 25), fill=(255, 225, 70), anchor="mm")

    # Main body: clear, relevant tips. Five items if supplied, with dynamic icon badge.
    body_top = 455
    items = [clean_text(x) for x in (points_list or [])[:5] if clean_text(x)]
    if not items:
        items = ["Check the settings before changing them", "Use trusted apps and updates"]
    item_h = 112 if len(items) >= 5 else 135
    body_bottom = body_top + item_h * len(items)
    panel_right = 765 if len(items) <= 4 else 825
    draw.rounded_rectangle((40, body_top-15, panel_right, min(body_bottom+5, 1090)),
                           radius=22, fill=(5, 18, 42), outline=accent, width=3)
    for i, point in enumerate(items):
        yy = body_top + i*item_h
        if yy + item_h > 1090:
            break
        draw.rounded_rectangle((62, yy+15, 122, yy+75), radius=16,
                               outline=(accent if i % 2 == 0 else pink), width=3)
        draw.text((92, yy+45), f"{i+1:02}", font=_font(FONT_BOLD, 27),
                  fill=accent if i % 2 == 0 else pink, anchor="mm")
        lines = textwrap.wrap(point, width=36 if len(items) >= 5 else 40)[:2]
        draw.multiline_text((145, yy+24), "\n".join(lines), font=_font(FONT_BOLD, 27),
                            fill=white, spacing=5)
        if i < len(items)-1:
            draw.line((62, yy+item_h-2, panel_right-20, yy+item_h-2),
                      fill=(40, 75, 110), width=2)

    # Topic-aware decorative device illustration (battery for charging/battery topics,
    # phone outline for mobile/app topics, chip for AI/security/general technology).
    title_topic = (title + " " + topic + " " + " ".join(items)).lower()
    cx, cy = 920, 745
    draw.rounded_rectangle((825, 510, 1040, 1000), radius=35, outline=pink, width=4)
    draw.rounded_rectangle((845, 550, 1020, 960), radius=22, outline=accent, width=3)
    if any(k in title_topic for k in ("battery", "charge", "charging", "power")):
        draw.text((932, 585), "BATTERY", font=_font(FONT_BOLD, 20), fill=accent, anchor="mm")
        draw.rounded_rectangle((875, 625, 990, 875), radius=18, outline=accent, width=4)
        draw.rectangle((910, 612, 955, 625), fill=accent)
        for j in range(4):
            yy = 825-j*48
            draw.rounded_rectangle((891, yy, 974, yy+34), radius=8,
                                   fill=accent if j < 3 else (20, 40, 70))
        draw.text((932, 910), "POWER", font=_font(FONT_BOLD, 22), fill=pink, anchor="mm")
    elif any(k in title_topic for k in ("ai", "security", "privacy", "fraud", "safe")):
        draw.polygon([(932, 610), (990, 635), (982, 735), (932, 785),
                      (882, 735), (874, 635)], outline=accent, fill=(8, 30, 55))
        draw.line((900, 690, 923, 713, 965, 660), fill=accent, width=8)
        draw.text((932, 840), "SECURE", font=_font(FONT_BOLD, 23), fill=pink, anchor="mm")
    else:
        draw.rounded_rectangle((875, 625, 990, 825), radius=18, outline=accent, width=4)
        for j in range(3):
            draw.rounded_rectangle((895, 655+j*48, 970, 690+j*48), radius=7,
                                   outline=pink if j == 1 else accent, width=2)
        draw.text((932, 865), "SMART TECH", font=_font(FONT_BOLD, 18), fill=pink, anchor="mm")
    draw.text((932, 945), "TIP", font=_font(FONT_BOLD, 24), fill=accent, anchor="mm")

    # Dynamic takeaway/footer based on this post's real content.
    takeaway = clean_text(post_data.get("takeaway") or
                          (items[0] if items else "Save this tip and try it"))
    takeaway = textwrap.shorten(takeaway, width=62, placeholder="...")
    draw.rounded_rectangle((40, 1095, 1040, 1180), radius=18, fill=(8, 18, 43), outline=pink, width=3)
    draw.text((65, 1120), "KEY TAKEAWAY", font=_font(FONT_BOLD, 20), fill=pink, anchor="lm")
    draw.text((65, 1152), takeaway, font=fit_font(takeaway, FONT_BOLD, 27, 930, 20),
              fill=accent, anchor="lm")

    cta = random.choice(["SAVE • SHARE • FOLLOW", "TRY IT TODAY • SAVE FOR LATER",
                         "FOLLOW FOR MORE TECH TIPS", "SHARE THIS WITH A FRIEND"])
    draw.rounded_rectangle((100, 1200, 980, 1265), radius=22, outline=accent, width=3)
    draw.text((W//2, 1232), cta, font=_font(FONT_BOLD, 27), fill=white, anchor="mm")
    draw.text((W//2, 1300),
              f"TechGurusar  •  {datetime.now(IST).strftime('%d %b %Y')}",
              font=_font(FONT_REG, 22), fill=muted, anchor="mm")
    img.save(POSTER_PATH, quality=95)
    return POSTER_PATH

# ------------------------------------------------------------------ AI
MODEL_CHAIN = list(dict.fromkeys([
    CLOUDFLARE_MODEL,
    "@cf/meta/llama-3.1-8b-instruct-fp8",
]))
LAST_GOOD_MODEL = {"name": None}


def ai_call(prompt, timeout=40):
    """Call Cloudflare Workers AI, trying models until one works."""
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError(
            "Cloudflare credentials missing: set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"
        )
    order = ([LAST_GOOD_MODEL["name"]] if LAST_GOOD_MODEL["name"] else []) + MODEL_CHAIN
    errors = []
    for model in dict.fromkeys(order):
        try:
            response = requests.post(
                CLOUDFLARE_AI_URL + model,
                headers={
                    "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 1.0,
                },
                timeout=timeout,
            )
            payload = response.json()
            result = payload.get("result") or {}
            if response.ok and payload.get("success") and result.get("response"):
                LAST_GOOD_MODEL["name"] = model
                return result["response"]
            errors.append(
                f"{model}: HTTP {response.status_code} "
                f"{str(payload.get('errors') or payload)[:180]}"
            )
            if response.status_code in (401, 403):
                break
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:100]}")
    raise RuntimeError(" | ".join(errors))


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)

FALLBACKS = [
    {"title": "5 Secret WhatsApp Tricks Jo Aap Miss Kar Rahe Ho!",
     "points": ["Chat lock lagao privacy ke liye", "Delete for everyone 2 din tak", "HD photo bhejo ab", "Disappearing message ON karo", "Chat backup OFF rakho"],
     "guide": ["WhatsApp > Chat > Chat Lock ON karo", "HD photo ke liye attach > HD select karo", "Settings > Privacy > Disappearing Message ON", "Backup OFF rakho"]},
    {"title": "5 Android Speed Tricks, Phone Rocket Banega!",
     "points": ["Animation 0.5x karo speed ke liye", "Background apps band karo", "Cache clear karo weekly", "Lite apps use karo", "Phone restart karo daily"],
     "guide": ["Settings > Developer > Animation 0.5x karo", "Settings > Apps > Cache Clear karo", "Lite version apps use karo", "Daily restart karo"]},
    {"title": "5 Battery Saving Tricks Jo Kaam Karte Hain!",
     "points": ["Dark mode ON rakho", "Brightness auto karo", "Background data limit karo", "Battery saver 20% pe ON", "Unused apps uninstall karo"],
     "guide": ["Settings > Display > Dark Mode", "Brightness Auto ON karo", "Settings > Data Usage > Background limit", "Battery Saver 20% pe lagao"]},
    {"title": "5 Phone Security Tricks Har User Ko Chahiye!",
     "points": ["2-step verification ON karo", "Unknown links pe click mat karo", "Play Protect scan chalao", "App permissions check karo", "Screen lock strong rakho"],
     "guide": ["Google Account > Security > 2-Step ON", "Play Store > Play Protect > Scan", "Settings > Apps > Permissions check", "PIN 6 digit ya fingerprint lagao"]},
    {"title": "5 Instagram Tricks Jo Har User Ko Pata Hone Chahiye!",
     "points": ["Close friends list banao", "Reels ka draft save karo", "Mute karo bina unfollow kiye", "Activity status OFF karo", "Saved posts ko collection me rakho"],
     "guide": ["Profile > Menu > Close Friends list banao", "Reel edit karke Save Draft dabao", "Profile pe Following > Mute karo", "Settings > Privacy > Activity Status OFF"]},
    {"title": "5 Google Maps Tricks Jo Safar Aasan Banate Hain!",
     "points": ["Offline map download karo", "Live location share karo", "Parking location save karo", "Toll-free route select karo", "Timeline OFF karke privacy lo"],
     "guide": ["Profile > Offline Maps > Select your own map", "Share location > 1 hour ya until you turn off", "Blue dot dabao > Save parking", "Directions > Options > Avoid tolls"]},
    {"title": "5 Storage Cleaning Tricks, Phone Space Free Karo!",
     "points": ["WhatsApp media clean karo", "Google Files se junk hatao", "Photos ko cloud backup karo", "Unused apps uninstall karo", "Downloads folder khali karo"],
     "guide": ["WhatsApp > Settings > Storage > Manage", "Files by Google > Clean > Junk files", "Google Photos > Free up space", "Settings > Apps > Unused apps hatao"]},
    {"title": "5 YouTube Tricks Jo Data Aur Time Dono Bachayen!",
     "points": ["Video quality 480p rakho", "Playback speed 1.5x karo", "Download karo WiFi pe", "Reminder break ON karo", "Captions ON karke dekho"],
     "guide": ["Video > Settings > Quality > 480p", "Settings icon > Playback speed 1.5x", "Download button dabao WiFi pe", "Settings > General > Remind me to take a break"]},
]

def build_comment(guide_steps):
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    head = random.choice(["👇 FULL GUIDE 👇", "📌 STEP BY STEP 📌", "🔥 DETAIL ME SAMJHO 🔥", "✅ ISSE KAR LO ✅"])
    tail = random.choice(["SAVE karo! #TechGurusar", "Dost ko bhi bhejo 🤝 #TechGurusar", "Pasand aaye to FOLLOW karo ❤️ #TechGurusar"])
    return head + "\n" + "\n".join(f"{emojis[i]} {s}" for i, s in enumerate(guide_steps[:5])) + "\n\n" + tail

def get_ai_json(state):
    topic = pick_topic(state)
    angle = random.choice(ANGLES)
    style = random.choice(COMMENT_STYLES)
    recent_titles = state["titles"][-15:]
    recent_comments = state["comments"][-4:]
    prompt = f"""Topic: {topic} ({angle})
Write Facebook post data in Hinglish (Hindi words written in English letters, like "Settings me jaake Chat Lock ON karo"). 
IMPORTANT: points, caption, first_comment, point_details and all replies must be Hinglish, NOT pure English.
Rules:
- title: catchy "5 tricks" style title in simple English/Hinglish, max 60 chars, NOT similar to: {recent_titles}
- points: exactly 5 very short tips, max 8 words each, written as actions/statements (like "Chat Lock ON karo"), NEVER as questions
- caption: short, with 1-2 emojis, ends with #TechGurusar
- first_comment: DETAILED guide 50+ words, style: {style}. Put every step/question on its own line using \n line breaks, with a blank line between sections. Must be completely different from: {recent_comments}
- point_details: 5 strings, one per point, each 15-25 words explaining how to do that exact tip
- reply_nice: 5 different short friendly replies (list) thanking the person for their comment/support (never say "thanks for sharing"), each different wording, related to this topic
- reply_link: 5 different short replies telling people the guide is in the first comment (list)
Return ONLY valid JSON with keys: title, points, caption, first_comment, point_details, reply_nice, reply_link
Unique seed {random.randint(1000, 9999)} {time.time()}"""
    data = None
    for attempt in range(3):
        try:
            d = extract_json(ai_call(prompt))
            if len(d.get("points", [])) < 5:
                raise ValueError("less than 5 points")
            if too_similar(d["title"], state["titles"], 0.75):
                raise ValueError("title repeated")
            if too_similar(d["first_comment"], state["comments"], 0.6):
                raise ValueError("comment repeated")
            data = d
            break
        except Exception as e:
            print(f"AI attempt {attempt + 1} failed:", e)
    if data is None:  # fallback, avoid recently used ones
        pool = [f for f in FALLBACKS if f["title"] not in state["titles"]] or FALLBACKS
        fb = dict(random.choice(pool))
        fb["points"] = fb["points"][:]
        data = {
            "title": fb["title"], "topic": fb["title"], "points": fb["points"],
            "caption": random.choice(["🤫 Ye 5 tricks ON kar lo! Guide 1st comment me 👇 #TechGurusar",
                                      "🚀 Try karo ye 5 tricks! Detail comment me 👇 #TechGurusar"]),
            "first_comment": build_comment(fb["guide"]),
            "point_details": fb["guide"],
            "reply_nice": ["❤️ Thanks! FOLLOW kar lo!", "🙏 Shukriya dost, share bhi karo!", "🔥 Mast! Aise tricks ke liye page follow karo!"],
            "reply_link": ["Guide 1st comment me hai bro 👇", "🔥 Full guide pehle comment me pin hai!", "Upar first comment dekho 👆 sab steps wahi hain"],
        }
    data.setdefault("topic", topic)
    return data

def ai_contextual_reply(post, comment_text):
    """Answer a question using this post's content only."""
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ACCOUNT_ID:
        return None
    ctx = {"title": post.get("title"), "points": post.get("points"), "guide": post.get("first_comment")}
    prompt = f"""You run the Facebook page TechGurusar. Reply to a comment on the post below in Hinglish.
Post: {json.dumps(ctx, ensure_ascii=False)}
Comment: "{comment_text}"
Rules: reply in Hinglish, max 30 words, friendly, 1 emoji, answer using the post's topic only, no hashtags, no links, don't repeat these earlier replies: {post.get('used_replies', [])[-5:]}
Return ONLY the reply text."""
    try:
        txt = ai_call(prompt, timeout=25).strip().strip('"')
        return txt[:300] if txt else None
    except Exception as e:
        print("AI reply failed:", e)
        return None

# ------------------------------------------------------------------ FACEBOOK
def fb_post_fast(image_path, caption):
    try:
        with open(image_path, "rb") as f:
            r1 = requests.post(f"{GRAPH}/{PAGE_ID}/photos", files={"source": f},
                               data={"published": "false", "access_token": PAGE_TOKEN}, timeout=60)
        print("UPLOAD RES:", r1.text)
        media_id = r1.json().get("id")
        if media_id:
            r2 = requests.post(f"{GRAPH}/{PAGE_ID}/feed", data={
                "message": caption,
                "attached_media": json.dumps([{"media_fbid": media_id}]),
                "access_token": PAGE_TOKEN}, timeout=60)
            print("FEED RES:", r2.text)
            return r2.json()
        with open(image_path, "rb") as fp:  # fallback: direct publish
            r = requests.post(f"{GRAPH}/{PAGE_ID}/photos", files={"source": fp},
                              data={"caption": caption, "access_token": PAGE_TOKEN, "published": "true"}, timeout=60)
        print("FALLBACK:", r.text)
        return r.json()
    except Exception as e:
        print("Post fail", e)
        return {}

def fb_comment_fast(pid, msg):
    try:
        time.sleep(2)
        r = requests.post(f"{GRAPH}/{pid}/comments", data={"message": msg, "access_token": PAGE_TOKEN}, timeout=20)
        print("COMMENT RES:", r.text)
        return r.json()
    except Exception as e:
        print("Comment fail", e)
        return {}

# ------------------------------------------------------------------ SMART REPLIES
LINK_WORDS = ("link", "kaha", "kahan", "kidhar", "guide", "step", "kaise", "kese", "how", "where", "batao", "bataiye")
THANKS_WORDS = ("thanks", "thank", "nice", "great", "awesome", "superb", "mast", "badhiya", "best", "good", "shukriya", "helpful", "👍", "❤", "🔥")

GENERIC_NICE = ["❤️ Thanks! Page ko FOLLOW kar lo!", "🙏 Shukriya dost, share bhi karo!", "🔥 Mast! Aise tricks ke liye jude raho!",
                "😊 Support ke liye thanks! Dost ko bhi bhejo", "👍 Pasand aaya to save kar lo!", "💯 Thanks bro! Roz naye tricks milenge"]
GENERIC_LINK = ["Guide pehle comment me hai bro 👇", "🔥 Full guide first comment me pin hai!", "Upar first comment dekho 👆 sab steps wahi hain"]

def fetch_post_context(post_id):
    """Rebuild post info from Facebook when local memory was wiped."""
    ctx = {"ts": time.time(), "title": "", "points": [], "first_comment": "", "point_details": [],
           "reply_nice": [], "reply_link": [], "used_replies": []}
    try:
        r = requests.get(f"{GRAPH}/{post_id}", params={"fields": "message", "access_token": PAGE_TOKEN}, timeout=15).json()
        ctx["title"] = r.get("message", "") or ""
        c = requests.get(f"{GRAPH}/{post_id}/comments", params={
            "filter": "toplevel", "order": "chronological", "fields": "from,message", "limit": 10,
            "access_token": PAGE_TOKEN}, timeout=15).json()
        for item in c.get("data", []):
            if str(item.get("from", {}).get("id", "")) == str(PAGE_ID):
                ctx["first_comment"] = item.get("message", "") or ""
                break
    except Exception as e:
        print("fetch_post_context failed:", e)
    return ctx

def pick_unused(options, used):
    fresh = [o for o in options if o and o not in used]
    return random.choice(fresh) if fresh else None

def make_reply(post, comment_text, name):
    text = comment_text.lower()
    used = post.setdefault("used_replies", [])
    reply = None
    m = re.search(r"(?:point|tip|trick|number|no\.?|#)?\s*\b([1-5])\b", text)
    details = post.get("point_details") or []
    if m and len(details) >= int(m.group(1)) and not any(w in text for w in THANKS_WORDS[:3]):
        n = int(m.group(1))
        reply = f"Point {n}: {details[n - 1]}"
    elif any(w in text for w in LINK_WORDS):
        reply = pick_unused((post.get("reply_link") or GENERIC_LINK), used)
    elif "?" in text or len(text.split()) > 7:
        reply = ai_contextual_reply(post, comment_text)
    elif any(w in text for w in THANKS_WORDS):
        reply = pick_unused((post.get("reply_nice") or GENERIC_NICE), used)
    if not reply:
        reply = pick_unused((post.get("reply_nice") or GENERIC_NICE) + (post.get("reply_link") or GENERIC_LINK), used)
    if not reply:  # all variants used - build a fresh one from the post itself
        pts = post.get("points") or ["ye tricks"]
        if not post.get("points"):
            reply = random.choice(GENERIC_NICE) + " " + random.choice(["🙌", "😊", "🔥", "👍"])
        else:
          reply = random.choice([
            f"Try karo: {random.choice(pts)} 🔥",
            f"Aapko kaun si trick sabse best lagi? Hum '{random.choice(pts)}' recommend karte hain 😊",
            f"Details first comment me hain, '{post.get('title', '')[:40]}' save kar lo ✅",
          ])
    if name and random.random() < 0.5:
        reply = f"{name.split()[0]}, {reply}"
    used.append(reply)
    post["used_replies"] = used[-30:]
    return reply

def handle_comment(value):
    comment_id = value.get("comment_id")
    post_id = value.get("post_id") or ""
    from_id = str(value.get("from", {}).get("id", ""))
    name = value.get("from", {}).get("name", "")
    text = value.get("message", "") or ""
    if not comment_id or from_id == str(PAGE_ID):
        return
    with _lock:
        state = load_state()
        if comment_id in state["replied"]:
            return
        post = state["posts"].get(post_id)
        state["replied"].append(comment_id)
        save_state(state)
    if post is None and post_id:  # memory wiped - rebuild from Facebook
        post = fetch_post_context(post_id)
        with _lock:
            state = load_state()
            state["posts"][post_id] = post
            save_state(state)
    if post is None:
        post = {}
    reply = make_reply(post, text, name)
    time.sleep(random.randint(3, 8))
    r = requests.post(f"{GRAPH}/{comment_id}/comments", data={"message": reply, "access_token": PAGE_TOKEN}, timeout=20)
    print("REPLIED:", r.text)
    with _lock:
        state = load_state()
        key = post_id if post_id in state["posts"] else None
        if key:
            state["posts"][key]["used_replies"] = post.get("used_replies", [])
        save_state(state)

def valid_signature(req):
    if not APP_SECRET:
        return True
    sig = req.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), req.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)

# ------------------------------------------------------------------ ROUTES
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Verification failed", 403
    if not valid_signature(request):
        return "bad signature", 403
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            if change.get("field") == "feed" and v.get("item") == "comment" and v.get("verb") == "add":
                threading.Thread(target=lambda val=v: _safe(handle_comment, val), daemon=True).start()
    return "ok", 200

def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        print("Webhook error:", e)

@app.route("/")
def home():
    return "Tech Gurusar LIVE ✅ V15 - TEMPLATES + NO REPEAT + SMART REPLIES"

@app.route("/ai-test")
def ai_test():
    """Shows the exact AI error: /ai-test?secret=KEY"""
    if request.args.get("secret") != SECRET_KEY:
        return jsonify({"error": "Invalid secret"}), 403
    info = {
        "key_set": bool(CLOUDFLARE_API_TOKEN),
        "account_id_set": bool(CLOUDFLARE_ACCOUNT_ID),
        "models_tried_in_order": MODEL_CHAIN,
    }
    try:
        info["reply"] = ai_call("Reply with exactly: OK", timeout=30)[:100]
        info["status"] = "AI WORKING"
        info["working_model"] = LAST_GOOD_MODEL["name"]
    except Exception as e:
        info["status"] = "AI FAILED"
        info["error"] = str(e)[:400]
    return jsonify(info)

@app.route("/preview")
def preview():
    """Test a poster design without posting: /preview?secret=KEY"""
    if request.args.get("secret") != SECRET_KEY:
        return jsonify({"error": "Invalid secret"}), 403
    pts = ["Chat lock lagao privacy ke liye", "Delete for everyone 2 din tak", "HD photo bhejo ab",
           "Disappearing message ON karo", "Chat backup OFF rakho"]
    create_tech_poster("5 Secret WhatsApp Tricks Jo Aap Miss Kar Rahe Ho!", pts)
    return send_file(POSTER_PATH, mimetype="image/jpeg")

def _run_auto_post(today):
    """Run the slow AI, image and Facebook work outside the HTTP request."""
    global _post_running
    try:
        # Do not hold the state lock while making slow network requests.
        with _lock:
            state = load_state()
            data = get_ai_json(state)
            poster_path = create_tech_poster(
                data["title"], data["points"], state, post_data=data
            )
            save_state(state)

        res = fb_post_fast(poster_path, data["caption"])
        pid = res.get("post_id") or res.get("id")
        if not pid:
            print("AUTO-POST FAILED: Facebook did not return a post ID:", res)
            return

        comment_res = fb_comment_fast(pid, data.get("first_comment", ""))
        print("AUTO-POST PUBLISHED:", pid, "COMMENT:", comment_res)

        with _lock:
            state = load_state()
            state["titles"].append(data["title"])
            state["comments"].append(data.get("first_comment", ""))
            state["daily"][today] = state["daily"].get(today, 0) + 1
            state["posts"][pid] = {
                "ts": time.time(),
                "topic": data.get("topic"),
                "title": data["title"],
                "points": data["points"],
                "first_comment": data.get("first_comment"),
                "point_details": data.get("point_details", []),
                "reply_nice": data.get("reply_nice", []),
                "reply_link": data.get("reply_link", []),
                "used_replies": [],
            }
            save_state(state)
    except Exception as e:
        print("AUTO-POST BACKGROUND ERROR:", repr(e))
    finally:
        with _post_guard:
            _post_running = False


@app.route("/auto-post")
def auto_post():
    global _post_running

    supplied_secret = request.args.get("secret", "")
    if not SECRET_KEY or not hmac.compare_digest(supplied_secret, SECRET_KEY):
        return jsonify({"error": "Invalid secret"}), 403

    today = datetime.now(IST).strftime("%Y-%m-%d")
    with _post_guard:
        if _post_running:
            return jsonify({"status": "already_running"}), 409

        with _lock:
            state = load_state()
            ago = time.time() - state.get("last_post", 0)
            if ago < MIN_GAP_SECONDS:
                return jsonify({
                    "error": "Duplicate blocked",
                    "last_post_ago": int(ago),
                }), 429

            state["daily"] = {today: state["daily"].get(today, 0)}
            if state["daily"][today] >= MAX_POSTS_PER_DAY:
                return jsonify({"error": f"Daily limit {MAX_POSTS_PER_DAY} reached"}), 429

            # Reserve the slot before launching the background job.
            state["last_post"] = time.time()
            save_state(state)

        _post_running = True
        try:
            threading.Thread(
                target=_run_auto_post,
                args=(today,),
                daemon=True,
                name="techgurusar-auto-post",
            ).start()
        except Exception:
            _post_running = False
            raise

    # Return immediately so cron-job.org does not wait for Facebook/AI calls.
    return jsonify({"accepted": True, "status": "posting_started"}), 202

@app.route("/privacy")
def privacy():
    return "<h1>Privacy Policy</h1><p>For Tech Gurusar Page.</p>"

@app.route("/terms")
def terms():
    return "<h1>Terms</h1><p>For Tech Gurusar Page.</p>"

@app.route("/data-deletion")
def data_deletion():
    return f"<h1>Data Deletion</h1><p>Email {CONTACT_EMAIL}</p>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
