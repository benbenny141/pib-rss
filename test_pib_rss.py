#!/usr/bin/env python3
"""Offline tests for pib_rss.py using fixtures modelled on real PIB markup."""
import sys, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pib_rss as P

LISTING = """<html><body><div class="content-area">
<h3 class="font104">Prime Minister's Office</h3>
<ul><li><a href="https://www.pib.gov.in/PressReleasePage.aspx?PRID=2286456"
  title="t">English rendering of PM's address</a></li></ul>
<h3 class="font104">Ministry of Home Affairs</h3>
<ul><li><a href="/PressReleasePage.aspx?PRID=2286385">Amit Shah lays foundation stone</a></li>
<li><a href="PressReleasePage.aspx?PRID=2286352">Museum of Word inaugurated</a></li>
<li><a href="/PressReleasePage.aspx?PRID=2286385">dupe should be dropped</a></li></ul>
</div></body></html>"""

RELEASE = """<html><head>
<meta property="og:title" content="English rendering of PM's address at Monsoon Session 2026"/>
<meta property="og:description" content="Welcome to you all."/></head><body>
<div class="ReleaseLefttag"><h2>Prime Minister's Office</h2></div>
<div class="innner-page-main-about-us-content-right-part">
<h2>English rendering of PM's address at Monsoon Session 2026</h2>
<div class="ReleaseDateSubHeaddateTime">Posted On: 20 JUL 2026 11:11AM by PIB Delhi</div>
<div id="PdfDiv"><p>Welcome to you all. The monsoon session begins today and the
country has picked up speed on many fronts including space, semiconductors and
green hydrogen rail. <a href="/PressReleasePage.aspx?PRID=1">related</a>
<img src="/images/x.png"/></p><p>MJPS/SS/VJ/RK</p></div>
<script>var junk=1;</script><div class="socialShare">Share on facebook</div>
</div></body></html>"""

RSSX = """<?xml version="1.0"?><rss version="2.0"><channel><item>
<title>Fallback item</title><link>https://pib.gov.in/PressReleaseIframePage.aspx?PRID=999</link>
</item></channel></rss>"""

fails = []
def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + (f" -> {detail}" if not cond else ""))
    if not cond: fails.append(name)

class FakeSession:
    request_timeout = 5
def fake_fetch(kind):
    def f(session, url, params=None, retries=3):
        if url == P.LIST_URL: return LISTING if kind == "listing" else ""
        if url == P.RSS_FALLBACK: return RSSX
        if url == P.RELEASE_URL: return RELEASE
        return ""
    return f

print("\n[1] listing discovery")
P.fetch = fake_fetch("listing")
found = P.discover_from_listing(FakeSession())
check("finds 3 unique PRIDs (dedupes)", len(found) == 3, [f["prid"] for f in found])
check("PMO ministry attributed", found[0]["ministry_hint"] == "Prime Minister's Office", found[0])
check("MHA ministry attributed", found[1]["ministry_hint"] == "Ministry of Home Affairs", found[1])
check("relative + absolute hrefs both parsed",
      {f["prid"] for f in found} == {"2286456", "2286385", "2286352"})

print("\n[2] two-source union (allRel is cache-flaky; RSS is fresher)")
P.fetch = fake_fetch("listing")
u = P.discover(FakeSession())
check("union = listing + rss-only", len(u) == 4, [x["prid"] for x in u])
check("rss-only PRID included", "999" in {x["prid"] for x in u})
check("listing ministry survives merge",
      next(x for x in u if x["prid"] == "2286456")["ministry_hint"] == "Prime Minister's Office")

P.fetch = fake_fetch("empty")
fb = P.discover(FakeSession())
check("RSS alone carries a stale/empty listing", len(fb) == 1 and fb[0]["prid"] == "999", fb)

print("\n[3] date parsing")
d = P.parse_posted_at("Posted On: 20 JUL 2026 11:11AM by PIB Delhi")
check("parses 11:11AM IST", d and (d.year, d.month, d.day, d.hour, d.minute) == (2026,7,20,11,11), d)
pm = P.parse_posted_at("Posted On: 5 JAN 2026 07:30PM by PIB Mumbai")
check("PM converts to 19:30", pm and pm.hour == 19, pm)
noon = P.parse_posted_at("1 MAR 2026 12:05AM")
check("12:05AM -> hour 0", noon and noon.hour == 0, noon)
check("garbage returns None", P.parse_posted_at("no date at all") is None)

print("\n[4] release extraction")
P.fetch = fake_fetch("listing")
rec = P.parse_release(FakeSession(), "2286456",
                      {"ministry_hint": "Prime Minister's Office"}, want_body=True)
check("title from og:title", rec["title"].startswith("English rendering"), rec["title"])
check("ministry extracted", rec["ministry"] == "Prime Minister's Office", rec["ministry"])
check("bureau extracted", rec["bureau"] == "PIB Delhi", rec["bureau"])
check("posted_at set", rec["posted_at"] and "2026-07-20T11:11" in rec["posted_at"], rec["posted_at"])
check("body captured", "monsoon session begins" in rec["body_html"], rec["body_html"][:80])
check("script stripped", "var junk" not in rec["body_html"])
check("share widget stripped", "Share on facebook" not in rec["body_html"])
check("relative link absolutised", "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1" in rec["body_html"])
check("relative img absolutised", "https://www.pib.gov.in/images/x.png" in rec["body_html"])
check("summary non-empty", len(rec["summary"]) > 20, rec["summary"])

