"""
Bay County Scanner -> AI stories -> static website + Discord notifications

RUNS ENTIRELY IN GITHUB ACTIONS. No laptop/server needs to stay on.
Every ~15 minutes (see .github/workflows/scanner.yml) this script:
  1. Records ~10 min of live audio from Bay County's feeds (Premium static URLs)
  2. Splits that audio into individual transmissions using silence detection
  3. Transcribes each transmission locally with Whisper
  4. Summarizes each one into a short story with Claude (decoding Bay County's
     Signal/10-codes), geocodes the location, and renders a map graphic
  5. Writes a page for each incident + updates the site's index into docs/
     (GitHub Pages serves straight out of docs/, so this IS the deploy step)
  6. Posts new incidents to your Discord webhook

FEEDS (Bay County, FL — broadcastify.com):
  44509 = Law Enforcement (Sheriff, Panama City PD, PC Beach PD, Lynn Haven PD)
  44503 = Fire/EMS

SECRETS YOU NEED TO SET (repo Settings -> Secrets and variables -> Actions):
  ANTHROPIC_API_KEY      - console.anthropic.com
  MAPBOX_TOKEN           - mapbox.com (free tier, no card needed)
  BROADCASTIFY_USERNAME  - your Broadcastify Premium login
  BROADCASTIFY_PASSWORD  - your Broadcastify Premium password
  DISCORD_WEBHOOK        - Discord channel -> Integrations -> Webhooks
  SITE_BASE_URL          - e.g. https://yourdomain.com or https://you.github.io/repo
"""

import os
import sys
import json
import uuid
import glob
import base64
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from io import BytesIO

import requests
from pydub import AudioSegment
from pydub.silence import split_on_silence
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw, ImageFont
import anthropic

# ---------------- CONFIG ----------------

FEEDS = {44509: "Bay County Law Enforcement", 44503: "Bay County Fire/EMS"}
MIN_CLIP_MS = 1500
SILENCE_THRESH_DB = -40
IMG_W, IMG_H = 1080, 1080

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN", "")
BC_USER = os.environ["BROADCASTIFY_USERNAME"]
BC_PASS = os.environ["BROADCASTIFY_PASSWORD"]
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")

STATE_PATH = "state/processed.json"
INCIDENTS_PATH = "state/incidents.json"
DOCS_DIR = "docs"

BADGE_COLORS = {
    "Medical Emergency": (230, 80, 60), "Traffic": (210, 170, 20), "Disturbance": (150, 70, 190),
    "Suspicious Activity": (80, 150, 170), "Theft Report": (200, 120, 40), "Break-In": (170, 90, 40),
    "Missing Person": (60, 90, 200), "Domestic Dispute": (180, 50, 120), "Assault Report": (170, 20, 20),
    "Fire": (230, 130, 30), "Alarm": (40, 150, 90), "Other Incidents": (100, 100, 100),
}

BAY_COUNTY_CODES = """
Signal 0 = Armed / use caution        Signal 1 = Drunk driver           Signal 2 = Drunk pedestrian
Signal 3 = Hit and run                Signal 4 = Accident               Signal 5 = Murder
Signal 6 = Escaped prisoner           Signal 7 = Dead person             Signal 8 = Missing person
Signal 9 = Stolen tag                 Signal 10 = Stolen vehicle         Signal 11 = Abandoned vehicle
Signal 12 = Reckless driver           Signal 13 = Suspicious person/vehicle   Signal 14 = Information
Signal 15 = Special detail            Signal 16 = Highway obstruction    Signal 17 = Contact message
Signal 18 = Felony                    Signal 19 = Misdemeanor            Signal 20 = Mentally ill person
Signal 21 = Break and enter           Signal 22 = Disturbance            Signal 23 = Pedestrian hitchhiker
Signal 24 = Robbery                   Signal 25 = Fire/arson             Signal 26 = Drowning
Signal 27 = Prowler                   Signal 28 = Riot                   Signal 29 = Personal aid
Signal 30 = Bomb threat               Signal 31 = Drug case
10-4 = Acknowledge/OK  10-7 = Out of service  10-8 = In service  10-20 = Location
10-33 = Emergency traffic  10-97 = Arrived on scene  10-98 = Assignment complete
"""

SUMMARY_PROMPT = """You are writing a short, factual local-news story from a raw police/fire scanner
transcript. Bay County, FL uses these radio codes — decode any that appear into plain English,
don't leave raw codes in the output:

{codes}

Rules:
- One tight headline (under 15 words), one 2-4 sentence body, plain English, no codes.
- Category: one of Medical Emergency, Traffic, Disturbance, Suspicious Activity, Theft Report,
  Break-In, Missing Person, Domestic Dispute, Assault Report, Fire, Alarm, Other Incidents.
  Use the most specific one that fits — "Other Incidents" is a last resort, not a default.
- Factual tone, no speculation, no names of private individuals, no drama.
- If garbled, unclear, or you're not confident what was said, respond with exactly: SKIP
- If it's just radio procedure (10-4, checking in/out) with no actual incident, respond: SKIP
- "location": most specific address/cross-street/landmark mentioned, formatted for a map search
  (append ", Bay County, FL"). Empty string if nothing is mentioned.

Transcript:
{transcript}

Respond ONLY as JSON: {{"category": "...", "headline": "...", "body": "...", "location": "..."}}
"""

whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------- STATE ----------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ---------------- STEP 1: PULL NEW ARCHIVES ----------------

LOCAL_TZ = ZoneInfo("America/Chicago")   # Bay County, FL is Central time

# How long to record from each live feed per run. The workflow runs every 15 minutes,
# so 10 minutes of audio leaves headroom for transcription and publishing.
RECORD_SECONDS = 600


def record_feeds():
    """
    Records RECORD_SECONDS of live audio from each feed in parallel.

    Uses Broadcastify's Premium static stream URLs (https://audio.broadcastify.com/<id>.mp3),
    which are a documented Premium feature and need HTTP basic auth with your account
    login. This replaced the old archive-download approach after Broadcastify rebuilt
    their archives section and the unofficial archive endpoint stopped existing.

    Returns a list of (feed_id, feed_name, mp3_path) for recordings that produced audio.
    """
    token = base64.b64encode(f"{BC_USER}:{BC_PASS}".encode()).decode()
    os.makedirs("recordings", exist_ok=True)

    procs = []
    for feed_id, feed_name in FEEDS.items():
        out_path = f"recordings/{feed_id}_{uuid.uuid4().hex[:8]}.mp3"
        cmd = [
            "ffmpeg", "-y",
            "-headers", f"Authorization: Basic {token}\r\n",
            "-i", f"https://audio.broadcastify.com/{feed_id}.mp3",
            "-t", str(RECORD_SECONDS),
            "-c", "copy", out_path,
            "-loglevel", "error",
        ]
        print(f"[feed {feed_id}] recording {RECORD_SECONDS}s from live stream...")
        procs.append((feed_id, feed_name, out_path, subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)))

    results = []
    for feed_id, feed_name, out_path, proc in procs:
        _, err = proc.communicate()
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if size < 10_000:
            print(f"[feed {feed_id}] recording failed or empty ({size} bytes). ffmpeg said: {err.strip()[:500]}")
            continue
        print(f"[feed {feed_id}] recorded {size // 1024} KB")
        results.append((feed_id, feed_name, out_path))
    return results

# ---------------- STEP 2: SEGMENT + TRANSCRIBE ----------------

def split_into_transmissions(mp3_path):
    audio = AudioSegment.from_file(mp3_path)
    clips = split_on_silence(audio, min_silence_len=700, silence_thresh=SILENCE_THRESH_DB, keep_silence=250)
    paths = []
    for clip in clips:
        if len(clip) < MIN_CLIP_MS:
            continue
        p = f"/tmp/clip_{uuid.uuid4().hex}.wav"
        clip.export(p, format="wav")
        paths.append(p)
    return paths


def transcribe(clip_path):
    segments, _ = whisper_model.transcribe(clip_path, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()

# ---------------- STEP 3: SUMMARIZE + GEOCODE ----------------

def summarize(transcript):
    if len(transcript) < 8:
        return None
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=300,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(codes=BAY_COUNTY_CODES, transcript=transcript)}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("SKIP"):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def geocode(location_text):
    if not location_text or not MAPBOX_TOKEN:
        return None
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(location_text)}.json"
    r = requests.get(url, params={"access_token": MAPBOX_TOKEN, "limit": 1, "country": "us"})
    features = r.json().get("features", []) if r.status_code == 200 else []
    if not features:
        return None
    lon, lat = features[0]["center"]
    return (lat, lon)

# ---------------- STEP 4: BUILD IMAGE ----------------

def get_font(size, bold=False):
    for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=font) <= max_width:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def build_image(post, incident_id, lat=None, lon=None):
    if lat is not None and MAPBOX_TOKEN:
        map_url = (f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
                   f"pin-l+e63232({lon},{lat})/{lon},{lat},14,0/{IMG_W}x{IMG_H}@2x?access_token={MAPBOX_TOKEN}")
        bg = Image.open(BytesIO(requests.get(map_url).content)).convert("RGB").resize((IMG_W, IMG_H))
    else:
        bg = Image.new("RGB", (IMG_W, IMG_H), (30, 30, 30))

    overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([0, IMG_H - 480, IMG_W, IMG_H], fill=(10, 10, 10, 190))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(bg)
    color = BADGE_COLORS.get(post["category"], (100, 100, 100))

    badge_font = get_font(30, bold=True)
    tw = draw.textlength(post["category"], font=badge_font)
    draw.rounded_rectangle([50, IMG_H - 440, 50 + tw + 32, IMG_H - 386], radius=8, fill=color)
    draw.text((66, IMG_H - 432), post["category"], font=badge_font, fill="white")

    draw.text((50, IMG_H - 370), f"Bay County · {datetime.now(LOCAL_TZ).strftime('%a, %b %-d, %-I:%M %p')}",
               font=get_font(26), fill=(220, 220, 220))

    head_font = get_font(52, bold=True)
    for i, line in enumerate(wrap_text(draw, post["headline"], head_font, IMG_W - 100)[:4]):
        draw.text((50, IMG_H - 320 + i * 62), line, font=head_font, fill="white")

    draw.text((50, IMG_H - 60), SITE_BASE_URL.replace("https://", "").upper(), font=get_font(28, bold=True), fill="white")

    path = f"{DOCS_DIR}/images/{incident_id}.png"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bg.save(path)
    return path

