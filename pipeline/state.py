"""seen.json state: dedup keys, load/save, pruning."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .config import STATE_PATH

log = logging.getLogger(__name__)

PRUNE_AFTER_DAYS = 90
TRACKING_PARAMS_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    """Canonicalize a URL so tracking-param variants dedup to one key."""
    parsed = urlparse(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in TRACKING_PARAMS
        and not any(k.startswith(p) for p in TRACKING_PARAMS_PREFIXES)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            urlencode(query),
            "",  # drop fragment
        )
    )


def hash_key(dedup_key: str) -> str:
    return hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "items": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if "items" not in state:
        raise ValueError(f"Malformed state file at {path}: missing 'items'")
    return state


def is_first_run(state: dict) -> bool:
    return not state["items"]


def is_seen(state: dict, dedup_key: str) -> bool:
    return hash_key(dedup_key) in state["items"]


def mark_seen(state: dict, dedup_key: str, url: str, source_id: str) -> None:
    state["items"][hash_key(dedup_key)] = {
        "url": url,
        "source": source_id,
        "first_seen": datetime.now(timezone.utc).date().isoformat(),
    }


def prune(state: dict, days: int = PRUNE_AFTER_DAYS) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    before = len(state["items"])
    state["items"] = {
        k: v for k, v in state["items"].items() if v.get("first_seen", cutoff) >= cutoff
    }
    dropped = before - len(state["items"])
    if dropped:
        log.info("Pruned %d state entries older than %d days", dropped, days)


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    prune(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    log.info("Saved state: %d seen items -> %s", len(state["items"]), path)
