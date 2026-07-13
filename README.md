# nyc-budget-updator

A weekly pipeline that monitors NYC/NYS fiscal policy sources (think tanks
across the spectrum, government offices, local press, and the NY Senate bill
tracker), summarizes the week's developments into a rigorously neutral digest
with the Anthropic API, and posts it to a personal Discord server.

- **Discord**: one @everyone-pinging message — a 30-second narrative brief of
  the week with hyperlinked citations, headline links for any tripwire
  (QSBS/S8921 etc.) or high-importance items, and a link to the full digest
- **Full digest**: every item (including low-importance) archived to
  [`digests/`](digests/) with summaries, claim-type labels, and citations
- **Schedule**: a local host cron runs `scripts/run_weekly.sh` Mondays ~7am ET;
  on changes it opens a pull request to `main` for review, plus manual runs

## How it works

```
sources.yaml ──> fetch (RSS / scrape / NY Senate API / NYC content API)
                   │  dedup against state/seen.json, keep past 7 days
                   ▼
profile.md ─────> summarize (Anthropic API, structured JSON output)
prompts/editorial_stance.md      │
                   ▼
                 digests/YYYY-MM-DD.md  +  Discord webhook post
                   │
                 state/seen.json  ──> opened as a PR to main (human merges)
```

The editorial stance (`prompts/editorial_stance.md`) enforces nonpartisan
framing, steelmanned FOR/AGAINST cases, claim-type labels
(enacted/projection/advocacy), and explicit retroactivity flags. The relevance
profile (`profile.md`) defines *what matters* — edit it freely to retune
importance ratings and tripwire keywords without touching code.

## Setup

### 1. Secrets / API keys

| Key | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [platform.claude.com](https://platform.claude.com) → API keys |
| `DISCORD_WEBHOOK_URL` | Your Discord server → channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy URL |
| `NYSENATE_API_KEY` | Free — request a key at [legislation.nysenate.gov](https://legislation.nysenate.gov/) (developer API key signup on the homepage) |

### 2. Local `.env`

```bash
cp .env.example .env        # fill in your keys
```

The weekly cron (`scripts/run_weekly.sh`) reads secrets from this gitignored
`.env`. Alongside the three keys above it needs `GITHUB_TOKEN` (a token with
`repo`/`pull_request` scope) so it can open the weekly PR against the
branch-protected `main`.

### 3. Local setup

No local Python needed — everything runs through Docker:

```bash
make digest-dry             # full pipeline, prints digest + Discord preview,
                            # posts nothing (saved to gitignored digest-preview.md)
make test-discord           # one test message to the webhook (verifies wiring)
make digest-send            # real run: writes digest, posts to Discord, updates state
```

### 4. Weekly cron

Install `scripts/run_weekly.sh` on an always-on host with `docker`, `git`,
`curl`, and `jq`. It refreshes `main`, runs the pipeline in the Docker image,
and — when `digests/` or `state/seen.json` changed — pushes a
`digest/YYYY-MM-DD` branch and opens a PR to `main` for you to merge:

```cron
7 11 * * 1  /path/to/nyc-budget-updator/scripts/run_weekly.sh >> /var/log/digest.log 2>&1
```

It is idempotent per day (re-runs won't duplicate a PR) and posts a Discord
notice if the run or PR creation fails.

## Editing what gets tracked

- **`profile.md`** — importance rules (HIGH/MEDIUM/LOW) and the tripwire
  keyword list (the `## Tripwire keywords` bullet list is parsed by the
  pipeline; items matching those keywords are pinned to the top of the digest
  and the Discord post regardless of what the model decides).
- **`sources.yaml`** — add/remove/adjust sources. Types: `rss` (feed URL),
  `scrape` (index page + `link_pattern` regex), `nysenate` (tracked bills +
  full-text search terms), `ibo` (NYC.gov content API). Each source carries a
  `stance` label passed to the summarizer as context for advocacy labeling —
  it is never printed as editorial judgment.
- **Audience** — everyone in the Discord channel gets the digest (posts ping
  `@everyone`). Add people by inviting them to the server; point the webhook
  at a different channel to move it.
- **Bill tracking** — S8921 is tracked explicitly; any new bill mentioning
  "qualified small business stock" or "1202" is caught by the search terms in
  `sources.yaml`. Tracked bills re-alert on every status change or amendment.

## When something breaks (runbook)

You don't need to watch this repo — failures come to you:

- **Hard failure** (pipeline exits non-zero, or the PR can't be created) →
  `scripts/run_weekly.sh` posts a short, non-pinging notice to the Discord
  webhook and exits non-zero. Check the host cron log for the full trace.
- **Dead source or failed Discord delivery** (run still succeeds) → the
  weekly PR body's "Run notes (soft failures)" section lists exactly what
  failed; the same failures appear in the digest's "Run notes" footer.
- **No PR on a Monday** → check the host cron log; a quiet week (no new items)
  legitimately opens no PR but still posts a "no new items" Discord heartbeat.

Debug locally with `make digest-dry` — it prints per-source fetch counts and
the failure list without sending anything.

## Notes

- The summarizer model defaults to `claude-sonnet-5`; override with
  `ANTHROPIC_MODEL` in `.env`.
- `state/seen.json` is the dedup memory, committed back via the weekly PR.
  Entries older than 90 days are pruned automatically. Tracked bills are keyed
  by status+date so status changes always re-alert.
- One dead feed never kills the run — it's recorded and reported, and the
  digest ships without it.
