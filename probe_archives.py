"""
Probe v3 (final): confirm the archive API response shapes.

v2 found the endpoints inside archives.js:
  list:  /archives/api/archives.php?feedId=<id>&date=<YYYY-MM-DD>
  play:  /archives/api/play.php?id=<feedId>-<timestamp>   (returns the audio URL)

This calls both and prints exactly what comes back, so the real fetcher can be
written against the actual structure. Prints NO credentials.
"""

import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

FEED_ID = 44509
BASE = "https://www.broadcastify.com/archives"
BC_USER = os.environ["BROADCASTIFY_USERNAME"]
BC_PASS = os.environ["BROADCASTIFY_PASSWORD"]

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
})

s.post("https://www.broadcastify.com/login/",
       data={"username": BC_USER, "password": BC_PASS,
             "action": "auth", "redirect": "https://www.broadcastify.com/"})
print(f"cookies: {list(s.cookies.keys())}\n")

today = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")

print("=" * 70)
print(f"LIST: /api/archives.php?feedId={FEED_ID}&date={today}")
print("=" * 70)

r = s.get(f"{BASE}/api/archives.php", params={"feedId": FEED_ID, "date": today}, timeout=30)
print(f"  HTTP {r.status_code} | {r.headers.get('content-type')} | {len(r.text)} chars")
print(f"  raw first 600 chars:\n    {r.text[:600]}\n")

archive_id = None
try:
    data = r.json()
    print(f"  parsed JSON type: {type(data).__name__}")
    if isinstance(data, dict):
        print(f"  top-level keys: {list(data.keys())}")
        for k, v in data.items():
            if isinstance(v, list) and v:
                print(f"  '{k}' is a list of {len(v)}; first entry: {json.dumps(v[0])[:300]}")
                first = v[0]
                if isinstance(first, dict):
                    for key in ("id", "archiveId", "archive_id", "aid"):
                        if key in first:
                            archive_id = first[key]
                elif isinstance(first, list):
                    archive_id = first[0]
    elif isinstance(data, list) and data:
        print(f"  list of {len(data)}; first entry: {json.dumps(data[0])[:300]}")
        archive_id = data[0][0] if isinstance(data[0], list) else data[0].get("id")
except Exception as e:
    print(f"  not JSON: {e}")

print(f"\n  --> archive id picked for next test: {archive_id}")

if archive_id:
    print()
    print("=" * 70)
    print(f"PLAY: /api/play.php?id={archive_id}")
    print("=" * 70)
    r2 = s.get(f"{BASE}/api/play.php", params={"id": archive_id}, timeout=30)
    print(f"  HTTP {r2.status_code} | {r2.headers.get('content-type')} | {len(r2.text)} chars")
    print(f"  raw first 600 chars:\n    {r2.text[:600]}\n")

    audio_url = None
    try:
        d2 = r2.json()
        print(f"  keys: {list(d2.keys()) if isinstance(d2, dict) else type(d2).__name__}")
        if isinstance(d2, dict):
            for k, v in d2.items():
                if isinstance(v, str) and (".mp3" in v or v.startswith("http")):
                    audio_url = v
                    print(f"  audio URL found under key '{k}'")
    except Exception as e:
        print(f"  not JSON: {e}")

    if audio_url:
        print(f"\n  testing audio URL (host only shown): {audio_url.split('?')[0][:90]}")
        r3 = s.get(audio_url, timeout=60, stream=True)
        chunk = next(r3.iter_content(65536), b"")
        print(f"  HTTP {r3.status_code} | {r3.headers.get('content-type')} | "
              f"content-length={r3.headers.get('content-length')} | first chunk {len(chunk)} bytes")
        if len(chunk) > 1000:
            print("  ^^ AUDIO DOWNLOAD WORKS")

print("\nDone. Paste this output back into the chat.")