print("\n[5] Hindi -> English twin resolution (RssMain ignores Lang=1)")
HINDI = """<html><head>
<meta property="og:title" content="संसद के मानसून सत्र 2026 की शुरुआत"/></head><body>
<div class="ReleaseDateSubHeaddateTime">20 JUL 2026 11:11AM by PIB Delhi</div>
<div id="PdfDiv"><p>स्वागत है भाई आप सबका। यह हिंदी संस्करण है।</p></div>
<p>Read this release in: <a href="https://pib.gov.in/PressReleasePage.aspx?PRID=2286456">English</a></p>
</body></html>"""
ORPHAN = HINDI.replace('<a href="https://pib.gov.in/PressReleasePage.aspx?PRID=2286456">English</a>', '')

def lang_fetch(session, url, params=None, retries=3):
    if url == P.RELEASE_URL:
        return {"2286457": HINDI, "2286456": RELEASE, "777": ORPHAN}.get(params["PRID"], "")
    return ""

check("is_hindi detects Devanagari", P.is_hindi("प्रधानमंत्री") and not P.is_hindi("Prime Minister"))
P.fetch = lang_fetch
hop = P.parse_release(FakeSession(), "2286457", {}, want_body=True)
check("Hindi PRID resolves to English twin", hop and hop["prid"] == "2286456", hop and hop["prid"])
check("resolved title is English", hop and not P.is_hindi(hop["title"]), hop and hop["title"][:40])
check("alias recorded for dedup", hop.get("hindi_prid") == "2286457", hop.get("hindi_prid"))
check("body is English, not Hindi", "monsoon session" in hop["body_html"].lower())
orphan = P.parse_release(FakeSession(), "777", {}, want_body=False)
check("Hindi with no twin is dropped", orphan is None, orphan)
check("no infinite recursion (_hop caps)",
      P.parse_release(FakeSession(), "2286457", {}, want_body=False, _hop=1) is not None)

print("\n[5b] alias pruning in state")
import tempfile as _tf
with _tf.TemporaryDirectory() as td:
    sp = Path(td) / "s.json"
    st = {"items": {"100": {"prid": "100", "posted_at": "2026-07-20T10:00:00+05:30", "title": "keep"}},
          "aliases": {"999": "100", "888": "gone"}}
    P.save_state(sp, st, max_items=10)
    al = P.load_state(sp)["aliases"]
    check("alias to retained item kept", al.get("999") == "100", al)
    check("alias to dropped item pruned", "888" not in al, al)

print("\n[5c] ministry != title guard")
P.fetch = fake_fetch("listing")
rec2 = P.parse_release(FakeSession(), "1", {"ministry_hint": "Ministry of Coal"}, want_body=False)
check("no body when --no-body", rec2["body_html"] == "")

print("\n[6] feed generation + XML validity")
xml = P.build_rss([rec], self_url="https://example.com/pib.xml")
root = ET.fromstring(xml.encode())
ch = root.find("channel")
items = ch.findall("item")
check("well-formed XML", root.tag == "rss")
check("one item", len(items) == 1)
it = items[0]
check("has pubDate", it.find("pubDate") is not None and "Jul 2026" in it.find("pubDate").text,
      it.find("pubDate") is not None and it.find("pubDate").text)
check("guid stable", it.find("guid").text == "pib-release-2286456")
check("category = ministry", [c.text for c in it.findall("category")][0] == "Prime Minister's Office")
check("bureau category present", "PIB Delhi" in [c.text for c in it.findall("category")])
ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
check("content:encoded present", it.find("content:encoded", ns) is not None)
check("atom:self present", ch.find("{http://www.w3.org/2005/Atom}link") is not None)

print("\n[7] escaping hostile input")
nasty = dict(rec, title='Steel & Coal <script>alert("x")</script> "quoted"',
             ministry="R&D", summary="a < b & c", body_html="", posted_at=None)
x2 = ET.fromstring(P.build_rss([nasty], None).encode())
t = x2.find("channel/item/title").text
check("ampersand/tags escaped safely", "&" in t and "<script>" in t, t)
check("missing pubDate tolerated", x2.find("channel/item/pubDate") is None)

print("\n[8] state round-trip + ordering")
import tempfile, json
with tempfile.TemporaryDirectory() as td:
    sp = Path(td) / "s.json"
    st = {"items": {"1": {"prid":"1","posted_at":"2026-07-01T10:00:00+05:30","title":"old"},
                    "2": {"prid":"2","posted_at":"2026-07-20T10:00:00+05:30","title":"new"},
                    "3": {"prid":"3","fetched_at":"2026-07-19T10:00:00+00:00","title":"nodate"}}}
    P.save_state(sp, st, max_items=10)
    back = P.load_state(sp)
    check("state persists 3", len(back["items"]) == 3)
    order = [i["title"] for i in sorted(back["items"].values(), key=P.sort_key, reverse=True)]
    check("newest first", order[0] == "new", order)
    P.save_state(sp, back, max_items=2)
    check("max_items trims oldest", len(P.load_state(sp)["items"]) == 2)
    (Path(td)/"bad.json").write_text("{ broken")
    check("corrupt state recovers",
          P.load_state(Path(td)/"bad.json") == {"items": {}, "aliases": {}})
    (Path(td)/"legacy.json").write_text('{"items": {}}')  # pre-alias state file
    check("legacy state upgrades cleanly",
          P.load_state(Path(td)/"legacy.json") == {"items": {}, "aliases": {}})

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'All checks passed.'}")
sys.exit(1 if fails else 0)
