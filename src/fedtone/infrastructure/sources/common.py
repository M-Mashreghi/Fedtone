"""Helpers shared by the official-source HTML/PDF adapters."""

import re
import warnings
from datetime import UTC, date, datetime, time
from urllib.parse import urldefrag, urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

from fedtone.application.sources import PermanentSourceError

ABSOLUTE_URL = re.compile(r"https?://[^\s\"'<>]+")
ENGLISH_DATE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})")


def text_of(tag: Tag) -> str:
    return tag.get_text(" ", strip=True)


def parse_day_month_year(text: str, source_url: str) -> date:
    """First ``6 June 2024``-style date in ``text``."""
    match = ENGLISH_DATE.search(text)
    if match is None:
        raise PermanentSourceError(f"{source_url}: no release date in {text[:80]!r}")
    try:
        return datetime.strptime(" ".join(match.groups()), "%d %B %Y").date()
    except ValueError:
        raise PermanentSourceError(f"{source_url}: unreadable release date {text!r}") from None


def scheduled_release(day: date, at: time, zone: ZoneInfo) -> datetime:
    """A release at local wall-clock time ``at`` on ``day``, converted to UTC."""
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


def matching_links(html: str, page_url: str, pattern: re.Pattern[str], base: str) -> list[str]:
    """Absolute links matching ``pattern`` on ``base``, in page order, without duplicates.

    Works for HTML pages (``href`` attributes) and for RSS/XML feeds, whose ``<link>`` text
    the HTML parser drops, by also scanning the raw text for absolute URLs.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "html.parser")
    candidates = [a["href"] for a in soup.find_all(href=True)]
    candidates += ABSOLUTE_URL.findall(html)
    seen: dict[str, None] = {}
    for href in candidates:
        url = urldefrag(urljoin(page_url, href.strip())).url.split("?", 1)[0]
        if url.startswith(base) and pattern.search(url):
            seen.setdefault(url, None)
    return list(seen)
