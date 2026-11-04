"""Bank of England Monetary Policy Summary adapter (official bankofengland.co.uk pages).

Discovery reads listing pages (by default the Bank's news RSS feed) and collects links to
``/monetary-policy-summary-and-minutes/YYYY/<month>-YYYY``. Older summaries can be added by
listing their pages in ``FEDTONE_SOURCES__BOE_LISTING_URLS``.

Each page carries the Monetary Policy Summary followed by the MPC minutes. Parsing keeps only
the summary: the paragraphs after the ``<h2>`` "Monetary Policy Summary, <Month YYYY>" up to the
next ``<h2>``. ``BoeMinutesAdapter`` (``BOE/mpc_minutes``) takes the minutes section of the same
page instead. The release date comes from ``.published-date`` and must agree with the year
and month in the URL.

Release time is not printed on the page: the Bank announces policy decisions at 12:00 UK time.
"""

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from fedtone.application.sources import (
    DiscoveredDocument,
    FetchedContent,
    ParsedDocument,
    PermanentSourceError,
)
from fedtone.infrastructure.sources.common import (
    matching_links,
    parse_day_month_year,
    scheduled_release,
    text_of,
)
from fedtone.infrastructure.sources.http import HttpFetcher

CENTRAL_BANK = "BOE"
DOCUMENT_TYPE = "monetary_policy_summary"
PARSER_VERSION = "boe-mps-html-1"
MINUTES_DOCUMENT_TYPE = "mpc_minutes"
MINUTES_PARSER_VERSION = "boe-mpc-minutes-html-1"
# Attendance list closing the minutes.
ATTENDANCE = re.compile(r"following members of the committee were present", re.IGNORECASE)
BASE_URL = "https://www.bankofengland.co.uk"
DEFAULT_LISTING_URLS = (f"{BASE_URL}/rss/news",)
LONDON = ZoneInfo("Europe/London")
RELEASE_AT = time(12, 0)
SUMMARY_PATH = re.compile(
    r"/monetary-policy-summary-and-minutes/(\d{4})/([a-z]+)-(\d{4})$", re.IGNORECASE
)


def summary_month_from_url(url: str) -> tuple[int, int] | None:
    """(year, month) encoded in the URL, or None."""
    match = SUMMARY_PATH.search(url)
    if match is None or match.group(1) != match.group(3):
        return None
    try:
        month = datetime.strptime(match.group(2).capitalize(), "%B").month
    except ValueError:
        return None
    return int(match.group(1)), month


def discover_summary_links(html: str, page_url: str) -> list[str]:
    return [
        url
        for url in matching_links(html, page_url, SUMMARY_PATH, BASE_URL)
        if summary_month_from_url(url) is not None
    ]


def parse_summary_html(html: str, source_url: str) -> ParsedDocument:
    return _parse_section(html, source_url, "monetary policy summary", PARSER_VERSION)


def parse_minutes_html(html: str, source_url: str) -> ParsedDocument:
    """The MPC minutes section of the same page (Task 7.3)."""
    return _parse_section(
        html, source_url, "minutes of the monetary policy committee", MINUTES_PARSER_VERSION
    )


def _parse_section(
    html: str, source_url: str, heading_prefix: str, parser_version: str
) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    heading = next(
        (h for h in soup.find_all("h2") if text_of(h).lower().startswith(heading_prefix)),
        None,
    )
    if heading is None:
        raise PermanentSourceError(f"{source_url}: no {heading_prefix!r} section")

    date_tag = soup.find(class_="published-date")
    if not isinstance(date_tag, Tag):
        raise PermanentSourceError(f"{source_url}: published date not found")
    page_date = parse_day_month_year(text_of(date_tag), source_url)
    url_month = summary_month_from_url(source_url)
    if url_month is not None and url_month != (page_date.year, page_date.month):
        raise PermanentSourceError(f"{source_url}: page date {page_date} != URL month")

    paragraphs: list[str] = []
    for element in heading.find_all_next(["h2", "p"]):
        if element.name == "h2":
            break
        text = text_of(element)
        if ATTENDANCE.search(text):
            break
        if text:
            paragraphs.append(text)
    if not paragraphs:
        raise PermanentSourceError(f"{source_url}: no paragraphs in {heading_prefix!r}")
    published_at = scheduled_release(page_date, RELEASE_AT, LONDON)
    return ParsedDocument(text_of(heading), published_at, tuple(paragraphs), parser_version)


class BoeSummaryAdapter:
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
            for url in discover_summary_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        ordered = sorted(urls, key=lambda u: summary_month_from_url(u) or (0, 0))
        return [
            DiscoveredDocument(self.central_bank, self.document_type, url, observed_at)
            for url in ordered
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
        return parse_summary_html(html, content.source_url)


class BoeMinutesAdapter(BoeSummaryAdapter):
    """MPC minutes, published on the same page as the Monetary Policy Summary."""

    document_type = MINUTES_DOCUMENT_TYPE

    def parse(self, content: FetchedContent) -> ParsedDocument:
        html = content.content.decode("utf-8", errors="replace")
        return parse_minutes_html(html, content.source_url)
