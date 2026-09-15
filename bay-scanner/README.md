# Bay County Scanner

Turns Bay County, FL public-safety scanner audio into short AI-written stories with a map,
published to a free website and posted to Discord — running entirely on GitHub's free
infrastructure. No server or laptop needs to stay on.

## Setup

1. **Create a Broadcastify Premium account** (broadcastify.com/premium) — required to access
   feed archives, which is what makes this work without a live 24/7 listener.

2. **Create a GitHub repo** and push all these files to it. Keep it **public** — public repos
   get unlimited free GitHub Actions minutes, which matters since transcription is CPU-heavy.

3. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from a branch" →
   Branch: `main`, folder: `/docs`. Wait a minute, then your site is live at
   `https://<your-username>.github.io/<repo-name>/`.

   **Using your own domain instead:** put a file `docs/CNAME` containing just your domain
   (e.g. `scanner.yoursite.com`), and add a CNAME DNS record at your domain registrar pointing
   that subdomain to `<your-username>.github.io`.

4. **Add repo secrets** (Settings → Secrets and variables → Actions → New repository secret):
   - `ANTHROPIC_API_KEY` — console.anthropic.com
   - `MAPBOX_TOKEN` — mapbox.com, free tier
   - `BROADCASTIFY_USERNAME` / `BROADCASTIFY_PASSWORD` — your Premium login
   - `DISCORD_WEBHOOK` — Discord channel → Integrations → Webhooks → New Webhook → Copy URL

5. **Add one repo variable** (same Settings page, "Variables" tab instead of "Secrets"):
   - `SITE_BASE_URL` — your site's URL with no trailing slash, e.g.
     `https://scanner.yoursite.com` or `https://you.github.io/bay-scanner`

6. **Test it**: Actions tab → "Bay County Scanner" → Run workflow (the manual trigger).
   Watch the run log. If it finds anything, you'll see new files under `docs/` get committed,
   a Discord message appear, and the site update within a minute or two.

7. Once it's running clean, it fires automatically every 15 minutes.

## Notes

- The Signal/10-code glossary in `scanner.py` covers Bay County Sheriff's codes from the
  RadioReference wiki — expand `BAY_COUNTY_CODES` as you spot real codes it's missing.
- The model is told to output exactly `SKIP` for garbled or non-incident audio rather than
  guess — check the Action logs occasionally to see how often that's happening and tune
  `SILENCE_THRESH_DB` in `scanner.py` if it's too aggressive or too loose.
- Feed IDs are set for Bay County (44509 = Law Enforcement, 44503 = Fire/EMS). Add more to
  the `FEEDS` dict in `scanner.py` if you want to cover more talkgroups later.
