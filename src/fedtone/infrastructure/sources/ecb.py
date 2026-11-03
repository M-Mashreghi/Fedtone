"""European Central Bank monetary policy decision adapter (official ecb.europa.eu pages).

Discovery reads the yearly lists of Governing Council monetary policy decisions
(``/press/govcdec/mopo/YYYY/html/index_include.en.html``; by default the current and the
previous year) and collects press-release links
``/press/pr/date/YYYY/html/ecb.mpYYMMDD~<id>.en.html``.

Parsing targets the press-release layout: an ``<h1>`` "Monetary policy decisions", the release
date in ``.ecb-publicationDate`` (must agree with the date in the URL) and the decision text in
``div.section``. Section headings, the ``***`` separator and the trailing "The President of the
ECB will comment ..." line are excluded.

Release time is not printed on the page. The ECB publishes the decision at 14:15 CET from 2022
(13:45 CET before). Both are before the close of the European and US markets, so daily
event alignment is the same either way; this is a schedule assumption, not page content.
"""

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
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

CENTRAL_BANK = "ECB"
DOCUMENT_TYPE = "monetary_policy_decision"
PARSER_VERSION = "ecb-mp-decision-html-1"
BASE_URL = "https://www.ecb.europa.eu"
FRANKFURT = ZoneInfo("Europe/Berlin")
DECISION_PATH = re.compile(r"/press/pr/date/\d{4}/html/ecb\.mp(\d{6})~[0-9a-f]+\.en\.html$")
TRAILING = ("the president of the ecb will comment", "for media queries")
SEPARATORS = {"***", "* * *"}


def yearly_listing_url(year: int) -> str:
    return f"{BASE_URL}/press/govcdec/mopo/{year}/html/index_include.en.html"


def default_listing_urls(today: date) -> tuple[str, ...]:
    return (yearly_listing_url(today.year - 1), yearly_listing_url(today.year))


def decision_date_from_url(url: str) -> date | None:
    match = DECISION_PATH.search(url)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%y%m%d").date()


def release_time(day: date) -> datetime:
    at = time(14, 15) if day.year >= 2022 else time(13, 45)
    return scheduled_release(day, at, FRANKFURT)


def discover_decision_links(html: str, page_url: str) -> list[str]:
    return matching_links(html, page_url, DECISION_PATH, BASE_URL)


def parse_decision_html(html: str, source_url: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    title_tag = main.find("h1")
    title = text_of(title_tag) if isinstance(title_tag, Tag) else ""
    if "monetary policy decision" not in title.lower():
        raise PermanentSourceError(f"{source_url}: page is not a monetary policy decision")

    section = main.find("div", class_="section")
    if not isinstance(section, Tag):
        raise PermanentSourceError(f"{source_url}: decision text (div.section) not found")
    date_tag = section.find(class_="ecb-publicationDate")
    if not isinstance(date_tag, Tag):
        raise PermanentSourceError(f"{source_url}: publication date not found")
    page_date = parse_day_month_year(text_of(date_tag), source_url)
    url_date = decision_date_from_url(source_url)
    if url_date is not None and url_date != page_date:
        raise PermanentSourceError(f"{source_url}: page date {page_date} != URL date {url_date}")

    paragraphs: list[str] = []
    for p in section.find_all("p"):
        if p is date_tag:
            continue
        text = text_of(p)
        if not text or text in SEPARATORS:
            continue
        if text.lower().startswith(TRAILING):
            break
        paragraphs.append(text)
    if not paragraphs:
        raise PermanentSourceError(f"{source_url}: no decision paragraphs found")
    return ParsedDocument(title, release_time(page_date), tuple(paragraphs), PARSER_VERSION)


class EcbDecisionAdapter:
    central_bank = CENTRAL_BANK
    document_type = DOCUMENT_TYPE

    def __init__(
        self,
        fetcher: HttpFetcher,
        *,
        listing_urls: Sequence[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetcher = fetcher
        self._listing_urls = tuple(listing_urls) if listing_urls else None
        self._clock = clock

    def discover(self) -> Sequence[DiscoveredDocument]:
        listings = self._listing_urls or default_listing_urls(self._clock().date())
        urls: dict[str, None] = {}
        for listing_url in listings:
            response = self._fetcher.get(listing_url)
            html = response.content.decode("utf-8", errors="replace")
            for url in discover_decision_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        ordered = sorted(urls, key=lambda u: decision_date_from_url(u) or date.min)
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
        return parse_decision_html(html, content.source_url)
