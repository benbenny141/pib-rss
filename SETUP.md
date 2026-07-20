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

## 6. WhatsApp digest (optional)

Delivery goes through [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/), a free bridge that can only message *your own* number — that restriction is how they prevent spam, and it fits this use case exactly.

**Read this before setting it up.** CallMeBot is an unofficial third party. Your phone number gets registered with their service, and every digest passes through their servers. PIB press releases are public government information, so there's nothing sensitive in transit here — that's the main reason this is an acceptable trade. I wouldn't route private content through it. The service is free and run by one person; it can change or disappear without notice, and if it does, the workflow will log an error but keep building the feed normally.

Setup, which only you can do since it involves your WhatsApp:

1. Save **+34 644 51 95 23** to your phone contacts as "CallMeBot".
2. From WhatsApp, message it exactly: `I allow callmebot to send me messages`
3. Wait for the reply containing your API key.
4. In the repo: **Settings → Secrets and variables → Actions → New repository secret**, twice:
   - `CALLMEBOT_PHONE` — your number with country code, e.g. `+919876543210`
   - `CALLMEBOT_APIKEY` — the key from step 3

Until both secrets exist, `notify.py` exits quietly and sends nothing, so the workflow is safe to run before you get to this.

**What arrives:** one digest per run, not one message per release — PIB can publish dozens an hour. Up to 8 headlines with ministry and link, then "…and N more", then a link to the reader page.

**The first run after you add the secrets sends nothing.** It records everything currently in the feed as a baseline, so you don't get the entire backlog at once. Digests begin with the next new release.

Test it locally without sending anything:

```bash
python notify.py --dry-run
```

## Things that will bite you eventually

**Scheduled workflows get disabled after 60 days of repo inactivity.** GitHub does this to every repo. This one dodges it by design — each run that finds new releases pushes a commit, which counts as activity. But if PIB goes quiet for two months, or the job breaks silently and stops committing, the schedule switches itself off. If the feed goes stale, check the Actions tab first.

**Cron is best-effort.** GitHub delays scheduled jobs under load, sometimes by 20+ minutes. `:17` avoids the congested top of the hour, but don't expect punctuality. Nothing breaks — the next run catches up, since discovery is based on what's new rather than on elapsed time.

**`--limit 80` caps each run at 80 new releases.** PIB's heavy days can exceed that. Runs are hourly, so the backlog drains on its own; raise it if you ever start a feed from cold and want to catch up faster.

**Be polite.** `--delay 1.5` spaces out requests to a government host. Please don't lower it.