# ---------------- STEP 5: BUILD SITE PAGES ----------------

CARD_TMPL = """<a class="card" href="incidents/{id}.html">
  <img src="images/{id}.png" alt="">
  <div class="meta"><span class="badge" style="background:{color}">{category}</span> {when}</div>
  <h3>{headline}</h3>
</a>"""

INDEX_TMPL = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Bay County Scanner</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{background:#111;color:#eee;font-family:Georgia,serif;margin:0;padding:20px;}}
h1{{font-style:italic;}}
.card{{display:block;color:inherit;text-decoration:none;border-bottom:1px solid #333;padding:16px 0;}}
.card img{{width:100%;max-width:500px;border-radius:8px;display:block;margin-bottom:10px;}}
.badge{{color:white;padding:3px 10px;border-radius:6px;font-size:12px;font-weight:bold;}}
.meta{{color:#999;font-size:13px;margin-bottom:6px;}}
</style></head><body>
<h1>Bay County Scanner</h1>
{cards}
</body></html>"""

PAGE_TMPL = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{headline}</title>
<meta property="og:title" content="{headline}">
<meta property="og:description" content="{body}">
<meta property="og:image" content="{site}/images/{id}.png">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{background:#111;color:#eee;font-family:Georgia,serif;max-width:600px;margin:20px auto;padding:0 20px;}}
img{{width:100%;border-radius:8px;}} a{{color:#8ab4ff;}}</style></head><body>
<p><a href="../index.html">&larr; Back</a></p>
<img src="../images/{id}.png">
<h1>{headline}</h1>
<p>{body}</p>
<p style="color:#999;font-size:13px">{when} · {category}{map_link}</p>
</body></html>"""


def write_pages(all_incidents):
    os.makedirs(f"{DOCS_DIR}/incidents", exist_ok=True)
    cards = []
    for inc in sorted(all_incidents, key=lambda x: x["ts"], reverse=True)[:100]:
        cards.append(CARD_TMPL.format(
            id=inc["id"], category=inc["category"], color="#%02x%02x%02x" % BADGE_COLORS.get(inc["category"], (100, 100, 100)),
            when=inc["when"], headline=inc["headline"]))
        map_link = f' · <a href="https://www.google.com/maps?q={inc["lat"]},{inc["lon"]}">View on map</a>' if inc.get("lat") else ""
        with open(f"{DOCS_DIR}/incidents/{inc['id']}.html", "w") as f:
            f.write(PAGE_TMPL.format(site=SITE_BASE_URL, map_link=map_link, **inc))
    with open(f"{DOCS_DIR}/index.html", "w") as f:
        f.write(INDEX_TMPL.format(cards="\n".join(cards)))

# ---------------- STEP 6: DISCORD ----------------

def notify_discord(post, incident_id, image_path):
    if not DISCORD_WEBHOOK:
        return
    url = f"{SITE_BASE_URL}/incidents/{incident_id}.html" if SITE_BASE_URL else ""
    content = f"**{post['category']}** — {post['headline']}\n{post['body']}\n{url}"
    with open(image_path, "rb") as f:
        requests.post(DISCORD_WEBHOOK, data={"content": content}, files={"file": f})

# ---------------- MAIN ----------------

def main():
    all_incidents = load_json(INCIDENTS_PATH, [])
    new_count = 0

    for feed_id, feed_name, mp3_path in record_feeds():
        clips = split_into_transmissions(mp3_path)
        print(f"[feed {feed_id}] {len(clips)} transmission(s) detected")
        os.remove(mp3_path)

        for clip_path in clips:
            transcript = transcribe(clip_path)
            os.remove(clip_path)
            if not transcript:
                continue
            post = summarize(transcript)
            if not post:
                continue

            incident_id = uuid.uuid4().hex[:10]
            coords = geocode(post.get("location", ""))
            image_path = build_image(post, incident_id, *(coords or (None, None)))

            incident = {
                "id": incident_id, "ts": datetime.now(timezone.utc).isoformat(),
                "when": datetime.now(LOCAL_TZ).strftime("%a, %b %-d %-I:%M %p"),
                "category": post["category"], "headline": post["headline"], "body": post["body"],
                "lat": coords[0] if coords else None, "lon": coords[1] if coords else None,
                "feed": feed_name,
            }
            all_incidents.append(incident)
            notify_discord(post, incident_id, image_path)
            new_count += 1

    if new_count:
        write_pages(all_incidents)
        save_json(INCIDENTS_PATH, all_incidents)
        print(f"Published {new_count} new incident(s).")
    else:
        print("Nothing publishable this run.")


if __name__ == "__main__":
    main()
