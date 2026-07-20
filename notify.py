#!/usr/bin/env python3
"""
notify.py — Telegram notifications for new PIB releases, one message per release.

Each new release is sent as its own Telegram message, oldest first so the chat
reads chronologically. Delivery is recorded per release, so a failure partway
through never causes the already-sent ones to be repeated.

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
    python notify.py --state pib_state.json --max 20
    python notify.py --dry-run          # print the messages, send nothing
    python notify.py --get-chat-id      # look up chat ids the bot can reach
    python notify.py --test             # send a test message to the chat
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
READER_URL = "https://benbenny141.github.io/pib-rss/"
IST = timezone(timedelta(hours=5, minutes=30))

# Telegram allows roughly 20 messages/minute to one group before it starts
# returning 429. 3s spacing keeps a comfortable margin.
DEFAULT_DELAY = 3.0
MAX_CONSECUTIVE_FAILURES = 3


def sort_key(item: dict) -> str:
    return item.get("posted_at") or item.get("fetched_at") or ""


def esc(s: str) -> str:
    """Telegram HTML parse_mode requires these three escaped, nothing else."""
    return html.escape(s or "", quote=False)


def fmt_when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone(IST)
    except ValueError:
        return ""
    return dt.strftime("%d %b %Y, %-I:%M %p IST")


def build_message(it: dict) -> str:
    """One release. Title verbatim — PIB's capitalisation is left as published."""
    lines = [f'<a href="{esc(it["link"])}"><b>{esc(it["title"])}</b></a>']
    meta = [esc(it["ministry"])] if it.get("ministry") else []
    when = fmt_when(it.get("posted_at"))
    if when:
        meta.append(when)
    if meta:
        lines.append("")
        lines.append(" · ".join(meta))
    return "\n".join(lines)


def api_call(token: str, method: str, params: dict, timeout: int = 30) -> dict | None:
    """Return Telegram's JSON payload, or None if the request itself failed."""
    try:
        r = requests.post(API.format(token=token, method=method),
                          data=params, timeout=timeout)
    except requests.RequestException as e:
        print(f"[error] request failed: {e}", file=sys.stderr)
        return None
    try:
        return r.json()
    except ValueError:
        print(f"[error] non-JSON reply: {r.text[:200]}", file=sys.stderr)
        return None


def send(token: str, chat_id: str, text: str) -> tuple[bool, int | None]:
    """Returns (ok, retry_after). retry_after is set when Telegram rate-limits."""
    payload = api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if payload is None:
        return False, None
    if payload.get("ok"):
        return True, None

    desc = payload.get("description", "unknown error")
    retry_after = (payload.get("parameters") or {}).get("retry_after")
    if retry_after:
        print(f"[warn] rate limited, retry after {retry_after}s", file=sys.stderr)
        return False, int(retry_after)
    print(f"[error] Telegram: {desc}", file=sys.stderr)
    return False, None


def get_chat_id(token: str) -> int:
    """Print chat ids from recent updates.

    Two things reliably trip people up here:
    - Bots can't see a chat until someone messages them in it.
    - In groups, Telegram's default privacy mode hides ordinary messages from
      bots. Commands starting with '/' always get through, which is why the
      instructions say to send /start rather than 'hi'.
    """
    payload = api_call(token, "getUpdates", {})
    if payload is None:
        return 1
    if not payload.get("ok"):
        print(f"[error] Telegram: {payload.get('description')}", file=sys.stderr)
        return 1
    result = payload["result"]

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
                  "certain admin changes). If messages stop arriving, re-run\n"
                  "this and update the secret.")
    return 0


def send_test(token: str, chat_id: str) -> int:
    text = ('<b>PIB feed — test</b>\n\nIf you can read this, releases will '
            f'arrive here.\n\n<a href="{READER_URL}">Open reader</a>')
    ok, _ = send(token, chat_id, text)
    if ok:
        print("[ok] test message delivered", file=sys.stderr)
        return 0
    print("\nIf Telegram said 'chat not found' or 'bot is not a member', add "
          "the bot to the group first.\nIf it said 'have no rights to send', "
          "give it permission to post in the group settings.", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Telegram notifications for new PIB releases")
    ap.add_argument("--state", default="pib_state.json")
    ap.add_argument("--max", type=int, default=20,
                    help="max messages per run; the rest go out next run")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="seconds between messages (Telegram rate limits)")
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
        print(f"[init] baseline set at {len(items)} items; nothing sent",
              file=sys.stderr)
        return 0

    # Oldest first, so the chat reads in the order PIB published.
    fresh = sorted([i for i in items if i["prid"] not in notified], key=sort_key)
    if not fresh:
        print("[skip] nothing new since last run", file=sys.stderr)
        return 0

    batch = fresh[: args.max]
    deferred = len(fresh) - len(batch)
    print(f"[info] {len(fresh)} new; sending {len(batch)}"
          + (f", {deferred} deferred to next run" if deferred else ""),
          file=sys.stderr)

    if args.dry_run:
        print("--- dry run, nothing sent ---")
        for it in batch:
            print(build_message(it))
            print("-" * 40)
        return 0

    sent = 0
    consecutive_failures = 0
    for n, it in enumerate(batch, 1):
        text = build_message(it)
        ok, retry_after = send(token, chat_id, text)

        # One retry on rate limit, honouring Telegram's own backoff figure.
        if not ok and retry_after:
            time.sleep(retry_after + 1)
            ok, _ = send(token, chat_id, text)

        if ok:
            notified.add(it["prid"])
            sent += 1
            consecutive_failures = 0
            print(f"  [{n}/{len(batch)}] {it['prid']} {it['title'][:60]}",
                  file=sys.stderr)
        else:
            consecutive_failures += 1
            print(f"  [{n}/{len(batch)}] FAILED {it['prid']}", file=sys.stderr)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"[abort] {MAX_CONSECUTIVE_FAILURES} consecutive "
                      "failures; stopping so the rest retry next run",
                      file=sys.stderr)
                break

        if n < len(batch):
            time.sleep(args.delay)

    # Persist whatever actually got through, so successes are never repeated.
    live = set(state.get("items", {}))
    state["notified"] = sorted(notified & live)
    if sent:
        state["notified_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                    encoding="utf-8")

    failed = len(batch) - sent
    if failed:
        # The workflow step is continue-on-error so a Telegram outage can't
        # block feed publication. Raise an annotation so it isn't hidden
        # behind a green check.
        print(f"::error title=Telegram delivery incomplete::"
              f"{sent} sent, {failed} failed; failures retry next run")
        return 1

    print(f"[done] sent {sent} release(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
