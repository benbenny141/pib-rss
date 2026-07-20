#!/usr/bin/env python3
"""
notify.py — Telegram digest of new PIB releases.

Sends ONE digest per run rather than one message per release: PIB can publish
dozens of items an hour. Notified releases are recorded in the state file, so
nothing is ever sent twice.

Inert by default. Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in the
environment it exits 0 without sending, so the workflow is safe to run before
you finish setup.

Setup (needs your own Telegram account):
  1. Message @BotFather, send /newbot, follow the prompts, copy the token.
  2. Send your new bot any message (say "hi") so it's allowed to reply to you.
  3. Run:  TELEGRAM_BOT_TOKEN=<token> python notify.py --get-chat-id
  4. Add both as GitHub repo secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

Usage:
    python notify.py --state pib_state.json --max 8
    python notify.py --dry-run          # print the message, send nothing
    python notify.py --get-chat-id      # look up your chat id
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
READER_URL = "https://benbenny141.github.io/pib-rss/"
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3800     # headroom, since HTML entities expand the payload


def sort_key(item: dict) -> str:
    return item.get("posted_at") or item.get("fetched_at") or ""


def esc(s: str) -> str:
    """Telegram HTML parse_mode requires these three escaped, nothing else."""
    return html.escape(s or "", quote=False)


def build_digest(new_items: list[dict], max_items: int) -> str:
    total = len(new_items)
    head = f"<b>PIB — {total} new release{'' if total == 1 else 's'}</b>"
    lines = [head, ""]

    for it in new_items[:max_items]:
        title = it["title"]
        if len(title) > 120:
            title = title[:117].rsplit(" ", 1)[0] + "…"
        lines.append(f"• <a href=\"{esc(it['link'])}\">{esc(title)}</a>")
        if it.get("ministry"):
            lines.append(f"  <i>{esc(it['ministry'])}</i>")
        lines.append("")

    remaining = total - min(total, max_items)
    if remaining:
        lines.append(f"…and {remaining} more.")
        lines.append("")
    lines.append(f'<a href="{READER_URL}">Open reader</a>')

    msg = "\n".join(lines)
    if len(msg) > SAFE_LIMIT:
        # Trim whole lines so we never cut an HTML tag in half.
        while len(msg) > SAFE_LIMIT and "\n" in msg:
            msg = msg.rsplit("\n", 1)[0]
        msg += f'\n…\n<a href="{READER_URL}">Open reader</a>'
    return msg


def api_call(token: str, method: str, params: dict, timeout: int = 30):
    try:
        r = requests.post(API.format(token=token, method=method),
                          data=params, timeout=timeout)
    except requests.RequestException as e:
        print(f"[error] request failed: {e}", file=sys.stderr)
        return None
    try:
        payload = r.json()
    except ValueError:
        print(f"[error] non-JSON reply: {r.text[:200]}", file=sys.stderr)
        return None
    if not payload.get("ok"):
        # description is where Telegram puts the actual reason.
        print(f"[error] Telegram: {payload.get('description', r.text[:200])}",
              file=sys.stderr)
        return None
    return payload["result"]


def get_chat_id(token: str) -> int:
    """Print chat ids from recent updates. The bot only sees messages sent to
    it after creation, so the user must message it first."""
    result = api_call(token, "getUpdates", {})
    if result is None:
        return 1
    chats = {}
    for upd in result:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("username") or chat.get("title") or chat.get("first_name", "")
            chats[chat["id"]] = f"{chat.get('type', '?')} {name}".strip()
    if not chats:
        print("No messages found. Send your bot a message in Telegram first, "
              "then re-run this.", file=sys.stderr)
        return 1
    print("Chat ids found:")
    for cid, desc in chats.items():
        print(f"  {cid}   ({desc})")
    print("\nAdd the id above as the TELEGRAM_CHAT_ID repo secret.")
    return 0


def send(token: str, chat_id: str, text: str) -> bool:
    ok = api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if ok:
        print(f"[ok] digest delivered ({len(text)} chars)", file=sys.stderr)
    return ok is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram digest of new PIB releases")
    ap.add_argument("--state", default="pib_state.json")
    ap.add_argument("--max", type=int, default=8,
                    help="releases listed before the digest says '…and N more'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true",
                    help="print chat ids the bot can reach, then exit")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if args.get_chat_id:
        if not token:
            print("Set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
            return 1
        return get_chat_id(token)

    if not args.dry_run and not (token and chat_id):
        print("[skip] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set; not sending",
              file=sys.stderr)
        return 0

    path = Path(args.state)
    if not path.exists():
        print(f"[skip] no state at {path}", file=sys.stderr)
        return 0
    state = json.loads(path.read_text(encoding="utf-8"))

    notified = set(state.get("notified", []))
    items = list(state.get("items", {}).values())

    # First run: record a baseline silently instead of blasting the backlog.
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

    msg = build_digest(fresh, args.max)

    if args.dry_run:
        print("--- dry run, message not sent ---")
        print(msg)
        return 0

    if not send(token, chat_id, msg):
        return 1   # state untouched, so the next run retries these items

    live = set(state.get("items", {}))
    state["notified"] = sorted((notified | {i["prid"] for i in fresh}) & live)
    state["notified_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[done] notified {len(fresh)} release(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
