# PIB RSS Feed Generator

Builds a proper RSS 2.0 feed from [pib.gov.in](https://www.pib.gov.in) — all press releases, English, National region, with full article text.

## Why not just use PIB's own feed?

PIB publishes RSS at `RssMain.aspx?ModId=6&Lang=1&Regid=3`, but it's minimal:

| | Official PIB feed | This script |
|---|---|---|
| pubDate | ❌ none | ✅ parsed from release page (IST) |
| description | ❌ none | ✅ 600-char summary |
| Full text | ❌ none | ✅ `content:encoded` |
| Ministry | ❌ none | ✅ `<category>` + `<dc:creator>` |
| Item count | ~20, current only | Rolling archive (default 200) |
| Language | `Lang=1` gets overridden by session cookies → Hindi | Cookies + params pinned to English |

Note that PIB assigns **separate PRIDs to English and Hindi** versions of the same release, so pinning the language actually matters — you're not just changing a display setting.

## Reading it

- **Web reader:** <https://benbenny141.github.io/pib-rss/> — search across full text, filter by ministry, expand releases inline, share to WhatsApp.
- **RSS reader:** subscribe to <https://benbenny141.github.io/pib-rss/feed.xml>.
- **Telegram digest:** optional, see [SETUP.md](SETUP.md#6-telegram-digest-optional).

## Install

```bash
pip install requests beautifulsoup4
```

## Run

```bash
python pib_rss.py                          # → ./feed.xml + ./pib_state.json
python pib_rss.py --out /srv/www/pib.xml --self-url https://you.example/pib.xml
python pib_rss.py --no-body                # metadata only, single page fetch
```

| Flag | Default | Purpose |
|---|---|---|
| `--out` | `feed.xml` | Output RSS path |
| `--state` | `pib_state.json` | Rolling archive; keeps the feed from resetting daily |
| `--max-items` | `200` | Items retained in feed and archive |
| `--limit` | `60` | Max *new* releases fetched per run |
| `--delay` | `1.0` | Seconds between release fetches — be polite to a .gov.in host |
| `--no-body` | off | Skip per-release fetches |
| `--self-url` | – | Public feed URL, emitted as `atom:link rel="self"` |

The state file is what makes this work as a feed rather than a snapshot: already-seen PRIDs are never re-fetched, so hourly runs are cheap (typically 1 listing fetch + a handful of release fetches).

## Schedule it

**Hosted, hourly, with a public feed URL:** see [SETUP.md](SETUP.md) — the GitHub Actions workflow in `.github/workflows/feed.yml` is ready to go.

**Locally**, cron hourly at :17:

```cron
17 * * * * /usr/bin/python3 /path/to/pib_rss.py --out /srv/www/pib.xml --state /var/lib/pib_state.json >> /var/log/pib_rss.log 2>&1
```

launchd (macOS), systemd timer, or a GitHub Action on `schedule:` all work the same way — just make sure `--state` points somewhere persistent.

## How it works

1. Warms a `requests.Session` against `index.aspx?lang=1&reg=3`, setting `lang`/`reg` cookies. PIB stores language/region server-side per session, which is why passing `Lang=1` alone silently fails.
2. Scrapes `allRel.aspx`, walking the DOM in order so each release inherits the ministry heading above it.
3. **Also** reads `RssMain.aspx` every run and unions the two. Neither source is sufficient alone — see below.
4. Fetches each new `PressReleasePage.aspx?PRID=…`, pulling title (`og:title` first), ministry, timestamp, PIB bureau, and body — stripping scripts and share widgets, absolutising relative URLs.
5. Merges into the archive and writes RSS 2.0.

## Why two sources

`allRel.aspx` is aggressively CDN-cached and shows only one selected date. Observed failures:

- Served a page dated **11 Jul** when fetched on **20 Jul** — nine days stale.
- Listed **1** release for a day when `RssMain.aspx` listed **20** for that same day.

`RssMain.aspx` is fresher but caps at ~20 items and carries no ministry attribution.

So each run queries both and merges on PRID. Listing entries win on ministry; RSS fills whatever the cached listing dropped. The log reports the split:

```
[info] listing page: 5 | rss: 20 (+17 the listing missed) | union: 22
```

If RSS is consistently contributing more than the listing, that's `allRel.aspx` being stale, not a parser bug — the script emits a warning saying so.

## Robustness

PIB re-skins its site periodically. Every extraction step tries a list of candidate selectors and then falls back to regex or `og:` meta tags, so a class rename degrades quality rather than breaking the run. HTTP failures retry with exponential backoff; a corrupt state file is discarded rather than crashing.

**Caveat:** the CSS selectors were derived from PIB's rendered output, not verified against live raw HTML (the build environment had no route to pib.gov.in). The regex and `og:` meta fallbacks should carry it regardless, but check the first run's stderr — it logs the title of every release it ingests. If titles look right and `feed.xml` has `<pubDate>` and `<content:encoded>` on its items, the selectors matched.

## Tests

```bash
python test_pib_rss.py
```

31 offline checks over fixtures modelled on real PIB markup: discovery and dedup, ministry attribution, AM/PM and midnight date parsing, body cleaning, XML well-formedness, escaping of `&`/`<` in titles, and state persistence/trimming/recovery.
