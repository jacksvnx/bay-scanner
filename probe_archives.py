"""
Probe v2: pull Broadcastify's archives JS and find the endpoints it calls.

v1 established:
  - login works (cookie: bcfyuser1)
  - /archives/feed/<id> returns 200 HTML but contains no archive IDs
  - the page loads /archives/scripts/archives.js

So the archive list comes from an API that JS calls. This fetches that JS and
prints every URL/fetch/endpoint-looking string in it.

Prints NO credentials.
"""

import os
import re
import json
import requests

FEED_ID = 44509
BC_USER = os.environ["BROADCASTIFY_USERNAME"]
BC_PASS = os.environ["BROADCASTIFY_PASSWORD"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

s = requests.Session()
s.headers.update({"User-Agent": UA})

s.post("https://www.broadcastify.com/login/",
       data={"username": BC_USER, "password": BC_PASS,
             "action": "auth", "redirect": "https://www.broadcastify.com/"},
       allow_redirects=True)
print(f"logged in, cookies: {list(s.cookies.keys())}\n")

print("=" * 70)
print("STEP 1: fetch archives.js")
print("=" * 70)

r = s.get("https://www.broadcastify.com/archives/scripts/archives.js?v=20", timeout=30)
js = r.text
print(f"  HTTP {r.status_code} | {len(js)} chars")

print()
print("=" * 70)
print("STEP 2: endpoint-looking strings in the JS")
print("=" * 70)

patterns = {
    "fetch() calls":      r"fetch\(\s*[`'\"]([^`'\"]+)[`'\"]",
    "template URLs":      r"[`'\"](/[a-z0-9/_.-]*(?:ajax|api|archive|feed|json|php)[a-z0-9/_.?=&${}-]*)[`'\"]",
    "absolute URLs":      r"[`'\"](https?://[^`'\"\s]+)[`'\"]",
    "url = assignments":  r"(?:url|endpoint|apiUrl|path)\s*[:=]\s*[`'\"]([^`'\"]+)[`'\"]",
}

for label, pat in patterns.items():
    hits = sorted(set(re.findall(pat, js, re.IGNORECASE)))
    hits = [h for h in hits if len(h) > 3][:25]
    print(f"\n  {label}: {len(hits)}")
    for h in hits:
        print(f"    {h}")

print()
print("=" * 70)
print("STEP 3: context around request-making code")
print("=" * 70)

for m in list(re.finditer(r"(ajax|fetch|XMLHttpRequest|\.get\(|\.post\()", js, re.I))[:12]:
    start = max(0, m.start() - 90)
    ctx = re.sub(r"\s+", " ", js[start:m.start() + 160])
    print(f"    ...{ctx}...")

print()
print("=" * 70)
print("STEP 4: also check the feed page for inline JSON/config")
print("=" * 70)

r2 = s.get(f"https://www.broadcastify.com/archives/feed/{FEED_ID}", timeout=30)
html = r2.text
for pat, label in [
    (r"(?:var|const|let)\s+(\w*(?:feed|archive|config)\w*)\s*=\s*([^;\n]{0,200})", "JS vars"),
    (r"data-(\w+)=[\"']([^\"']{0,80})[\"']", "data-attributes"),
]:
    hits = re.findall(pat, html, re.IGNORECASE)[:20]
    print(f"\n  {label}: {len(hits)}")
    for h in hits:
        print(f"    {h[0]} = {str(h[1])[:120]}")

print("\nDone. Paste this whole output back into the chat.")
