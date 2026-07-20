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
  2. Add the bot to your group, then send "/start" IN the group. A plain
     message will not work: bots run in privacy mode and only see commands.
  3. Run:  TELEGRAM_BOT_TOKEN=<token> python notify.py --get-chat-id
     Group ids are negative — keep the minus sign.
  4. Add both as GitHub repo secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

Usage:
    python notify.py --state pib_state.json --max 8
    python notify.py --dry-run          # print the message, send nothing
    python notify.py --get-chat-id      # look up chat ids the bot can reach
    python notify.py --test             # send a test message to the chat
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
    """Print chat ids from recent updates.

    Two things reliably trip people up here:
    - Bots can't see a chat until someone messages them in it.
    - In groups, Telegram's default privacy mode hides ordinary messages from
      bots. Commands starting with '/' always get through, which is why the
      instructions say to send /start rather than 'hi'.
    """
    result = api_call(token, "getUpdates", {})
    if result is None:
        return 1

    chats = {}
    for upd in result:
        msg = (upd.get("message") or upd.get("channel_post")
               or upd.get("my_chat_member") or {})
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = (chat.get("title") or chat.get("username")
                    or chat.get("first_name", ""))
            chats[chat["id"]] = (chat.get("type", "?"), name)

    if not chats:
        print("No chats found.\n"
              "  • Private chat: send your bot any message, then re-run.\n"
              "  • Group: add the bot to the group, then send '/start' IN the\n"
              "    group. Plain messages stay hidden from bots by default, so\n"
              "    a command is required.",
              file=sys.stderr)
        return 1

    groups = {c: v for c, v in chats.items() if v[0] in ("group", "supergroup")}
    print("Chat ids found:\n")
    for cid, (ctype, name) in chats.items():
        tag = "  ← group" if ctype in ("group", "supergroup") else ""
        print(f"  {cid:<16} {ctype:<11} {name}{tag}")

    print("\nAdd the id you want as the TELEGRAM_CHAT_ID repo secret.")
    if groups:
        print("Group ids are negative — include the minus sign.")
        if any(t == "group" for t, _ in groups.values()):
            print("\nNote: a basic 'group' gets a NEW id if Telegram ever\n"
                  "upgrades it to a supergroup (which happens automatically on\n"
                  "certain admin changes). If digests stop arriving, re-run\n"
                  "this and update the secret.")
    return 0


def send_test(token: str, chat_id: str) -> int:
    text = ('<b>PIB feed — test</b>\n\nIf you can read this, digests will '
            f'arrive here.\n\n<a href="{READER_URL}">Open reader</a>')
    if send(token, chat_id, text):
        return 0
    print("\nIf Telegram said 'chat not found' or 'bot is not a member', add "
          "the bot to the group first.\nIf it said 'have no rights to send', "
          "give it permission to post in the group settings.", file=sys.stderr)
    return 1


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
    ap.add_argument("--test", action="store_true",
                    help="send a one-off test message to TELEGRAM_CHAT_ID")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if args.get_chat_id:
        if not token:
            print("Set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
            return 1
        return get_chat_id(token)

    if args.test:
        if not (token and chat_id):
            print("Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.",
                  file=sys.stderr)
            return 1
        return send_test(token, chat_id)

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
