"""Digest markdown rendering and delivery orchestration."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DIGESTS_DIR, REPO_URL
from .fetch import SourceFailure
from .summarize import DigestEntry

log = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")


def digest_date() -> str:
    """The Monday 11:00 UTC run is 6-7am ET, so date the digest in NY time."""
    return datetime.now(NY_TZ).date().isoformat()


def digest_path(date_str: str) -> Path:
    return DIGESTS_DIR / f"{date_str}.md"


def digest_url(date_str: str) -> str:
    return f"{REPO_URL}/blob/main/digests/{date_str}.md"


def split_by_importance(
    entries: list[DigestEntry],
) -> tuple[list[DigestEntry], list[DigestEntry], list[DigestEntry], list[DigestEntry]]:
    flags = [e for e in entries if e.tripwire]
    highs = [e for e in entries if e.importance == "HIGH" and not e.tripwire]
    mediums = [e for e in entries if e.importance == "MEDIUM" and not e.tripwire]
    lows = [e for e in entries if e.importance == "LOW" and not e.tripwire]
    return flags, highs, mediums, lows


def _render_entry(entry: DigestEntry) -> str:
    lines = [f"### {entry.headline}", ""]
    if entry.summary:
        lines += [entry.summary, ""]
    if entry.why_it_matters:
        lines += [f"**Why it matters:** {entry.why_it_matters}", ""]
    lines.append(
        f"[{entry.source_name}]({entry.url}) — {entry.date} — claim type: {entry.claim_type}"
    )
    return "\n".join(lines)


def render_digest(
    entries: list[DigestEntry],
    contrast: str | None,
    brief: str,
    source_failures: list[SourceFailure],
    truncated_count: int,
    date_str: str,
) -> str:
    flags, highs, mediums, lows = split_by_importance(entries)
    parts = [
        f"# NYC/NYS Fiscal Policy Digest — {date_str}",
        "",
        f"_Generated {datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M %Z')} · "
        f"{len(entries)} item(s)_",
        "",
    ]
    if brief:
        parts += ["## The week in brief", "", brief.strip(), ""]
    if contrast:
        parts += ["> **Where sources disagree:** " + contrast.strip(), ""]
    sections = [
        ("🚨 Tripwire alerts", flags),
        ("High importance", highs),
        ("Medium importance", mediums),
        ("Low importance", lows),
    ]
    for title, section_entries in sections:
        if not section_entries:
            continue
        parts.append(f"## {title}")
        parts.append("")
        for entry in section_entries:
            parts.append(_render_entry(entry))
            parts.append("")
    footer = []
    if truncated_count:
        footer.append(
            f"- {truncated_count} additional new item(s) exceeded this week's cap "
            "and were not summarized (they will not reappear)."
        )
    for failure in source_failures:
        footer.append(f"- Source `{failure.source_id}` failed: {failure.error}")
    if footer:
        parts += ["---", "", "## Run notes", ""] + footer + [""]
    return "\n".join(parts)


def write_digest(content: str, date_str: str) -> Path:
    path = digest_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.info("Wrote digest -> %s", path)
    return path


def append_delivery_failures(path: Path, delivery_failures: list[dict]) -> None:
    if not delivery_failures:
        return
    lines = ["", "## Delivery failures", ""]
    lines += [f"- {f['name']}: {f['error']}" for f in delivery_failures]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
