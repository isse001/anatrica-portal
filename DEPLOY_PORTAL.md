# Deploy the Portal with auto-refreshing news (GitHub Pages + Actions)

The portal is a single static file. Hosting it on **GitHub Pages** gives everyone
a link that works on phone and desktop, and a **GitHub Action** rebuilds
`news.json` from GDELT every few hours so the News module stays current for all
visitors — no one has to press Refresh, and it updates even when nobody has the
page open.

## What goes in the repo

```
index.html            <- rename a copy of Portal.html to this
news.json             <- seed file (already generated; the Action overwrites it)
news_fetch.py         <- pulls GDELT -> news.json  (stdlib only)
.github/workflows/news.yml
```

`build_portal.py` and the 9 source dashboards are **not** needed on the server —
only when you want to regenerate `Portal.html`. Keep them in the repo anyway if
you like (they don't affect Pages), or in a `src/` subfolder.

## One-time setup

1. **Create a repo** (public = free Pages + free Actions). e.g. `anatrica-portal`.
2. Copy `Portal.html` to `index.html`, and add `news.json`, `news_fetch.py`,
   `.github/workflows/news.yml`. Commit and push.
3. **Enable Pages**: repo → *Settings → Pages* → *Build and deployment* →
   Source = *Deploy from a branch*, Branch = `main` / root. Save.
   After ~1 min the site is at `https://<you>.github.io/<repo>/`.
4. **Allow the Action to commit**: repo → *Settings → Actions → General* →
   *Workflow permissions* → **Read and write permissions**. Save.
   (The workflow also declares `permissions: contents: write`, but this toggle
   must be on.)
5. Run it once now: repo → *Actions* → *refresh news.json* → *Run workflow*.
   Check the run log; it should print `wrote news.json — N items…` and, if the
   content changed, push a commit. Pages redeploys automatically.

Done. Open the Pages URL on a phone and a laptop — both get the same, current
`news.json`. "Add to Home Screen" makes it behave like an app.

## How it stays fresh

- `news.yml` runs on `cron: "17 */6 * * *"` → every 6 hours. Change the cron for
  a different cadence (`0 6 * * *` = once daily at 06:00 UTC).
- Each run: `news_fetch.py` queries GDELT DOC 2.0 for the 4 categories
  (global / mining / africa / mideast — same queries as the News pane), filters
  and de-dupes, floats trusted trade outlets to the top, keeps 30 per category,
  writes `news.json`, commits only if it changed.
- A category that fails (GDELT throttle, outage) **keeps its previous rows** —
  the feed never blanks. If *every* category fails and there's no prior file the
  run exits non-zero and leaves `news.json` untouched.
- In the browser: the **portal shell** fetches `news.json` (same-origin — the
  News pane can't, from its sandboxed `srcdoc` iframe) and pushes it into the
  pane via `postMessage`. The shell re-fetches every 3 h and whenever the tab is
  re-focused, so a long-open portal updates itself.
- If `news.json` is missing (e.g. opened as a local `file://`), the News pane
  falls back to its built-in curated digest.

## Notes / limits

- GitHub free-tier scheduled runs can be **delayed 10–30 min** under load, and
  are **auto-disabled after 60 days with no repo commits** (GitHub emails first;
  a single commit or *Enable workflow* revives it).
- GDELT is keyless and asks for ≤ 1 request / 5 s; the script sleeps 6 s between
  categories and retries with backoff.
- Regenerating the portal after editing a dashboard: run `python build_portal.py`
  locally, copy the new `Portal.html` to `index.html`, commit.
- Private repo: Pages needs GitHub Pro/Team; everything else is the same.

## Alternative: Cloudflare Worker

If you want more punctual refreshes or don't want commit noise, a Cloudflare
Worker with a Cron Trigger can fetch GDELT server-side, cache in KV, and serve
`news.json` with `Access-Control-Allow-Origin: *`. Point the shell's
`fetch("news.json…")` at the Worker URL instead. Ask and I'll write it.
