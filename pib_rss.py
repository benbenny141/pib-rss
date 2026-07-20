#!/usr/bin/env python3
"""
pib_rss.py — Build a proper RSS 2.0 feed from PIB (Press Information Bureau, India).

Why this exists: PIB's official feed (RssMain.aspx) ships titles + links only —
no pubDate, no description, no ministry, capped at ~20 items, and the Lang
parameter is overridden by session cookies so you often get Hindi when you asked
for English. This script fixes all of that.

What it does:
  1. Establishes an English/National session (lang=1, reg=3).
  2. Reads the day's release list from allRel.aspx (falls back to RssMain.aspx).
  3. Fetches each new release page and extracts title, ministry, timestamp, body.
  4. Merges into a rolling JSON archive so the feed accumulates over time.
  5. Writes feed.xml (RSS 2.0, full text in content:encoded).

Usage:
    pip install requests beautifulsoup4
    python pib_rss.py                       # writes ./feed.xml + ./pib_state.json
    python pib_rss.py --out /srv/www/pib.xml --state /var/lib/pib_state.json
    python pib_rss.py --max-items 300 --limit 40 --delay 1.5
    python pib_rss.py --no-body             # metadata only, one fetch per run

Cron (hourly):
    17 * * * * /usr/bin/python3 /path/pib_rss.py --out /srv/www/pib.xml >> /var/log/pib.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import warnings

import requests
from bs4 import BeautifulSoup

try:  # bs4 warns when html.parser reads XML; harmless here, so quiet it
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

BASE = "https://www.pib.gov.in"
LIST_URL = f"{BASE}/allRel.aspx"
RSS_FALLBACK = f"{BASE}/RssMain.aspx"
RELEASE_URL = f"{BASE}/PressReleasePage.aspx"

# PIB's own codes: lang 1 = English, 2 = Hindi. reg 3 = National/Delhi.
LANG_EN = "1"
REGION_NATIONAL = "3"

IST = timezone(timedelta(hours=5, minutes=30))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 pib_rss.py/1.0"
)

PRID_RE = re.compile(r"PressRelease(?:Iframe)?Page\.aspx\?PRID=(\d+)", re.I)

# "Posted On: 20 JUL 2026 11:11AM by PIB Delhi"  /  Hindi: "प्रविष्टि तिथि:"
DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)", re.I
)
BUREAU_RE = re.compile(r"\bby\s+(PIB\s+[A-Za-z .]+)", re.I)

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
    )
}

# Selector candidates, tried in order. PIB re-skins its site periodically, so
# every extraction falls back through several options and then to regex.
MINISTRY_SELECTORS = [
    "div.ReleaseLefttag h2", "div.MinistryNameSubhead", "div.ReleaseMinistrytag",
    "h2.ReleaseMinistrytag", "div.release_ministry", ".innner-page-main-about-us-content-right-part h2",
]
TITLE_SELECTORS = [
    "h2.ReleaseTitle", "div.ReleaseTitle h2", "h1.ReleaseTitle",
    "div.innner-page-main-about-us-content-right-part h2", "h2",
]
DATE_SELECTORS = [
    "div.ReleaseDateSubHeaddateTime", "span.ReleaseDateSubHeaddateTime",
    "div.release_date", ".ReleaseDateSubHead",
]
BODY_SELECTORS = [
    "div#PdfDiv", "div.innner-page-main-about-us-content-right-part",
    "div.pdfPrint", "div.content-area", "div#printData",
]


# ---------------------------------------------------------------- HTTP session

def make_session(timeout: int = 30) -> requests.Session:
    """Session pinned to English + National. PIB stores this in cookies, so we
    set them explicitly AND pass query params on every request."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
    s.cookies.set("lang", LANG_EN, domain="www.pib.gov.in")
    s.cookies.set("reg", REGION_NATIONAL, domain="www.pib.gov.in")
    s.request_timeout = timeout  # consumed by fetch()
    try:
        s.get(f"{BASE}/index.aspx", params={"lang": LANG_EN, "reg": REGION_NATIONAL},
              timeout=timeout)
    except requests.RequestException as e:
        print(f"[warn] session warm-up failed: {e}", file=sys.stderr)
    return s


def fetch(session: requests.Session, url: str, params: dict | None = None,
          retries: int = 3) -> str | None:
    p = {"lang": LANG_EN, "reg": REGION_NATIONAL}
    if params:
        p.update(params)
    timeout = getattr(session, "request_timeout", 30)
    for attempt in range(retries):
        try:
            r = session.get(url, params=p, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return r.text
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"[warn] {url} attempt {attempt+1}/{retries}: {e} "
                  f"(retry in {wait}s)", file=sys.stderr)
            time.sleep(wait)
    return None


