# CLAUDE.md

Weekly NYC/NYS fiscal policy digest: fetch sources → summarize with the
Anthropic API → SMS via Twilio → digest committed to `digests/`. Runs in
GitHub Actions (cron Mondays 11:00 UTC) inside the same Docker image used
locally.

## Commands

Everything runs through Docker — no local Python setup:

```bash
make digest-dry    # full pipeline, prints digest + SMS preview, no sends/writes
make digest-send   # real run (respects TEST_PHONE_NUMBER override in .env)
make test-sms      # single test SMS to TEST_PHONE_NUMBER
docker compose run --rm digest python - <<'EOF'   # ad-hoc code against the package
from pipeline import fetch, config
EOF
```

There is no test suite; verification is `make digest-dry` plus the inline
assert-style checks used during development (see git history).

## Layout

| Path | Role |
|---|---|
| `pipeline/main.py` | CLI + orchestration; `--dry-run`, `--test-sms`, `--max-items` |
| `pipeline/fetch.py` | Source fetchers: `rss`, `scrape`, `nysenate`, `ibo`; returns `(items, failures)` |
| `pipeline/state.py` | `state/seen.json` dedup: sha256 of normalized URL; 90-day prune |
| `pipeline/summarize.py` | One Anthropic call for all items; JSON schema + fallback parse |
| `pipeline/sms.py` | GSM-7 transliteration, septet segmentation, ≤3-segment composer, Twilio |
| `pipeline/deliver.py` | Digest markdown rendering, NY-timezone dating |
| `sources.yaml` | Source list — user-editable, no code changes needed |
| `profile.md` | Relevance profile — loaded into the system prompt; tripwire keywords parsed from its `## Tripwire keywords` bullet list |
| `prompts/editorial_stance.md` | Nonpartisan editorial rules — the core product constraint |

## Conventions & invariants

- **One failing source must never kill the run.** Every fetcher is wrapped
  per-source; failures go into the digest footer, Actions warnings, and a
  `source-failure` GitHub issue.
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
- **SMS must stay GSM-7.** `sms.to_gsm7` strips accents before composing —
  "pied-à-terre" would otherwise flip the message to UCS-2 (67 chars/segment
  vs 153). Budget is 3×153 septets; the composer degrades greedily.
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
- `recipients.json` is gitignored (public repo, phone numbers = PII); CI
  materializes it from the `RECIPIENTS_JSON` secret. Never commit a real one.
- The model is `claude-sonnet-5` by default (`ANTHROPIC_MODEL` overrides).
  Structured output via `output_config={"format": {"type": "json_schema"...}}`
  with a prompt-based JSON fallback on `BadRequestError` — keep both paths.
- Local `docker compose run` mounts the repo at `/app`, shadowing the image
  copy — code edits don't need a rebuild; dependency changes do.
