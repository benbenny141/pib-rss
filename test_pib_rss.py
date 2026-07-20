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
found = P.discover(FakeSession())
check("finds 3 unique PRIDs (dedupes)", len(found) == 3, [f["prid"] for f in found])
check("PMO ministry attributed", found[0]["ministry_hint"] == "Prime Minister's Office", found[0])
check("MHA ministry attributed", found[1]["ministry_hint"] == "Ministry of Home Affairs", found[1])
check("relative + absolute hrefs both parsed",
      {f["prid"] for f in found} == {"2286456", "2286385", "2286352"})

print("\n[2] RSS fallback when listing is empty")
P.fetch = fake_fetch("empty")
fb = P.discover(FakeSession())
check("falls back to RssMain", len(fb) == 1 and fb[0]["prid"] == "999", fb)

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

print("\n[5] ministry != title guard")
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
    check("corrupt state recovers", P.load_state(Path(td)/"bad.json") == {"items": {}})

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'All checks passed.'}")
sys.exit(1 if fails else 0)
