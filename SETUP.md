# Hosting the feed on GitHub Actions + Pages

End result: a public URL like `https://<you>.github.io/pib-rss/feed.xml`, rebuilt hourly, that you can paste into any RSS reader.

## 1. Push the repo

From `pib-rss/`:

```bash
git init -b main
git add .
git commit -m "PIB RSS feed generator"
gh repo create pib-rss --public --source=. --push
```

No `gh` CLI? Create an empty **public** repo named `pib-rss` on github.com, then:

```bash
git remote add origin https://github.com/<you>/pib-rss.git
git push -u origin main
```

Public matters: GitHub Pages on private repos requires a paid plan, and Actions minutes are free only on public repos.

## 2. Turn on Pages

Repo → **Settings** → **Pages** → Source: **Deploy from a branch** → Branch: `main`, folder: **`/docs`** → Save.

First publish takes a minute or two. `index.html` is already in `docs/`, so the landing page appears at `https://<you>.github.io/pib-rss/` and the feed sits beside it at `/feed.xml`.

## 3. Let the workflow write back

Repo → **Settings** → **Actions** → **General** → *Workflow permissions* → select **Read and write permissions** → Save.

The workflow commits `docs/feed.xml` and `pib_state.json` after each run. That state file is what makes this a feed instead of a snapshot — without it every run would rediscover the same releases and the archive would never grow past a single day.

## 4. Kick off the first run

Repo → **Actions** → **Build PIB feed** → **Run workflow**.

The log ends with a line like `47 items | 47 with pubDate | 47 with full text`. Those three numbers should track each other. If the second or third drops below 80% of the first, the job emits a warning rather than failing — a stale-but-valid feed beats no feed — but it means PIB reskinned something and the selectors in `pib_rss.py` need a look.

That count is also the honest version of the `grep` check from earlier: `grep -c` counts *lines*, and release bodies contain newlines, so `content:encoded` legitimately appears on two lines per item.

## 5. Subscribe

```
https://<you>.github.io/pib-rss/feed.xml
```

## 6. Telegram digest (optional)

Official Telegram Bot API — no third-party bridge, no phone number shared with anyone, free with no message quota.

**Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather):

1. Send `/newbot`
2. Give it a name (display name) and a username (must end in `bot`, e.g. `pib_feed_bot`)
3. BotFather replies with a token like `8123456789:AAH...`. Treat it like a password.

**Add the bot to your group.** Group → name at top → **Add members** → search your bot's `@username` → add.

**Get the group's chat id.** Two Telegram behaviours matter here:

- A bot can't see any chat until it receives something from it.
- In groups, bots run in *privacy mode* by default and **cannot see ordinary messages** — only ones starting with `/`. This is the step people get stuck on: sending "hi" in the group does nothing.

So, in the group, send:

```
/start
```

Then run locally:

```bash
TELEGRAM_BOT_TOKEN=<your-token> python notify.py --get-chat-id
```

Output looks like:

```
  -1002345678901   supergroup  PIB Updates  ← group
  987654321        private     Ben
```

Take the negative number — **include the minus sign**.

**Confirm it lands** before wiring up the workflow:

```bash
TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=-100... python notify.py --test
```

A test message should appear in the group. If Telegram says *chat not found* the bot isn't a member; if it says *no rights to send*, the group restricts posting and the bot needs permission.

**Add the secrets.** Repo → **Settings → Secrets and variables → Actions → New repository secret**, twice:

- `TELEGRAM_BOT_TOKEN` — the token from BotFather
- `TELEGRAM_CHAT_ID` — the number from the previous step

Until both exist, `notify.py` exits quietly and sends nothing, so the workflow is safe to run before you get to this.

**What arrives:** one message per release — headline (linked, capitalisation exactly as PIB published it), then ministry and timestamp. Sent oldest first so the group reads in publication order.

Messages are spaced 3 seconds apart and capped at 20 per run, because Telegram rate-limits a single group to roughly 20 messages a minute. Anything over the cap goes out on the next hourly run rather than being dropped. If Telegram does return a rate-limit error, the script waits the exact backoff Telegram asks for and retries once.

Delivery is recorded **per release, per chat**, so a failure partway through a batch never causes the already-sent ones to repeat.

### Posting to more than one group

`TELEGRAM_CHAT_ID` accepts several ids, comma-separated:

```
-1002345678901,-1009876543210
```

For each additional group: add the bot to it, send `/start` in it, run `--get-chat-id`, and append the new id to the secret.

**A newly added group is baselined, not backfilled.** It starts receiving from the next new release rather than replaying the archive — the same behaviour as the first group on day one. Each group keeps its own ledger, so if one group is unreachable for a while, the others keep receiving normally and the failed one catches up when it recovers, without duplicating anything.

Verify all configured groups at once:

```bash
TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=<id1>,<id2> python notify.py --test
```

**The first run after you add the secrets sends nothing.** It records the current feed as a baseline so you don't receive the entire backlog at once. Digests start with the next new release.

Preview the message without sending:

```bash
python notify.py --dry-run
```

**Want it muted at night?** Telegram's per-chat notification settings handle that — mute the group and the messages still arrive silently.

**If digests suddenly stop arriving in the group**, check whether the chat id changed. A *basic group* is assigned a new id when Telegram converts it to a *supergroup*, which happens automatically on certain changes (adding an admin, making it public, growing past the member limit). Re-run `--get-chat-id` and update the secret. Groups that already show as `supergroup` above are stable and won't do this.

## Things that will bite you eventually

**Scheduled workflows get disabled after 60 days of repo inactivity.** GitHub does this to every repo. This one dodges it by design — each run that finds new releases pushes a commit, which counts as activity. But if PIB goes quiet for two months, or the job breaks silently and stops committing, the schedule switches itself off. If the feed goes stale, check the Actions tab first.

**Cron is best-effort.** GitHub delays scheduled jobs under load, sometimes by 20+ minutes. `:17` avoids the congested top of the hour, but don't expect punctuality. Nothing breaks — the next run catches up, since discovery is based on what's new rather than on elapsed time.

**`--limit 80` caps each run at 80 new releases.** PIB's heavy days can exceed that. Runs are hourly, so the backlog drains on its own; raise it if you ever start a feed from cold and want to catch up faster.

**Be polite.** `--delay 1.5` spaces out requests to a government host. Please don't lower it.
