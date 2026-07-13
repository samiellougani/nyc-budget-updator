"""Configuration loading: env vars, sources.yaml, profile.md."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCES_PATH = REPO_ROOT / "sources.yaml"
PROFILE_PATH = REPO_ROOT / "profile.md"
STANCE_PATH = REPO_ROOT / "prompts" / "editorial_stance.md"
STATE_PATH = REPO_ROOT / "state" / "seen.json"
DIGESTS_DIR = REPO_ROOT / "digests"
RUN_SUMMARY_PATH = REPO_ROOT / "run_summary.json"

DEFAULT_MODEL = "claude-sonnet-5"
REPO_URL = os.environ.get(
    "GITHUB_REPO_URL", "https://github.com/samiellougani/nyc-budget-updator"
)

# Several source hosts sit behind Cloudflare and 403 non-browser user agents.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    type: str  # rss | scrape | nysenate
    url: str
    stance: str
    categories: list[str] = field(default_factory=list)
    link_pattern: str = ""
    fetch_detail: bool = True
    session_year: int = 0
    bills: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)


def load_sources(path: Path = SOURCES_PATH) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = []
    for entry in raw["sources"]:
        sources.append(
            Source(
                id=entry["id"],
                name=entry["name"],
                type=entry["type"],
                url=entry["url"],
                stance=entry.get("stance", ""),
                categories=entry.get("categories", []),
                link_pattern=entry.get("link_pattern", ""),
                fetch_detail=entry.get("fetch_detail", True),
                session_year=entry.get("session_year", 0),
                bills=entry.get("bills", []),
                search_terms=entry.get("search_terms", []),
            )
        )
    return sources


def load_profile(path: Path = PROFILE_PATH) -> str:
    return path.read_text(encoding="utf-8")


def load_editorial_stance(path: Path = STANCE_PATH) -> str:
    return path.read_text(encoding="utf-8")


def parse_tripwire_keywords(profile_md: str) -> list[str]:
    """Extract the bullet list under the '## Tripwire keywords' heading."""
    match = re.search(
        r"^##\s+Tripwire keywords\s*$(.*?)(?=^##\s|\Z)",
        profile_md,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    keywords = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            keywords.append(line[2:].strip())
    return keywords


def get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
