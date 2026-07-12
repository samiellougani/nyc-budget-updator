"""SMS composition (GSM-7 aware) and Twilio delivery."""

from __future__ import annotations

import logging
import math
import os
import unicodedata

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

log = logging.getLogger(__name__)

# 3 concatenated GSM-7 segments = 3 x 153 septets
MAX_SEGMENTS = 3
SEGMENT_BUDGET = MAX_SEGMENTS * 153

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = set("^{}\\[~]|€")  # each costs 2 septets

_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "•": "-", "×": "x", "÷": "/",
}


def to_gsm7(text: str) -> str:
    """Deterministically transliterate to the GSM 03.38 charset. One stray
    'à' in 'pied-à-terre' would otherwise flip the whole message to UCS-2
    (67 chars/segment instead of 153)."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Strip accents: NFKD then drop combining marks
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if c in GSM7_BASIC or c in GSM7_EXTENSION)


def septets(text: str) -> int:
    return sum(2 if c in GSM7_EXTENSION else 1 for c in text)


def segment_count(text: str) -> int:
    """Segments for a GSM-7 body (UCS-2 safety net included)."""
    if all(c in GSM7_BASIC or c in GSM7_EXTENSION for c in text):
        n = septets(text)
        return 1 if n <= 160 else math.ceil(n / 153)
    n = len(text)
    return 1 if n <= 70 else math.ceil(n / 67)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 2].rstrip() + ".."


def compose_sms(
    flag_entries: list,
    high_entries: list,
    medium_count: int,
    low_count: int,
    digest_url: str,
) -> str:
    """Tripwire FLAG lines, then HIGH lines, then counts, then the link.
    Greedy reduction keeps the body within 3 GSM-7 segments; the link and the
    first FLAG/HIGH line are never dropped."""
    flags = [f"FLAG: {_shorten(to_gsm7(e.headline), 70)}" for e in flag_entries]
    highs = [
        f"- {_shorten(to_gsm7(e.headline), 60)}: {_shorten(to_gsm7(e.why_it_matters), 60)}"
        for e in high_entries
    ]
    dropped = 0

    def build(flag_lines: list[str], high_lines: list[str]) -> str:
        lines = list(flag_lines) + list(high_lines)
        extra = medium_count + low_count + dropped
        if extra:
            lines.append(f"+{medium_count + dropped} medium, {low_count} low")
        lines.append(digest_url)
        return "\n".join(lines)

    body = build(flags, highs)
    # Step 1: shorten HIGH lines to headline-only, last first
    i = len(highs) - 1
    while septets(body) > SEGMENT_BUDGET and i >= 0:
        highs[i] = f"- {_shorten(to_gsm7(high_entries[i].headline), 60)}"
        body = build(flags, highs)
        i -= 1
    # Step 2: drop HIGH lines entirely (keep at least one if there are no flags)
    keep_min = 0 if flags else 1
    while septets(body) > SEGMENT_BUDGET and len(highs) > keep_min:
        highs.pop()
        dropped += 1
        body = build(flags, highs)
    # Step 3: shorten FLAG headlines
    if septets(body) > SEGMENT_BUDGET:
        flags = [f"FLAG: {_shorten(to_gsm7(e.headline), 50)}" for e in flag_entries]
        body = build(flags, highs)
    # Last resort: drop trailing FLAG lines, never the first
    while septets(body) > SEGMENT_BUDGET and len(flags) > 1:
        flags.pop()
        body = build(flags, highs)
    return body


def send_sms(body: str, recipients: list[dict]) -> list[dict]:
    """Send to every recipient; one bad number never blocks the rest.
    Returns a list of {name, error} failures."""
    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    )
    from_number = os.environ["TWILIO_FROM_NUMBER"]
    log.info(
        "Sending SMS (%d chars, %d segment(s)) to %d recipient(s)",
        len(body),
        segment_count(body),
        len(recipients),
    )
    failures = []
    for recipient in recipients:
        try:
            message = client.messages.create(
                to=recipient["phone"], from_=from_number, body=body
            )
            log.info("SMS to %s: sid=%s", recipient["name"], message.sid)
        except TwilioRestException as exc:
            log.warning("SMS to %s FAILED: %s", recipient["name"], exc.msg)
            failures.append({"name": recipient["name"], "error": str(exc.msg)})
    return failures
