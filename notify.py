#!/usr/bin/env python3
"""
notify.py — WhatsApp digest of new PIB releases, via the CallMeBot bridge.

Sends ONE digest message per run rather than one message per release: PIB can
publish dozens of items an hour, and CallMeBot rate-limits (and you would hate
your phone). Items already notified are recorded in the state file so nothing
is ever sent twice.

Inert by default. With no CALLMEBOT_PHONE / CALLMEBOT_APIKEY in the environment
it exits 0 without sending, so the workflow runs fine before you set it up.

Setup (do this yourself — it needs your own WhatsApp):
  1. Add +34 644 51 95 23 to your phone contacts as "CallMeBot".
  2. WhatsApp it: "I allow callmebot to send me messages"
  3. It replies with your API key.
  4. Add both as GitHub repo secrets: CALLMEBOT_PHONE (e.g. +919876543210)
     and CALLMEBOT_APIKEY.

Usage:
    python notify.py --state pib_state.json --max 8
    python notify.py --dry-run          # print the message, send nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://api.callmebot.com/whatsapp.php"
READER_URL = "https://benbenny141.github.io/pib-rss/"
MAX_WHATSAPP_CHARS = 3500   # real limit ~4096; leave headroom


def sort_key(item: dict) -> str:
    return item.get("posted_at") or item.get("fetched_at") or ""


def build_digest(new_items: list[dict], total_unsent: int, max_items: int) -> str:
    lines = [f"*PIB — {len(new_items)} new release" + ("s*" if len(new_items) != 1 else "*")]
    lines.append("")

    for it in new_items[:max_items]:
        title = it["title"]
        if len(title) > 110:
            title = title[:107].rsplit(" ", 1)[0] + "…"
        ministry = it.get("ministry", "")
        lines.append(f"• {title}")
        if ministry:
            lines.append(f"  _{ministry}_")
        lines.append(f"  {it['link']}")
        lines.append("")

    remaining = total_unsent - min(len(new_items), max_items)
    if remaining > 0:
        lines.append(f"…and {remaining} more.")
        lines.append("")
    lines.append(READER_URL)

    msg = "\n".join(lines)
    if len(msg) > MAX_WHATSAPP_CHARS:
        msg = msg[:MAX_WHATSAPP_CHARS].rsplit("\n", 1)[0] + f"\n…\n{READER_URL}"
    return msg


def send(phone: str, apikey: str, text: str, timeout: int = 30) -> bool:
    """CallMeBot returns 200 with an HTML body; errors show up in that body
    rather than the status code, so check both."""
    try:
        r = requests.get(
            API,
            params={"phone": phone, "text": text, "apikey": apikey},
            timeout=timeout,
        )
    except requests.RequestException as e:
        print(f"[error] request failed: {e}", file=sys.stderr)
        return False

    body = (r.text or "").lower()
    if r.status_code != 200:
        print(f"[error] HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    if "error" in body or "apikey" in body and "invalid" in body:
        print(f"[error] CallMeBot rejected the request: {r.text[:200]}",
              file=sys.stderr)
        return False
    print(f"[ok] digest delivered ({len(text)} chars)", file=sys.stderr)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="WhatsApp digest of new PIB releases")
    ap.add_argument("--state", default="pib_state.json")
    ap.add_argument("--max", type=int, default=8,
                    help="releases listed in the digest before it says '…and N more'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not args.dry_run and not (phone and apikey):
        print("[skip] CALLMEBOT_PHONE/CALLMEBOT_APIKEY not set; not sending",
              file=sys.stderr)
        return 0

    path = Path(args.state)
    if not path.exists():
        print(f"[skip] no state at {path}", file=sys.stderr)
        return 0
    state = json.loads(path.read_text(encoding="utf-8"))

    notified = set(state.get("notified", []))
    items = list(state.get("items", {}).values())

    # First ever run: mark everything as seen and stay silent, rather than
    # blasting the entire backlog at your phone.
    if not notified and items:
        state["notified"] = [i["prid"] for i in items]
        path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"[init] baseline set at {len(items)} items; no message sent",
              file=sys.stderr)
        return 0

    fresh = sorted([i for i in items if i["prid"] not in notified],
                   key=sort_key, reverse=True)
    if not fresh:
        print("[skip] nothing new since last digest", file=sys.stderr)
        return 0

    msg = build_digest(fresh, len(fresh), args.max)

    if args.dry_run:
        print("--- dry run, message not sent ---")
        print(msg)
        return 0

    if not send(phone, apikey, msg):
        return 1   # leave state untouched so the next run retries

    state["notified"] = sorted(notified | {i["prid"] for i in fresh})
    # Keep the ledger bounded; it only needs to cover items still in the feed.
    live = set(state.get("items", {}))
    state["notified"] = sorted(set(state["notified"]) & live)
    state["notified_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[done] notified {len(fresh)} release(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
