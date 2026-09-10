#!/usr/bin/env python3
"""
news_fetch.py — pull chemical/mining/trade news from the GDELT DOC 2.0 API
and write news.json for the Anatrica portal's News pane.

Runs server-side (GitHub Actions), so there is no CORS problem here — the
browser never calls GDELT.  Standard library only; no pip installs.

Output: news.json  ->  { "generated": ISO8601, "categories": { <cat>: [row, ...] } }
row = { "title", "url", "src", "tag", "dt": ISO8601, "trusted": bool, "country": "" | "XX" }

On a per-category failure the previous news.json's rows for that category are kept,
so a partial GDELT outage never blanks the feed.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

OUT = "news.json"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
MAXRECORDS = 75
TIMESPAN = "3w"
PER_CATEGORY = 30
POLITE_SLEEP = 6          # GDELT asks for <= 1 request / 5 s
RETRIES = 3
UA = "Mozilla/5.0 (compatible; AnatricaPortalNewsBot/1.0; +https://github.com/)"

# --- queries: keep in sync with News.html QUERIES ---------------------------
QUERIES = {
    "global": '("sulphuric acid" OR "sulfuric acid" OR "sulphur price" OR "sulfur price" OR "granular sulphur" OR "caustic soda" OR "soda ash" OR "hydrogen peroxide" OR "ammonium sulphate" OR chlor-alkali) (shortage OR crunch OR price OR export OR tariff OR outage OR ban OR "record") sourcelang:eng',
    "mining": '(copper OR "copper price" OR "sulphuric acid" OR "sulfuric acid" OR SX-EW OR "copper concentrate" OR cobalt OR smelter) (mine OR mining OR LME OR shortage OR crunch OR ban OR record OR Chile OR Zambia OR Congo OR Codelco OR "critical minerals") sourcelang:eng',
    "africa": '(copper OR cobalt OR mining OR "caustic soda" OR "soda ash" OR fertilizer OR sulphur OR "sulphuric acid" OR chemical) (Congo OR DRC OR Zambia OR Nigeria OR Egypt OR Morocco OR Kenya OR "South Africa" OR Tanzania OR Ghana) (ban OR export OR import OR price OR shortage OR tariff OR smelter OR refinery OR duty) sourcelang:eng',
    "mideast": '(sulphur OR sulfur OR "sulphuric acid" OR petrochemical OR methanol OR ammonia OR fertilizer OR "caustic soda") (Hormuz OR "Red Sea" OR Saudi OR Aramco OR SABIC OR UAE OR Iran OR Qatar OR Gulf) (disruption OR reroute OR export OR shortage OR price OR outage OR sanctions) sourcelang:eng',
}

TRUSTED = {"icis.com", "chemanalyst.com", "argusmedia.com", "spglobal.com", "reuters.com",
           "bloomberg.com", "chemweek.com", "chemicalweek.com", "chemengonline.com",
           "fertilizerdaily.com", "worldfertilizer.com", "hellenicshippingnews.com", "opisnet.com",
           "tradingeconomics.com", "mining.com", "miningweekly.com", "mining-technology.com",
           "fastmarkets.com", "bnamericas.com", "sunsirs.com", "engineeringnews.co.za",
           "businessday.ng", "dailynewsegypt.com", "english.ahram.org.eg", "theeastafrican.co.ke",
           "apanews.net", "news.un.org", "un.org", "weforum.org", "procurementresource.com",
           "chemicalindustryjournal.co.uk", "echemi.com", "chemorbis.com", "hydrocarbonprocessing.com",
           "offshore-technology.com", "seatrade-maritime.com", "freightwaves.com", "gard.no",
           "zawya.com", "meed.com", "arabnews.com", "thenationalnews.com"}

BLOCK = {"einpresswire.com", "openpr.com", "digitaljournal.com", "issuewire.com", "prfree.com",
         "prlog.org", "newswire.com", "abnnewswire.com", "24-7pressrelease.com", "pr.com"}


def domain_of(url):
    try:
        h = urllib.parse.urlparse(url).hostname or ""
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def parse_seendate(s):
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi, se = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
    except ValueError:
        return None


def gdelt(cat):
    q = urllib.parse.urlencode({
        "query": QUERIES[cat], "mode": "artlist", "format": "json",
        "maxrecords": MAXRECORDS, "sort": "datedesc", "timespan": TIMESPAN,
    })
    url = GDELT + "?" + q
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
            raw_stripped = raw.lstrip()
            if not raw_stripped.startswith("{"):
                raise ValueError("non-JSON response (throttle/error): " + raw_stripped[:120])
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  [{cat}] attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            if attempt < RETRIES:
                time.sleep(POLITE_SLEEP * attempt * 2)
    raise last


def shape(cat, payload):
    seen, rows = set(), []
    for a in (payload.get("articles") or []):
        url = a.get("url") or ""
        title = (a.get("title") or "").strip()
        if not title or not re.match(r"^https?://", url, re.I):
            continue
        dom = domain_of(url)
        if not dom or dom in BLOCK or url in seen:
            continue
        seen.add(url)
        dt = parse_seendate(a.get("seendate"))
        sc = a.get("sourcecountry") or ""
        rows.append({
            "title": title,
            "url": url,
            "src": dom or a.get("domain") or "",
            "tag": "",
            "dt": dt.isoformat().replace("+00:00", "Z") if dt else "",
            "trusted": dom in TRUSTED,
            "country": sc if (sc and len(sc) <= 3) else "",
        })
    rows.sort(key=lambda r: (0 if r["trusted"] else 1, "" if not r["dt"] else r["dt"]), reverse=False)
    rows.sort(key=lambda r: r["dt"], reverse=True)          # newest first
    rows.sort(key=lambda r: 0 if r["trusted"] else 1)       # trusted floated to top, keeping date order
    return rows[:PER_CATEGORY]


def main():
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {"categories": {}}
    prev_cats = prev.get("categories", {}) if isinstance(prev, dict) else {}

    cats, ok_any = {}, False
    for i, cat in enumerate(QUERIES):
        if i:
            time.sleep(POLITE_SLEEP)
        try:
            rows = shape(cat, gdelt(cat))
            if rows:
                cats[cat] = rows
                ok_any = True
                print(f"  [{cat}] {len(rows)} items")
            else:
                raise ValueError("0 usable items")
        except Exception as e:  # noqa: BLE001
            keep = prev_cats.get(cat, [])
            cats[cat] = keep
            print(f"  [{cat}] FAILED ({e}) — kept {len(keep)} previous items", file=sys.stderr)

    if not ok_any and not any(prev_cats.values()):
        print("all categories failed and no previous news.json — leaving file untouched", file=sys.stderr)
        sys.exit(1)

    out = {"generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
           "categories": cats}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT} — {sum(len(v) for v in cats.values())} items across {len(cats)} categories")


if __name__ == "__main__":
    main()
