"""
One-off diagnostic: figure out how Broadcastify's archives work right now.

Broadcastify rebuilt their archives section, so the old ajax.php listing endpoint
404s. This logs in with your Premium account and reports what the current archive
page actually returns, so we can write the real fetcher against facts instead of
guesses.

Prints NO credentials — only status codes, counts, and short structural snippets.

Run it from the Actions tab via the "Probe Broadcastify Archives" workflow.
"""

import os
import re
import sys
import requests

FEED_ID = 44509
BC_USER = os.environ["BROADCASTIFY_USERNAME"]
BC_PASS = os.environ["BROADCASTIFY_PASSWORD"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

s = requests.Session()
s.headers.update({"User-Agent": UA})


def show(label, resp, body_chars=0):
    ctype = resp.headers.get("content-type", "?")
    print(f"  {label}: HTTP {resp.status_code} | {ctype} | {len(resp.text)} chars")
    if body_chars:
        snippet = re.sub(r"\s+", " ", resp.text[:body_chars])
        print(f"    body starts: {snippet}")


print("=" * 70)
print("STEP 1: log in")
print("=" * 70)

r = s.post(
    "https://www.broadcastify.com/login/",
    data={"username": BC_USER, "password": BC_PASS,
          "action": "auth", "redirect": "https://www.broadcastify.com/"},
    allow_redirects=True,
)
show("POST /login/", r)
cookies = list(s.cookies.keys())
print(f"  cookies received: {cookies}")
logged_in = any("bcfy" in c.lower() or "phpsess" in c.lower() for c in cookies)
print(f"  looks logged in: {logged_in}")

print()
print("=" * 70)
print("STEP 2: try the archive listing page")
print("=" * 70)

candidates = [
    f"https://www.broadcastify.com/archives/feed/{FEED_ID}",
    f"https://www.broadcastify.com/archives/ajax.php?feedId={FEED_ID}&date=09/14/2026",
    f"https://www.broadcastify.com/archives/api/feed/{FEED_ID}",
]

page_html = ""
for url in candidates:
    try:
        r = s.get(url, timeout=30)
        show(url.replace("https://www.broadcastify.com", ""), r, body_chars=200)
        if r.status_code == 200 and len(r.text) > len(page_html):
            page_html = r.text
    except Exception as e:
        print(f"  {url}: ERROR {e}")

print()
print("=" * 70)
print("STEP 3: look for archive IDs in whatever came back")
print("=" * 70)

ids = sorted(set(re.findall(rf"{FEED_ID}-\d{{9,12}}", page_html)))
print(f"  found {len(ids)} archive IDs matching '{FEED_ID}-<timestamp>'")
for i in ids[:5]:
    print(f"    {i}")

# Look for any JSON/fetch URLs the page references — that's likely the new listing API
api_hints = sorted(set(re.findall(r"['\"](/archives/[^'\"\s]{3,60})['\"]", page_html)))
print(f"  archive-ish URLs referenced in page: {api_hints[:15]}")

print()
print("=" * 70)
print("STEP 4: try downloading one archive")
print("=" * 70)

if ids:
    aid = ids[0]
    for pattern in [
        f"https://www.broadcastify.com/archives/downloadv2/{aid}",
        f"https://www.broadcastify.com/archives/download/{aid}",
    ]:
        try:
            r = s.get(pattern, timeout=60, allow_redirects=True)
            ctype = r.headers.get("content-type", "?")
            print(f"  {pattern.replace('https://www.broadcastify.com', '')}: "
                  f"HTTP {r.status_code} | {ctype} | {len(r.content)} bytes | final: {r.url[:80]}")
            if "audio" in ctype or len(r.content) > 100_000:
                print("    ^^ THIS ONE RETURNED AUDIO")
        except Exception as e:
            print(f"  {pattern}: ERROR {e}")
else:
    print("  no archive IDs found, nothing to try")

print()
print("Done. Paste this whole output back into the chat.")
