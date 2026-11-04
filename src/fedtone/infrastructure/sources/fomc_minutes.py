"""FOMC minutes adapter (official federalreserve.gov pages).

Discovery reads the FOMC meeting calendar and collects ``/monetarypolicy/fomcminutesYYYYMMDD.htm``
links; the date in the URL is the last day of the meeting (the policy decision).

Parsing keeps the minutes' narrative: the paragraphs of ``#article`` after the heading
"Minutes of the Federal Open Market Committee", up to the "Notation Vote" / "Attendance"
sections. Footnotes ("Return to text") are excluded.

The page does not state its release time. Minutes are published three weeks after the policy
decision at 2:00 p.m. Eastern; ``published_at`` follows that schedule (a schedule assumption,
noted in docs/ARCHITECTURE.md).
"""

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from fedtone.application.sources import (
    DiscoveredDocument,
    FetchedContent,
    ParsedDocument,
    PermanentSourceError,
)
from fedtone.infrastructure.sources.common import matching_links, scheduled_release, text_of
from fedtone.infrastructure.sources.http import HttpFetcher

CENTRAL_BANK = "FED"
DOCUMENT_TYPE = "fomc_minutes"
PARSER_VERSION = "fomc-minutes-html-1"
BASE_URL = "https://www.federalreserve.gov"
DEFAULT_LISTING_URLS = (f"{BASE_URL}/monetarypolicy/fomccalendars.htm",)
NEW_YORK = ZoneInfo("America/New_York")
RELEASE_LAG = timedelta(days=21)
RELEASE_AT = time(14, 0)
MINUTES_PATH = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm$")
STOP = ("notation vote", "attendance")


def meeting_date_from_url(url: str) -> date | None:
    match = MINUTES_PATH.search(url)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def release_time(meeting_end: date) -> datetime:
    return scheduled_release(meeting_end + RELEASE_LAG, RELEASE_AT, NEW_YORK)


def discover_minutes_links(html: str, page_url: str) -> list[str]:
    return matching_links(html, page_url, MINUTES_PATH, BASE_URL)


def parse_minutes_html(html: str, source_url: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find(id="article")
    if not isinstance(article, Tag):
        raise PermanentSourceError(f"{source_url}: #article container not found")
    heading = next(
        (
            h
            for h in article.find_all(["h2", "h3", "h4"])
            if "minutes of the federal open market committee" in text_of(h).lower()
        ),
        None,
    )
    if heading is None:
        raise PermanentSourceError(f"{source_url}: page is not FOMC minutes")
    meeting = meeting_date_from_url(source_url)
    if meeting is None:
        raise PermanentSourceError(f"{source_url}: meeting date not in URL")

    paragraphs: list[str] = []
    for p in heading.find_all_next("p"):
        if article not in p.parents:
            break
        text = text_of(p)
        if not text:
            continue
        if text.lower().startswith(STOP):
            break
        if "Return to text" in text:
            continue
        paragraphs.append(text)
    if len(paragraphs) < 3:
        raise PermanentSourceError(f"{source_url}: no minutes paragraphs found")
    title = f"{text_of(heading)}, meeting ending {meeting:%B %d, %Y}"
    return ParsedDocument(title, release_time(meeting), tuple(paragraphs), PARSER_VERSION)


class FomcMinutesAdapter:
    central_bank = CENTRAL_BANK
    document_type = DOCUMENT_TYPE

    def __init__(
        self,
        fetcher: HttpFetcher,
        *,
        listing_urls: Sequence[str] = DEFAULT_LISTING_URLS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetcher = fetcher
        self._listing_urls = tuple(listing_urls)
        self._clock = clock

    def discover(self) -> Sequence[DiscoveredDocument]:
        urls: dict[str, None] = {}
        for listing_url in self._listing_urls:
            response = self._fetcher.get(listing_url)
            html = response.content.decode("utf-8", errors="replace")
            for url in discover_minutes_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        # Minutes of a meeting are only published three weeks later: skip future ones.
        released = [u for u in urls if release_time(meeting_date_from_url(u)) <= observed_at]
        ordered = sorted(released, key=lambda u: meeting_date_from_url(u))
        return [
            DiscoveredDocument(CENTRAL_BANK, DOCUMENT_TYPE, url, observed_at) for url in ordered
        ]

    def fetch(self, document: DiscoveredDocument) -> FetchedContent:
        response = self._fetcher.get(document.source_url)
        return FetchedContent(
            source_url=document.source_url,
            content=response.content,
            content_type=response.content_type,
            fetched_at=self._clock(),
        )

    def parse(self, content: FetchedContent) -> ParsedDocument:
        html = content.content.decode("utf-8", errors="replace")
        return parse_minutes_html(html, content.source_url)
