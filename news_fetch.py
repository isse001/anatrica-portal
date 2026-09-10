#!/usr/bin/env python3
"""
news_fetch.py — build news.json for the Anatrica portal's News pane.

Primary source: Google News RSS search (keyless, and unlike GDELT's DOC 2.0 API
it does not 429 shared cloud IPs like GitHub Actions runners).  GDELT is tried
first only opportunistically; it is expected to fail from CI and that is fine.

Runs server-side (GitHub Actions) — no CORS involved.  Standard library only.

Output: news.json  ->  { "generated": ISO8601, "categories": { <cat>: [row, ...] } }
row = { "title","url","src","tag","x","dt": ISO8601, "trusted": bool, "country": "" }

A category that yields nothing keeps its rows from the previous news.json, so a
transient outage never blanks the feed.
"""

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

OUT = "news.json"
PER_CATEGORY = 30
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RETRIES = 3

# --- Google News RSS: tighter queries than GDELT; when:14d keeps it recent ---
GNEWS = {
    "global":  '("sulphuric acid" OR "sulfuric acid" OR "caustic soda" OR "soda ash" OR "sulphur price" OR "ammonium sulphate" OR chlor-alkali) (shortage OR crunch OR price OR export OR tariff OR outage OR ban) when:14d',
    "mining":  'copper (mine OR mining OR LME OR "sulphuric acid" OR "copper concentrate" OR cobalt OR smelter OR Codelco OR Chile OR Zambia OR Congo) (price OR shortage OR crunch OR ban OR record OR export) when:14d',
    "africa":  '(copper OR cobalt OR "caustic soda" OR "soda ash" OR fertilizer OR sulphur OR "sulphuric acid") (Congo OR DRC OR Zambia OR Nigeria OR Egypt OR Morocco OR Kenya OR "South Africa" OR Tanzania OR Ghana) (export OR import OR ban OR price OR tariff OR smelter OR refinery) when:14d',
    "mideast": '(sulphur OR sulfur OR "sulphuric acid" OR petrochemical OR methanol OR ammonia OR fertilizer OR "caustic soda") (Hormuz OR "Red Sea" OR Saudi OR Aramco OR SABIC OR UAE OR Iran OR Qatar OR Gulf) (disruption OR reroute OR export OR shortage OR sanctions OR outage) when:14d',
}

# GDELT DOC 2.0 — tried first, allowed to fail
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_Q = {
    "global": '("sulphuric acid" OR "sulfuric acid" OR "caustic soda" OR "soda ash" OR "ammonium sulphate" OR chlor-alkali) (shortage OR price OR export OR tariff OR ban) sourcelang:eng',
    "mining": '(copper OR "sulphuric acid" OR cobalt OR smelter) (mine OR LME OR shortage OR ban OR Chile OR Zambia OR Congo) sourcelang:eng',
    "africa": '(copper OR cobalt OR "caustic soda" OR fertilizer OR sulphur) (Congo OR Zambia OR Nigeria OR Egypt OR Morocco OR Kenya OR "South Africa") (ban OR export OR price OR tariff) sourcelang:eng',
    "mideast": '(sulphur OR petrochemical OR methanol OR ammonia OR fertilizer) (Hormuz OR Saudi OR Aramco OR Iran OR Qatar OR Gulf) (disruption OR export OR shortage OR sanctions) sourcelang:eng',
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
           "zawya.com", "meed.com", "arabnews.com", "thenationalnews.com", "spglobal.com",
           "kitco.com", "metal.com", "cnbc.com", "ft.com", "wsj.com", "apnews.com"}

BLOCK = {"einpresswire.com", "openpr.com", "digitaljournal.com", "issuewire.com", "prfree.com",
         "prlog.org", "newswire.com", "abnnewswire.com", "24-7pressrelease.com", "pr.com",
         "globenewswire.com", "prnewswire.com", "businesswire.com", "accesswire.com"}


