"""Discord webhook delivery for the weekly digest.

Plain-content messages (no embeds — the user wants a normal-looking message,
not a bordered card): an @everyone header line with counts, the model-written
weekly brief with inline hyperlinks, tripwire/HIGH headline links when any
exist, and a full-digest link. Usually one message; Discord caps content at
2000 chars, so overlong briefs split at sentence boundaries (only the first
message pings).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
CONTENT_LIMIT = 1990  # Discord hard limit is 2000
MAX_BULLETS = 6
INTER_MESSAGE_DELAY = 0.5
SUPPRESS_EMBEDS = 1 << 2  # message flag 4: no link-preview cards

# @everyone only pings if the payload explicitly allows the mention.
ALLOWED_MENTIONS = {"parse": ["everyone"]}
NO_MENTIONS = {"parse": []}


def _post(payload: dict) -> list[dict]:
    """POST one message to the webhook. Returns a failures list (empty on
    success). ?wait=true makes Discord confirm creation synchronously."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        log.warning("DISCORD_WEBHOOK_URL is not set — skipping delivery")
        return [{"name": "discord", "error": "DISCORD_WEBHOOK_URL is not set"}]
    try:
        resp = requests.post(
            url, json=payload, params={"wait": "true"}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        log.info("Discord message delivered (HTTP %d)", resp.status_code)
        return []
    except requests.RequestException as exc:
        detail = ""
        if getattr(exc, "response", None) is not None:
            detail = f" — {exc.response.text[:200]}"
        log.warning("Discord delivery FAILED: %s%s", exc, detail)
        return [{"name": "discord", "error": f"{type(exc).__name__}: {exc}{detail}"}]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_content(text: str, limit: int = CONTENT_LIMIT) -> list[str]:
    """Split at paragraph, then sentence, then word boundaries. Raw length
    includes link URLs, so a link-dense brief can exceed the cap even when
    the visible text looks short."""
    chunks = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            sentence = window.rfind(". ")
            if sentence >= limit // 2:
                cut = sentence + 1  # keep the period
            else:
                space = window.rfind(" ")
                cut = space if space >= limit // 2 else limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def compose_digest_messages(
    flags: list,
    highs: list,
    mediums: list,
    lows: list,
    brief: str,
    digest_url: str,
    date_str: str,
) -> list[dict]:
    counts = []
    if flags:
        counts.append(f"{len(flags)} tripwire")
    if highs:
        counts.append(f"{len(highs)} high")
    counts.append(f"{len(mediums)} medium, {len(lows)} low")

    parts = [f"@everyone **NYC/NYS Fiscal Digest — {date_str}** ({', '.join(counts)})"]
    if brief:
        parts.append(brief.strip())
    flagged = list(flags) + list(highs)
    if flagged:
        bullets = [
            f"• [{_truncate(e.headline, 150)}]({e.url})" for e in flagged[:MAX_BULLETS]
        ]
        if len(flagged) > MAX_BULLETS:
            bullets.append(f"…and {len(flagged) - MAX_BULLETS} more in the digest.")
        parts.append("**Flagged this week:**\n" + "\n".join(bullets))
    total = len(flags) + len(highs) + len(mediums) + len(lows)
    parts.append(f"[Full digest (all {total} items)]({digest_url})")

    chunks = _split_content("\n\n".join(parts))
    if len(chunks) > 1:
        log.warning("Digest message split into %d parts (content cap)", len(chunks))
    messages = [
        {"content": chunk, "flags": SUPPRESS_EMBEDS, "allowed_mentions": NO_MENTIONS}
        for chunk in chunks
    ]
    messages[0]["allowed_mentions"] = ALLOWED_MENTIONS
    return messages


def send_digest(
    flags: list,
    highs: list,
    mediums: list,
    lows: list,
    brief: str,
    digest_url: str,
    date_str: str,
) -> list[dict]:
    messages = compose_digest_messages(
        flags, highs, mediums, lows, brief, digest_url, date_str
    )
    for i, payload in enumerate(messages):
        failures = _post(payload)
        if failures:
            failures[0]["error"] += f" (message {i + 1}/{len(messages)})"
            return failures
        if i + 1 < len(messages):
            time.sleep(INTER_MESSAGE_DELAY)
    return []


def send_test() -> list[dict]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _post(
        {
            "content": (
                f"@everyone nyc-budget-updator test message ({stamp}). "
                "Discord wiring OK."
            ),
            "allowed_mentions": ALLOWED_MENTIONS,
        }
    )
