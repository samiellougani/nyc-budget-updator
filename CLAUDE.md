# CLAUDE.md

Weekly NYC/NYS fiscal policy digest: fetch sources → summarize with the
Anthropic API → post to Discord via webhook → digest committed to `digests/`.
Runs from a **local host cron** (Ubuntu box with Docker) via
`scripts/run_weekly.sh`, in the same Docker image used for `make` targets.
The script refreshes `main`, runs the pipeline, and — when there are changes —
opens a **pull request** to `main` that the human reviews and merges. Install
the cron roughly Mondays ~7am ET, e.g.
`7 11 * * 1  /path/to/repo/scripts/run_weekly.sh >> /var/log/digest.log 2>&1`.

## Contributing workflow (hard rules)

- **NEVER commit or push directly to `main`.** Every change — code, config,
  docs, process — goes on a feature branch and lands via a pull request.
- **Every PR uses `.github/PULL_REQUEST_TEMPLATE.md`, filled out** (summary,
  why, changes, verification, checklist). No empty template sections.
- **Update this CLAUDE.md in the same PR for ANY change** it doesn't already
  describe — new behavior, structure, conventions, or gotchas.
- The human reviews and merges PRs; don't self-merge.
- The weekly cron is no exception: `scripts/run_weekly.sh` never pushes to
  `main`. It pushes a `digest/YYYY-MM-DD` branch and opens a PR (data commit:
  `digests/*.md`, `state/seen.json`) for the human to merge. `main` is
  branch-protected.

## Commands

Everything runs through Docker — no local Python setup:

```bash
make digest-dry    # full pipeline, prints digest + Discord preview, no posts;
                   # also writes gitignored digest-preview.md for inspection
make digest-send   # real run: digest file + Discord post + state update
make test-discord  # single test message to the Discord webhook
docker compose run --rm digest python - <<'EOF'   # ad-hoc code against the package
from pipeline import fetch, config
EOF
```

There is no test suite; verification is `make digest-dry` plus the inline
assert-style checks used during development (see git history).

The weekly production run is `scripts/run_weekly.sh` (invoked by the host
cron). It reads secrets from a gitignored `.env` at repo root
(`ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`, `NYSENATE_API_KEY`,
`GITHUB_TOKEN`), builds `digest:latest`, runs `python -m pipeline.main`, and
opens the weekly PR. Requires `docker`, `git`, `curl`, and `jq` on the host.

## Layout

| Path | Role |
|---|---|
| `pipeline/main.py` | CLI + orchestration; `--dry-run`, `--test-discord`, `--max-items` |
| `pipeline/fetch.py` | Source fetchers: `rss`, `scrape`, `nysenate`, `ibo`; returns `(items, failures)` |
| `pipeline/state.py` | `state/seen.json` dedup: sha256 of normalized URL; 90-day prune |
| `pipeline/summarize.py` | One Anthropic call for all items; JSON schema + fallback parse |
| `pipeline/notify.py` | Discord webhook delivery: plain-content message (no embeds) — @everyone header + model-written weekly brief + tripwire/HIGH headline links + full-digest link |
| `pipeline/deliver.py` | Digest markdown rendering, NY-timezone dating |
| `sources.yaml` | Source list — user-editable, no code changes needed |
| `profile.md` | Relevance profile — loaded into the system prompt; tripwire keywords parsed from its `## Tripwire keywords` bullet list |
| `prompts/editorial_stance.md` | Nonpartisan editorial rules — the core product constraint |
| `scripts/run_weekly.sh` | Local-cron entrypoint: refresh main → build → run → open weekly PR; Discord notice on failure |

## Conventions & invariants

- **One failing source must never kill the run.** Every fetcher is wrapped
  per-source; failures go into the digest footer, `run_summary.json`, and the
  "Run notes (soft failures)" section of the weekly PR body.
- **State invariant:** `seen.json` is saved only after the digest file is
  written. Summarizer failure → non-zero exit, no state write, items retry
  next week. Dry-run never touches state.
