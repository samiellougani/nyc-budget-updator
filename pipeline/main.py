"""Weekly digest pipeline CLI.

    python -m pipeline.main               full run: fetch -> summarize -> digest -> SMS -> state
    python -m pipeline.main --dry-run     print digest to stdout; no SMS, no state, no digest file
    python -m pipeline.main --test-sms    send one test SMS to TEST_PHONE_NUMBER and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from . import config, deliver, fetch, sms, state
from .summarize import matches_tripwire, summarize

log = logging.getLogger("pipeline")

FIRST_RUN_SCRAPE_CAP = 10
DEFAULT_MAX_ITEMS = 60


def write_run_summary(**fields) -> None:
    config.RUN_SUMMARY_PATH.write_text(
        json.dumps(fields, indent=2) + "\n", encoding="utf-8"
    )


def run_test_sms() -> int:
    test_number = os.environ.get("TEST_PHONE_NUMBER", "").strip()
    if not test_number:
        log.error("--test-sms requires TEST_PHONE_NUMBER to be set in .env")
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = f"nyc-budget-updator test message ({stamp}). Twilio wiring OK."
    failures = sms.send_sms(body, [{"name": "test", "phone": test_number}])
    if failures:
        log.error("Test SMS failed: %s", failures)
        return 1
    log.info("Test SMS sent to %s", test_number)
    return 0


def run_pipeline(dry_run: bool, max_items: int) -> int:
    sources = config.load_sources()
    profile_md = config.load_profile()
    stance_md = config.load_editorial_stance()
    tripwires = config.parse_tripwire_keywords(profile_md)
    log.info(
        "Loaded %d sources, %d tripwire keywords, model=%s",
        len(sources),
        len(tripwires),
        config.get_model(),
    )

    session = fetch.make_session()
    items, source_failures = fetch.fetch_all(sources, session)
    log.info(
        "Fetched %d candidate items (%d source failures)",
        len(items),
        len(source_failures),
    )

    run_state = state.load_state()
    first_run = state.is_first_run(run_state)
    new_items = [i for i in items if not state.is_seen(run_state, i.dedup_key)]
    log.info("%d new items after dedup (first_run=%s)", len(new_items), first_run)

    # First run: no seen.json yet, so scrape index pages surface their entire
    # history. Cap each scrape source to its newest links (index order); the
    # remainder is pre-seeded as seen without being summarized.
    to_process = []
    scrape_counts: dict[str, int] = {}
    for item in new_items:
        if first_run and item.needs_detail:
            scrape_counts[item.source_id] = scrape_counts.get(item.source_id, 0) + 1
            if scrape_counts[item.source_id] > FIRST_RUN_SCRAPE_CAP:
                continue
        to_process.append(item)

    fetch.enrich_items([i for i in to_process if i.needs_detail], session)

    cutoff = fetch.window_cutoff()
    to_process = [i for i in to_process if not i.date or i.date >= cutoff]

    # Cap what goes to the LLM: tripwire matches always first, then newest.
    to_process.sort(
        key=lambda i: (
            not matches_tripwire(f"{i.title} {i.excerpt}", tripwires),
            -(i.date or cutoff).timestamp(),
        )
    )
    truncated_count = max(0, len(to_process) - max_items)
    if truncated_count:
        log.warning("Capping items sent to the LLM: %d -> %d", len(to_process), max_items)
        to_process = to_process[:max_items]

    if not to_process:
        log.info("No new items this week — nothing to summarize.")
        if not dry_run:
            for item in new_items:
                state.mark_seen(run_state, item.dedup_key, item.url, item.source_id)
            state.save_state(run_state)
        write_run_summary(
            ok=True,
            mode="dry-run" if dry_run else "send",
            items_fetched=len(items),
            items_new=len(new_items),
            items_summarized=0,
            truncated=0,
            source_failures=[vars(f) for f in source_failures],
            sms_failures=[],
            digest_path=None,
        )
        return 0

    entries, contrast = summarize(to_process, stance_md, profile_md, tripwires)
    log.info("Summarized %d items (contrast=%s)", len(entries), bool(contrast))

    date_str = deliver.digest_date()
    digest_md = deliver.render_digest(
        entries, contrast, source_failures, truncated_count, date_str
    )

    if dry_run:
        print("\n" + "=" * 72)
        print(digest_md)
        print("=" * 72)
        flags, highs, mediums, lows = deliver.split_by_importance(entries)
        body = sms.compose_sms(
            flags, highs, len(mediums), len(lows), deliver.digest_url(date_str)
        )
        print("\nSMS PREVIEW "
              f"({len(body)} chars, {sms.segment_count(body)} segment(s)):\n")
        print(body)
        write_run_summary(
            ok=True,
            mode="dry-run",
            items_fetched=len(items),
            items_new=len(new_items),
            items_summarized=len(entries),
            truncated=truncated_count,
            source_failures=[vars(f) for f in source_failures],
            sms_failures=[],
            digest_path=None,
        )
        return 0

    # Send mode: digest file first (state is only saved after this succeeds).
    path = deliver.write_digest(digest_md, date_str)
    flags, highs, mediums, lows = deliver.split_by_importance(entries)
    body = sms.compose_sms(
        flags, highs, len(mediums), len(lows), deliver.digest_url(date_str)
    )
    recipients = config.load_recipients()
    sms_failures = []
    if recipients:
        sms_failures = sms.send_sms(body, recipients)
        deliver.append_sms_failures(path, sms_failures)
    else:
        log.warning("No recipients configured — skipping SMS send")

    for item in new_items:
        state.mark_seen(run_state, item.dedup_key, item.url, item.source_id)
    state.save_state(run_state)

    write_run_summary(
        ok=True,
        mode="send",
        items_fetched=len(items),
        items_new=len(new_items),
        items_summarized=len(entries),
        truncated=truncated_count,
        source_failures=[vars(f) for f in source_failures],
        sms_failures=sms_failures,
        digest_path=str(path.relative_to(config.REPO_ROOT)),
    )
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="NYC/NYS fiscal policy weekly digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="print digest to stdout; no SMS, no state write")
    parser.add_argument("--test-sms", action="store_true",
                        help="send a single test SMS to TEST_PHONE_NUMBER and exit")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS,
                        help="cap on items sent to the summarizer")
    args = parser.parse_args()

    if args.test_sms:
        return run_test_sms()
    try:
        return run_pipeline(dry_run=args.dry_run, max_items=args.max_items)
    except Exception as exc:  # noqa: BLE001 — record the failure, then re-raise
        log.error("Pipeline failed: %s", exc, exc_info=True)
        write_run_summary(ok=False, mode="dry-run" if args.dry_run else "send",
                          error=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
