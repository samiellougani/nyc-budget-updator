# nyc-budget-updator

A weekly pipeline that monitors NYC/NYS fiscal policy sources (think tanks
across the spectrum, government offices, local press, and the NY Senate bill
tracker), summarizes the week's developments into a rigorously neutral digest
with the Anthropic API, and delivers it by SMS via Twilio.

- **Full digest**: committed to [`digests/`](digests/) every Monday
- **SMS**: tripwire alerts (QSBS/S8921 etc.) first, then high-importance
  headlines, then a link to the full digest — kept under 3 SMS segments
- **Schedule**: GitHub Actions, Mondays 11:00 UTC (6–7am ET), plus manual runs

## How it works

```
sources.yaml ──> fetch (RSS / scrape / NY Senate API / NYC content API)
                   │  dedup against state/seen.json, keep past 7 days
                   ▼
profile.md ─────> summarize (Anthropic API, structured JSON output)
prompts/editorial_stance.md      │
                   ▼
                 digests/YYYY-MM-DD.md  +  SMS to recipients (Twilio)
                   │
                 state/seen.json committed back to the repo
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
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | [Twilio Console](https://console.twilio.com) dashboard |
| `TWILIO_FROM_NUMBER` | A Twilio phone number with SMS capability (buy one in the console, ~$1/mo) |
| `NYSENATE_API_KEY` | Free — request a key at [legislation.nysenate.gov](https://legislation.nysenate.gov/) (developer API key signup on the homepage) |

> **Twilio trial accounts:** until you upgrade to a paid account, Twilio only
> delivers SMS to **verified** numbers (Console → Phone Numbers → Verified
> Caller IDs) and prefixes messages with a trial notice. Add each recipient as
> a verified number, or upgrade the account.

### 2. GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions. Add all four keys above,
plus:

- `RECIPIENTS_JSON` — the full recipient list as JSON, e.g.
  `[{"name": "Sami", "phone": "+15551234567"}]`. The workflow writes this to
  `recipients.json` at runtime. (The file itself is gitignored because this
  repo is public — phone numbers never land in git.)

### 3. Local setup

No local Python needed — everything runs through Docker:

```bash
cp .env.example .env        # fill in your keys
cp recipients.json.example recipients.json   # optional; only used by digest-send

make digest-dry             # full pipeline, prints digest + SMS preview, sends nothing
make test-sms               # one test SMS to TEST_PHONE_NUMBER (verifies Twilio wiring)
make digest-send            # real run: writes digest, sends SMS, updates state
```

Set `TEST_PHONE_NUMBER` in `.env` for local testing — when set, **all** SMS
goes only to that number and `recipients.json` is ignored, so you can't
accidentally spam the real list.

## Editing what gets tracked

- **`profile.md`** — importance rules (HIGH/MEDIUM/LOW) and the tripwire
  keyword list (the `## Tripwire keywords` bullet list is parsed by the
  pipeline; items matching those keywords are pinned to the top of the digest
  and the SMS regardless of what the model decides).
- **`sources.yaml`** — add/remove/adjust sources. Types: `rss` (feed URL),
  `scrape` (index page + `link_pattern` regex), `nysenate` (tracked bills +
  full-text search terms), `ibo` (NYC.gov content API). Each source carries a
  `stance` label passed to the summarizer as context for advocacy labeling —
  it is never printed as editorial judgment.
- **Recipients** — update the `RECIPIENTS_JSON` secret (CI) or your local
  `recipients.json`. Remember Twilio trial-account verification (above).
- **Bill tracking** — S8921 is tracked explicitly; any new bill mentioning
  "qualified small business stock" or "1202" is caught by the search terms in
  `sources.yaml`. Tracked bills re-alert on every status change or amendment.

## When something breaks (runbook)

You don't need to watch this repo — failures come to you:

- **Red run / hard failure** → a GitHub issue labeled `pipeline-failure` is
  opened (or commented on, if one is already open) with the error and a link
  to the run.
- **Dead source or failed SMS** (run still succeeds) → an issue labeled
  `source-failure` lists exactly what failed; the same failures appear as
  warnings on the Actions run page and in the digest's "Run notes" footer.
- Close the issue once fixed — future failures re-open the conversation by
  commenting on any still-open issue rather than spamming new ones.

Debug locally with `make digest-dry` — it prints per-source fetch counts and
the failure list without sending anything.

## Notes

- The summarizer model defaults to `claude-sonnet-5`; override with
  `ANTHROPIC_MODEL` in `.env` or the workflow env.
- `state/seen.json` is the dedup memory, committed back by CI. Entries older
  than 90 days are pruned automatically. Tracked bills are keyed by
  status+date so status changes always re-alert.
- One dead feed never kills the run — it's recorded and reported, and the
  digest ships without it.