# ------------------------------------------------------------------- discovery

def discover_from_listing(session: requests.Session) -> list[dict]:
    """Scrape allRel.aspx. Releases are grouped under ministry headings, so we
    walk the document in order and remember the most recent heading seen."""
    html = fetch(session, LIST_URL)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    content = (soup.select_one("div.content-area")
               or soup.select_one("div.innner-page-main-about-us-content-right-part")
               or soup)

    found: list[dict] = []
    seen: set[str] = set()
    ministry = ""

    for el in content.find_all(["h3", "h2", "h4", "a"]):
        if el.name in ("h2", "h3", "h4"):
            text = el.get_text(" ", strip=True)
            # Ministry headings are short-ish and never contain a release link.
            if text and not el.find("a") and len(text) < 200:
                ministry = text
            continue
        href = el.get("href") or ""
        m = PRID_RE.search(href)
        if not m:
            continue
        prid = m.group(1)
        if prid in seen:
            continue
        seen.add(prid)
        found.append({
            "prid": prid,
            "title_hint": el.get_text(" ", strip=True),
            "ministry_hint": ministry,
        })
    return found


def discover_from_rss(session: requests.Session) -> list[dict]:
    """Fallback: PIB's own feed. Thin, but reliable and always parseable."""
    xml = fetch(session, RSS_FALLBACK,
                {"ModId": "6", "Lang": LANG_EN, "Regid": REGION_NATIONAL})
    if not xml:
        return []
    soup = BeautifulSoup(xml, "html.parser")
    out, seen = [], set()
    for item in soup.find_all("item"):
        link = item.find("link")
        raw = link.get_text(strip=True) if link else ""
        if not raw and link:
            raw = (link.next_sibling or "").strip()
        m = PRID_RE.search(raw)
        if not m:
            continue
        prid = m.group(1)
        if prid in seen:
            continue
        seen.add(prid)
        title = item.find("title")
        out.append({
            "prid": prid,
            "title_hint": title.get_text(strip=True) if title else "",
            "ministry_hint": "",
        })
    return out


def discover(session: requests.Session) -> list[dict]:
    items = discover_from_listing(session)
    if items:
        print(f"[info] listing page: {len(items)} releases", file=sys.stderr)
        return items
    print("[warn] listing page yielded nothing; falling back to RssMain.aspx",
          file=sys.stderr)
    items = discover_from_rss(session)
    print(f"[info] fallback feed: {len(items)} releases", file=sys.stderr)
    return items


# ------------------------------------------------------------------ extraction

def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                return t
    return ""


def parse_posted_at(text: str) -> datetime | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    day, mon, year, hour, minute, ampm = m.groups()
    month = MONTHS.get(mon[:3].lower())
    if not month:
        return None
    hour = int(hour) % 12
    if ampm.upper() == "PM":
        hour += 12
    try:
        return datetime(int(year), month, int(day), hour, int(minute), tzinfo=IST)
    except ValueError:
        return None


def clean_body(node) -> tuple[str, str]:
    """Return (html, plain_text) with chrome stripped out."""
    for junk in node.select(
        "script, style, noscript, .share-btn, .socialShare, .ShareIcons, "
        "table.MsoNormalTable, .pdfPrintBtn, .language-links"
    ):
        junk.decompose()

    for a in node.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            a["href"] = BASE + href
        elif not href.startswith(("http://", "https://", "mailto:")):
            a["href"] = f"{BASE}/{href.lstrip('./')}"
    for img in node.find_all("img", src=True):
        src = img["src"]
        if src.startswith("/"):
            img["src"] = BASE + src

    html = node.decode_contents().strip()
    text = re.sub(r"\n{3,}", "\n\n", node.get_text("\n", strip=True))
    return html, text


def parse_release(session: requests.Session, prid: str, hints: dict,
                  want_body: bool) -> dict | None:
    html = fetch(session, RELEASE_URL, {"PRID": prid})
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", property="og:title")
    title = (og.get("content", "").strip() if og else "") \
        or _first_text(soup, TITLE_SELECTORS) \
        or hints.get("title_hint", "") \
        or f"PIB release {prid}"

    ministry = _first_text(soup, MINISTRY_SELECTORS) or hints.get("ministry_hint", "")
    if ministry.strip() == title.strip():
        ministry = hints.get("ministry_hint", "")

    date_text = _first_text(soup, DATE_SELECTORS)
    posted = parse_posted_at(date_text) or parse_posted_at(soup.get_text(" ", strip=True))

    bureau = ""
    bm = BUREAU_RE.search(date_text or soup.get_text(" ", strip=True))
    if bm:
        bureau = bm.group(1).strip()

    body_html, body_text = "", ""
    if want_body:
        for sel in BODY_SELECTORS:
            node = soup.select_one(sel)
            if node and len(node.get_text(strip=True)) > 120:
                body_html, body_text = clean_body(node)
                break
        if not body_html:
            desc = soup.find("meta", property="og:description")
            if desc:
                body_text = desc.get("content", "").strip()
                body_html = f"<p>{escape(body_text)}</p>"

    summary = body_text[:600].rsplit(" ", 1)[0] + "…" if len(body_text) > 600 else body_text

    return {
        "prid": prid,
        "title": title,
        "link": f"{RELEASE_URL}?PRID={prid}",
        "ministry": ministry,
        "bureau": bureau,
        "posted_at": posted.isoformat() if posted else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "body_html": body_html,
        "summary": summary or title,
    }


