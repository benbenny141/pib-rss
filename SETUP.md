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

## Things that will bite you eventually

**Scheduled workflows get disabled after 60 days of repo inactivity.** GitHub does this to every repo. This one dodges it by design — each run that finds new releases pushes a commit, which counts as activity. But if PIB goes quiet for two months, or the job breaks silently and stops committing, the schedule switches itself off. If the feed goes stale, check the Actions tab first.

**Cron is best-effort.** GitHub delays scheduled jobs under load, sometimes by 20+ minutes. `:17` avoids the congested top of the hour, but don't expect punctuality. Nothing breaks — the next run catches up, since discovery is based on what's new rather than on elapsed time.

**`--limit 80` caps each run at 80 new releases.** PIB's heavy days can exceed that. Runs are hourly, so the backlog drains on its own; raise it if you ever start a feed from cold and want to catch up faster.

**Be polite.** `--delay 1.5` spaces out requests to a government host. Please don't lower it.
