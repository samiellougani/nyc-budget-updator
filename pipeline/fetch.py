"""Fetch items from RSS feeds, scraped index pages, and the NY Senate API."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .config import USER_AGENT, Source
from .state import normalize_url

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
WINDOW_DAYS = 7
EXCERPT_MAX = 500


@dataclass
class Item:
    source_id: str
    source_name: str
    stance: str
    title: str
    url: str
    date: datetime | None
    excerpt: str
    dedup_key: str
    needs_detail: bool = False


@dataclass
class SourceFailure:
    source_id: str
    error: str


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def window_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(value: str) -> datetime | None:
    try:
        return _to_utc(dateparser.parse(value))
    except (ValueError, OverflowError, TypeError):
        return None


def _clean_html(html: str) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    return text[:EXCERPT_MAX]


def fetch_all(
    sources: list[Source], session: requests.Session
) -> tuple[list[Item], list[SourceFailure]]:
    """Fetch every source; one failing source never aborts the run."""
    items: list[Item] = []
    failures: list[SourceFailure] = []
    for source in sources:
        try:
            if source.type == "rss":
                fetched = fetch_rss(source, session)
            elif source.type == "scrape":
                fetched = fetch_scrape(source, session)
            elif source.type == "nysenate":
                fetched = fetch_nysenate(source, session)
            elif source.type == "ibo":
                fetched = fetch_ibo(source, session)
            else:
                raise ValueError(f"Unknown source type: {source.type}")
            log.info("[%s] fetched %d items", source.id, len(fetched))
            items.extend(fetched)
        except Exception as exc:  # noqa: BLE001 — isolate every source
            log.warning("[%s] FAILED: %s", source.id, exc)
            failures.append(SourceFailure(source.id, f"{type(exc).__name__}: {exc}"))
    return items, failures


# --- RSS -------------------------------------------------------------------


def _entry_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            date = _parse_date(raw)
            if date:
                return date
    return None


def fetch_rss(source: Source, session: requests.Session) -> list[Item]:
    resp = session.get(source.url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Unparseable feed: {feed.bozo_exception}")

    cutoff = window_cutoff()
    wanted_categories = {c.lower() for c in source.categories}
    items = []
    for entry in feed.entries:
        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "").strip()
        if not link or not title:
            continue
        if wanted_categories:
            entry_categories = {
                t.get("term", "").lower() for t in getattr(entry, "tags", [])
            }
            if not entry_categories & wanted_categories:
                continue
        date = _entry_date(entry)
        if date and date < cutoff:
            continue
        excerpt = _clean_html(getattr(entry, "summary", ""))
        items.append(
            Item(
                source_id=source.id,
                source_name=source.name,
                stance=source.stance,
                title=title,
                url=link,
                date=date,
                excerpt=excerpt,
                dedup_key=normalize_url(link),
            )
        )
    return items


# --- Scrape ----------------------------------------------------------------


def fetch_scrape(source: Source, session: requests.Session) -> list[Item]:
    """Extract publication links from an index page; diffing against seen.json
    (done by the caller) is what makes an item 'new'. Dates/excerpts are filled
    in later by enrich_items() for new items only."""
    resp = session.get(source.url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    pattern = re.compile(source.link_pattern) if source.link_pattern else None

    items: list[Item] = []
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(source.url, anchor["href"])
        path = urlparse(url).path
        if pattern and not pattern.search(path):
            continue
        normalized = normalize_url(url)
        if normalized in seen_urls:
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 10:  # skip "Read more" style anchors
            continue
        seen_urls.add(normalized)
        items.append(
            Item(
                source_id=source.id,
                source_name=source.name,
                stance=source.stance,
                title=title,
                url=url,
                date=None,
                excerpt="",
                dedup_key=normalized,
                needs_detail=source.fetch_detail,
            )
        )
    if not items:
        raise ValueError("Index page yielded 0 publication links (layout change?)")
    return items


def enrich_items(items: list[Item], session: requests.Session) -> None:
    """Fetch article pages for NEW scrape items to recover date + excerpt.
    Bounded: callers only pass unseen items, a handful per week."""
    for item in items:
        if not item.needs_detail:
            continue
        try:
            resp = session.get(item.url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            if not item.date:
                item.date = _detail_date(soup)
            if not item.excerpt:
                item.excerpt = _detail_excerpt(soup)
        except Exception as exc:  # noqa: BLE001
            log.warning("Detail fetch failed for %s: %s", item.url, exc)
        # An undated item still counts as fresh: it just appeared on the index,
        # and dedup ensures it passes through exactly once.
        if not item.date:
            item.date = datetime.now(timezone.utc)


def _detail_date(soup: BeautifulSoup) -> datetime | None:
    meta = soup.find("meta", property="article:published_time")
    if meta and meta.get("content"):
        date = _parse_date(meta["content"])
        if date:
            return date
    time_tag = soup.find("time")
    if time_tag:
        raw = time_tag.get("datetime") or time_tag.get_text(strip=True)
        return _parse_date(raw)
    return None


def _detail_excerpt(soup: BeautifulSoup) -> str:
    for selector in (
        {"property": "og:description"},
        {"name": "description"},
    ):
        meta = soup.find("meta", attrs=selector)
        if meta and meta.get("content", "").strip():
            return meta["content"].strip()[:EXCERPT_MAX]
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) >= 80:
            return text[:EXCERPT_MAX]
    return ""


# --- NYC IBO (NYC.gov content API) -------------------------------------------


def fetch_ibo(source: Source, session: requests.Session) -> list[Item]:
    """The IBO publications page (ibo.nyc.gov/content/publications) is
    JS-rendered; this hits the NYC.gov content API JSON it loads from."""
    resp = session.get(source.url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    cutoff = window_cutoff()
    items = []
    for entry in resp.json().get("results", []):
        values = {
            v["name"]: (v.get("value") or [None])[0] for v in entry.get("values", [])
        }
        raw_date = values.get("Publication Date")
        date = _parse_date(raw_date) if raw_date else None
        if not date or date < cutoff:
            continue
        title = values.get("Title") or entry.get("name") or ""
        link_path = values.get("Content Link URL") or f"publications/{entry.get('url', '')}"
        url = f"https://www.ibo.nyc.gov/content/{link_path.lstrip('/')}"
        items.append(
            Item(
                source_id=source.id,
                source_name=source.name,
                stance=source.stance,
                title=title.strip(),
                url=url,
                date=date,
                excerpt=_clean_html(values.get("Description") or "")[:EXCERPT_MAX],
                dedup_key=normalize_url(url),
            )
        )
    return items


# --- NY Senate Open Legislation API -----------------------------------------


def fetch_nysenate(source: Source, session: requests.Session) -> list[Item]:
    api_key = os.environ.get("NYSENATE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("NYSENATE_API_KEY is not set")

    cutoff = window_cutoff()
    items: list[Item] = []
    covered_prints: set[str] = set()

    for print_no in source.bills:
        resp = session.get(
            f"{source.url}/api/3/bills/{source.session_year}/{print_no}",
            params={"key": api_key, "view": "no_fulltext"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        bill = resp.json().get("result", {})
        covered_prints.add(bill.get("basePrintNo", print_no))
        item = _bill_to_item(bill, source, cutoff, always_track=True)
        if item:
            items.append(item)

    for term in source.search_terms:
        resp = session.get(
            f"{source.url}/api/3/bills/{source.session_year}/search",
            params={
                "term": term,
                "sort": "status.actionDate:DESC",
                "limit": 10,
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for hit in resp.json().get("result", {}).get("items", []):
            bill = hit.get("result", hit)
            base = bill.get("basePrintNo", "")
            if base in covered_prints:
                continue
            item = _bill_to_item(bill, source, cutoff, always_track=False)
            if item:
                covered_prints.add(base)
                items.append(item)
    return items


def _bill_to_item(
    bill: dict, source: Source, cutoff: datetime, always_track: bool
) -> Item | None:
    base = bill.get("basePrintNo", "")
    status = bill.get("status") or {}
    action_date = _parse_date(status.get("actionDate") or "")
    # Explicitly tracked bills alert on any status change; search hits only
    # matter when the action falls in this week's window.
    if action_date and action_date < cutoff:
        return None
    if not action_date and not always_track:
        return None

    session_year = bill.get("session", source.session_year)
    version = bill.get("activeVersion") or ""
    status_desc = status.get("statusDesc", "unknown status")
    excerpt = (
        f"Status: {status_desc}."
        f" Last action {status.get('actionDate', 'n/a')}."
        f" Summary: {(bill.get('summary') or '')[:300]}"
    )
    # Dedup key includes status + action date so the bill re-alerts on every
    # status change or new amendment instead of being seen-once-forever.
    dedup_key = (
        f"nysenate:{base}{version}:{status_desc}:{status.get('actionDate', '')}"
    )
    return Item(
        source_id=source.id,
        source_name=source.name,
        stance=source.stance,
        title=f"{base}{version}: {bill.get('title', 'Untitled bill')}",
        url=f"https://www.nysenate.gov/legislation/bills/{session_year}/{base}",
        date=action_date,
        excerpt=excerpt,
        dedup_key=dedup_key,
    )