# ----------------------------------------------------------------- persistence

def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("items", {})
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] state unreadable ({e}); starting fresh", file=sys.stderr)
        return {"items": {}}


def save_state(path: Path, state: dict, max_items: int) -> None:
    items = sorted(state["items"].values(), key=sort_key, reverse=True)[:max_items]
    state["items"] = {i["prid"]: i for i in items}
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def sort_key(item: dict) -> str:
    return item.get("posted_at") or item.get("fetched_at") or ""


# ------------------------------------------------------------------- feed build

def build_rss(items: list[dict], self_url: str | None) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "<title>PIB India — Press Releases (English)</title>",
        f"<link>{LIST_URL}</link>",
        "<description>All press releases from the Press Information Bureau, "
        "Government of India. Unofficial enriched feed with dates, ministries, "
        "and full text.</description>",
        "<language>en-IN</language>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<generator>pib_rss.py</generator>",
        "<ttl>30</ttl>",
    ]
    if self_url:
        parts.append(f'<atom:link href="{escape(self_url)}" rel="self" '
                     'type="application/rss+xml"/>')

    for it in items:
        parts.append("<item>")
        parts.append(f"<title>{escape(it['title'])}</title>")
        parts.append(f"<link>{escape(it['link'])}</link>")
        parts.append(f"<guid isPermaLink=\"false\">pib-release-{it['prid']}</guid>")
        if it.get("posted_at"):
            try:
                parts.append("<pubDate>"
                             f"{format_datetime(datetime.fromisoformat(it['posted_at']))}"
                             "</pubDate>")
            except ValueError:
                pass
        if it.get("ministry"):
            parts.append(f"<category>{escape(it['ministry'])}</category>")
            parts.append(f"<dc:creator>{escape(it['ministry'])}</dc:creator>")
        if it.get("bureau"):
            parts.append(f"<category>{escape(it['bureau'])}</category>")
        parts.append(f"<description>{escape(it.get('summary') or it['title'])}"
                     "</description>")
        if it.get("body_html"):
            parts.append(f"<content:encoded><![CDATA[{it['body_html']}]]>"
                         "</content:encoded>")
        parts.append("</item>")

    parts += ["</channel>", "</rss>"]
    return "\n".join(parts)


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an RSS feed from pib.gov.in")
    ap.add_argument("--out", default="feed.xml", help="output RSS path")
    ap.add_argument("--state", default="pib_state.json", help="rolling archive path")
    ap.add_argument("--max-items", type=int, default=200,
                    help="items retained in feed/archive")
    ap.add_argument("--limit", type=int, default=60,
                    help="max new releases fetched per run")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between release fetches (be polite)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--no-body", action="store_true",
                    help="skip per-release fetch; metadata only")
    ap.add_argument("--self-url", default=None,
                    help="public URL of the feed, for atom:self")
    args = ap.parse_args()

    state = load_state(Path(args.state))
    known = set(state["items"])

    session = make_session(args.timeout)
    discovered = discover(session)
    if not discovered:
        print("[error] no releases discovered — site layout may have changed",
              file=sys.stderr)
        return 1

    new = [d for d in discovered if d["prid"] not in known][: args.limit]
    print(f"[info] {len(new)} new of {len(discovered)} discovered", file=sys.stderr)

    added = 0
    for i, d in enumerate(new, 1):
        rec = parse_release(session, d["prid"], d, want_body=not args.no_body)
        if rec:
            state["items"][rec["prid"]] = rec
            added += 1
            print(f"  [{i}/{len(new)}] {rec['prid']} {rec['title'][:70]}",
                  file=sys.stderr)
        if i < len(new):
            time.sleep(args.delay)

    items = sorted(state["items"].values(), key=sort_key, reverse=True)[: args.max_items]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_rss(items, args.self_url), encoding="utf-8")
    save_state(Path(args.state), state, args.max_items)

    print(f"[done] +{added} new, {len(items)} items -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
