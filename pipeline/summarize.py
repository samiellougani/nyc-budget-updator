"""Summarize fetched items into a neutral digest via the Anthropic API."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import anthropic

from .config import get_model
from .fetch import Item

log = logging.getLogger(__name__)

# Generous: Sonnet 5's adaptive thinking counts against max_tokens, and on a
# 40+ item editorial task thinking alone can consume >10K tokens. Streaming is
# required by the SDK at this size (avoids HTTP timeouts).
MAX_TOKENS = 64000

IMPORTANCE_VALUES = {"HIGH", "MEDIUM", "LOW"}
CLAIM_TYPE_VALUES = {"enacted", "projection", "advocacy"}

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "importance": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "why_it_matters": {"type": "string"},
                    "claim_type": {
                        "type": "string",
                        "enum": ["enacted", "projection", "advocacy"],
                    },
                    "tripwire": {"type": "boolean"},
                },
                "required": [
                    "id",
                    "headline",
                    "summary",
                    "importance",
                    "why_it_matters",
                    "claim_type",
                    "tripwire",
                ],
                "additionalProperties": False,
            },
        },
        "contrast": {"type": ["string", "null"]},
    },
    "required": ["items", "contrast"],
    "additionalProperties": False,
}


@dataclass
class DigestEntry:
    headline: str
    summary: str
    url: str
    source_id: str
    source_name: str
    date: str
    importance: str
    why_it_matters: str
    claim_type: str
    tripwire: bool


def _keyword_pattern(keyword: str) -> re.Pattern:
    # Word boundaries so "1202" doesn't match inside e.g. "$12,020" or zip codes
    return re.compile(rf"(?<![\w]){re.escape(keyword)}(?![\w])", re.IGNORECASE)


def matches_tripwire(text: str, keywords: list[str]) -> bool:
    return any(_keyword_pattern(k).search(text) for k in keywords)


def _build_user_message(items: list[Item]) -> str:
    payload = [
        {
            "id": i,
            "title": item.title,
            "source": item.source_name,
            "source_stance": item.stance,
            "date": item.date.date().isoformat() if item.date else "unknown",
            "excerpt": item.excerpt,
            "url": item.url,
        }
        for i, item in enumerate(items)
    ]
    return (
        "Summarize this week's items per your editorial stance and the reader's "
        "relevance profile. Return one output entry per input item, referencing "
        "each item by its `id`.\n\nITEMS:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
    )


def _extract_text(response) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError(f"No text block in response (stop_reason={response.stop_reason})")


def _parse_json_loosely(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _request(client: anthropic.Anthropic, label: str, **kwargs):
    """Streaming request (required at this max_tokens); never parse truncated
    output — a max_tokens stop means the JSON is cut mid-string."""
    with client.messages.stream(**kwargs) as stream:
        response = stream.get_final_message()
    log.info(
        "Summarizer call ok (%s): in=%d out=%d tokens, stop=%s",
        label,
        response.usage.input_tokens,
        response.usage.output_tokens,
        response.stop_reason,
    )
    if response.stop_reason == "max_tokens":
        raise ValueError(
            f"Model output truncated at max_tokens={kwargs['max_tokens']} — "
            "raise MAX_TOKENS or lower --max-items"
        )
    return response


def _call_model(client: anthropic.Anthropic, system: str, user_message: str) -> dict:
    model = get_model()
    try:
        response = _request(
            client,
            "structured",
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        return _parse_json_loosely(_extract_text(response))
    except anthropic.BadRequestError as exc:
        log.warning(
            "Structured output rejected (%s); falling back to prompt-based JSON", exc
        )
    fallback_message = (
        user_message
        + "\n\nRespond with ONLY a JSON object matching this schema — no prose, "
        "no code fences:\n"
        + json.dumps(SCHEMA)
    )
    response = _request(
        client,
        "fallback",
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": fallback_message}],
    )
    return _parse_json_loosely(_extract_text(response))


def summarize(
    items: list[Item],
    stance_md: str,
    profile_md: str,
    tripwire_keywords: list[str],
) -> tuple[list[DigestEntry], str | None]:
    """One call for all items so the model can build the cross-source
    contrast section. Returns (entries, contrast)."""
    if not items:
        return [], None

    client = anthropic.Anthropic(max_retries=3)
    system = stance_md + "\n\n---\n\n# RELEVANCE PROFILE\n\n" + profile_md
    result = _call_model(client, system, _build_user_message(items))

    entries: list[DigestEntry] = []
    for raw in result.get("items", []):
        entry = _validate_entry(raw, items, tripwire_keywords)
        if entry:
            entries.append(entry)

    returned_ids = {raw.get("id") for raw in result.get("items", [])}
    missing = [i for i in range(len(items)) if i not in returned_ids]
    if missing:
        log.warning("Model omitted %d items (ids %s)", len(missing), missing)

    contrast = result.get("contrast")
    if contrast is not None and not str(contrast).strip():
        contrast = None
    return entries, contrast


def _validate_entry(
    raw: dict, items: list[Item], tripwire_keywords: list[str]
) -> DigestEntry | None:
    item_id = raw.get("id")
    if not isinstance(item_id, int) or not 0 <= item_id < len(items):
        log.warning("Dropping model entry with invalid id: %r", item_id)
        return None
    # url/source/date come from OUR fetched data, never from the model —
    # citations cannot be fabricated.
    item = items[item_id]

    importance = str(raw.get("importance", "")).upper()
    if importance not in IMPORTANCE_VALUES:
        log.warning("Invalid importance %r for id %d; defaulting LOW", importance, item_id)
        importance = "LOW"
    claim_type = str(raw.get("claim_type", "")).lower()
    if claim_type not in CLAIM_TYPE_VALUES:
        log.warning("Invalid claim_type %r for id %d; defaulting projection", claim_type, item_id)
        claim_type = "projection"

    # Belt and suspenders: the tripwire pin must fire even if the model missed
    # it. Keyword matches force tripwire=True and HIGH importance.
    tripwire = bool(raw.get("tripwire", False))
    if matches_tripwire(f"{item.title} {item.excerpt}", tripwire_keywords):
        tripwire = True
    if tripwire:
        importance = "HIGH"

    return DigestEntry(
        headline=str(raw.get("headline", item.title)).strip() or item.title,
        summary=str(raw.get("summary", "")).strip(),
        url=item.url,
        source_id=item.source_id,
        source_name=item.source_name,
        date=item.date.date().isoformat() if item.date else "unknown",
        importance=importance,
        why_it_matters=str(raw.get("why_it_matters", "")).strip(),
        claim_type=claim_type,
        tripwire=tripwire,
    )