- **Anti-hallucination join:** the model only returns item `id`s; url/source/
  date are joined back from fetched data in `summarize._validate_entry`.
  Never let model output supply citation URLs.
- **Tripwire enforcement is dual:** the model flags, AND
  `summarize.matches_tripwire` (word-boundary regex) force-flags + upgrades to
  HIGH. Keywords live in `profile.md`, parsed by
  `config.parse_tripwire_keywords`.
- **NY Senate dedup keys include status + actionDate** so tracked bills
  re-alert on every status change/amendment. Article keys are normalized URLs
  (tracking params stripped).
- **Discord gets the weekly brief as a plain-content message — NOT an embed.**
  The user explicitly rejected the embed's bordered-card look. The model
  writes `weekly_brief` (120–180 words, plain English for a reader with no
  fiscal background — jargon explained inline; tripwire/HIGH first, MEDIUM
  themes, LOW omitted; 5–8 links max) hyperlinking phrases via item-id refs
  (`[phrase](#12)`); `summarize._resolve_brief_links` substitutes real URLs
  and strips any direct-URL link the model tries to emit — the
  anti-hallucination invariant applies to the brief too. After the brief:
  tripwire/HIGH headline links ("Flagged this week", max 10) and a
  full-digest link.
- **Message mechanics.** `content` starts with `@everyone` AND the payload
  sets `allowed_mentions: {"parse": ["everyone"]}` — without allowed_mentions
  the mention renders but does not notify. `flags: 4` (SUPPRESS_EMBEDS) stops
  every hyperlink from spawning a preview card. Content hard limit is 2000
  chars **counting raw link URLs**; `_split_content` breaks at
  paragraph/sentence boundaries into follow-up messages (no ping) when
  needed — normally it's a single message.
- **Quiet weeks still post.** A send-mode run with zero new items posts a
  non-pinging "no new items" heartbeat (`notify.send_no_news`) — otherwise a
  quiet week is indistinguishable from a broken cron. Dry runs never post.
- **Failure signaling is via Discord + the PR, not GitHub issues.** A hard
  failure (pipeline non-zero, or PR creation fails after the branch is pushed)
  makes `scripts/run_weekly.sh` post a short plain-content, non-pinging notice
  to the Discord webhook and exit non-zero — so a broken cron is visible in
  the channel. Soft failures (dead source / delivery error) surface in the
  weekly PR body's "Run notes" section. The `run_weekly.sh` run is idempotent
  per day: if `digest/YYYY-MM-DD` already exists on the remote it logs and
  exits 0, so re-running the cron never duplicates PRs. Timing is up to the
  host `cron`; keep the minute off-peak (`7 11 * * 1`).
- **Editorial neutrality is a hard requirement** — any prompt edits must keep
  the FOR/AGAINST steelmanning, claim-type labels, no-loaded-language rule,
  and retroactivity flagging in `prompts/editorial_stance.md`.

## Gotchas

- Several hosts (cbcny.org, nysfocus.com) are Cloudflare-protected: all HTTP
  goes through `fetch.make_session()` which sets a browser User-Agent. Don't
  use bare `requests.get`.
- IBO's site is JS-rendered; `fetch_ibo` hits the NYC.gov content API JSON
  (`apps.nyc.gov/content-api/...`) instead. The a860-gpp.nyc.gov portal
  hard-403s bots — don't switch back to it.
- THE CITY rebranded: feed is `thecityreporter.nyc/feed/` (thecity.nyc 301s).
- Discord delivery posts to `DISCORD_WEBHOOK_URL` with `?wait=true`, which
  makes Discord confirm message creation synchronously — a non-2xx response
  IS the delivery failure signal (no async status polling needed).
- The model is `claude-sonnet-5` by default (`ANTHROPIC_MODEL` overrides).
  Structured output via `output_config={"format": {"type": "json_schema"...}}`
  with a prompt-based JSON fallback on `BadRequestError` — keep both paths.
- Local `docker compose run` mounts the repo at `/app`, shadowing the image
  copy — code edits don't need a rebuild; dependency changes do.