def domain_of(url):
    try:
        h = (urllib.parse.urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else ""


# ---------------- Google News RSS ----------------
def gnews(cat):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(GNEWS[cat])
           + "&hl=en-US&gl=US&ceid=US:en")
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            raw = fetch(url)
            if b"<!DOCTYPE" in raw[:2000] or b"<!ENTITY" in raw:   # no DTDs/entities in a plain RSS feed
                raise ValueError("unexpected DOCTYPE/ENTITY in feed")
            root = ET.fromstring(raw)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  [{cat}] gnews attempt {attempt}/{RETRIES}: {e}", file=sys.stderr)
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    else:
        raise last

    rows, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = it.find("source")
        src_name = (src_el.text or "").strip() if src_el is not None else ""
        src_site = (src_el.get("url") if src_el is not None else "") or ""
        dom = domain_of(src_site)
        # Google News titles are "Headline - Publisher" — trim the trailing publisher
        if src_name and title.endswith(" - " + src_name):
            title = title[: -(len(src_name) + 3)].strip()
        title = html.unescape(title)
        if not dom or dom in BLOCK:
            continue
        key = re.sub(r"\W+", "", title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            dt = None
        rows.append({
            "title": title, "url": link, "src": dom or src_name.lower().replace(" ", ""),
            "tag": "", "x": "", "dt": iso(dt),
            "trusted": dom in TRUSTED, "country": "",
        })
    rows.sort(key=lambda r: r["dt"], reverse=True)
    rows.sort(key=lambda r: 0 if r["trusted"] else 1)
    return rows[:PER_CATEGORY]


# ---------------- GDELT (opportunistic) ----------------
def gdelt(cat):
    q = urllib.parse.urlencode({"query": GDELT_Q[cat], "mode": "artlist", "format": "json",
                                "maxrecords": 60, "sort": "datedesc", "timespan": "3w"})
    raw = fetch(GDELT + "?" + q, timeout=25).decode("utf-8", "replace")
    if not raw.lstrip().startswith("{"):
        raise ValueError("non-JSON (throttled): " + raw.lstrip()[:80])
    j = json.loads(raw)
    rows, seen = [], set()
    for a in (j.get("articles") or []):
        url = a.get("url") or ""
        title = (a.get("title") or "").strip()
        if not title or not re.match(r"^https?://", url, re.I):
            continue
        dom = domain_of(url)
        if not dom or dom in BLOCK or url in seen:
            continue
        seen.add(url)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", str(a.get("seendate") or ""))
        dt = datetime(*(int(x) for x in m.groups()), tzinfo=timezone.utc) if m else None
        rows.append({"title": title, "url": url, "src": dom, "tag": "", "x": "",
                     "dt": iso(dt), "trusted": dom in TRUSTED, "country": ""})
    rows.sort(key=lambda r: r["dt"], reverse=True)
    rows.sort(key=lambda r: 0 if r["trusted"] else 1)
    return rows[:PER_CATEGORY]


def main():
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f).get("categories", {})
    except Exception:
        prev = {}

    cats, ok_any = {}, False
    for i, cat in enumerate(GNEWS):
        if i:
            time.sleep(2)
        rows = []
        for name, fn in (("gdelt", gdelt), ("gnews", gnews)):
            try:
                rows = fn(cat)
                if rows:
                    print(f"  [{cat}] {len(rows)} items via {name}")
                    break
            except Exception as e:  # noqa: BLE001
                print(f"  [{cat}] {name} failed: {e}", file=sys.stderr)
        if rows:
            cats[cat] = rows
            ok_any = True
        else:
            keep = prev.get(cat, [])
            cats[cat] = keep
            print(f"  [{cat}] no source worked — kept {len(keep)} previous items", file=sys.stderr)

    if not ok_any and not any(prev.values()):
        print("all sources failed and no previous news.json — leaving file untouched", file=sys.stderr)
        sys.exit(1)

    out = {"generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
           "categories": cats}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT} — {sum(len(v) for v in cats.values())} items")


if __name__ == "__main__":
    main()
