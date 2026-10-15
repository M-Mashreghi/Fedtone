"""Federal Reserve FOMC statement adapter (official federalreserve.gov pages).

Discovery reads listing pages (by default the FOMC meeting calendar) and collects links to
statement press releases (``/newsevents/pressreleases/monetaryYYYYMMDDa.htm``). Older URL
schemes used before 2006 are not supported yet.

Parsing targets the press-release layout: an ``#article`` container with the release date in
``.article__time``, the title in ``.title`` and ``For release at 2:00 p.m. EST`` in
``.releaseTime``. Site chrome and trailing boilerplate (implementation-note link, media
contact, last-update line) are excluded. Anything unexpected raises PermanentSourceError so
the document is routed to review instead of being silently mis-parsed.
"""

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
from urllib.parse import urldefrag, urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from fedtone.application.sources import (
    DiscoveredDocument,
    FetchedContent,
    ParsedDocument,
    PermanentSourceError,
)
from fedtone.infrastructure.sources.http import HttpFetcher

CENTRAL_BANK = "FED"
DOCUMENT_TYPE = "fomc_statement"
PARSER_VERSION = "fomc-statement-html-1"
BASE_URL = "https://www.federalreserve.gov"
DEFAULT_LISTING_URLS = (f"{BASE_URL}/monetarypolicy/fomccalendars.htm",)

NEW_YORK = ZoneInfo("America/New_York")
STATEMENT_PATH = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm$")
RELEASE_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?", re.IGNORECASE)
# Paragraphs starting with these mark the end of the statement text.
TRAILING_BOILERPLATE = (
    "implementation note issued",
    "for media inquiries",
    "last update",
    "related information",
)


def statement_date_from_url(url: str) -> date | None:
    match = STATEMENT_PATH.search(urldefrag(url).url.split("?", 1)[0])
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def discover_statement_links(html: str, page_url: str) -> list[str]:
    """Absolute statement URLs in a listing page, in page order, without duplicates."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}
    for link in soup.find_all("a", href=True):
        url = urldefrag(urljoin(page_url, link["href"].strip())).url
        if statement_date_from_url(url) is not None and url.startswith(BASE_URL):
            seen.setdefault(url, None)
    return list(seen)


def parse_statement_html(html: str, source_url: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find(id="article")
    if not isinstance(article, Tag):
        raise PermanentSourceError(f"{source_url}: #article container not found")

    title_tag = article.find(class_="title") or article.find(["h1", "h2", "h3"])
    title = _text(title_tag) if title_tag else ""
    if "fomc statement" not in title.lower():
        raise PermanentSourceError(f"{source_url}: page is not an FOMC statement ({title!r})")

    published_at = _published_at(article, source_url)

    heading = article.find(class_="heading")
    paragraphs: list[str] = []
    for p in article.find_all("p"):
        if heading is not None and heading in p.parents:
            continue
        text = _text(p)
        if not text:
            continue
        if text.lower().startswith(TRAILING_BOILERPLATE):
            break
        paragraphs.append(text)
    if not paragraphs:
        raise PermanentSourceError(f"{source_url}: no statement paragraphs found")
    return ParsedDocument(title, published_at, tuple(paragraphs), PARSER_VERSION)


def _text(tag: Tag) -> str:
    return tag.get_text(" ", strip=True)


def _published_at(article: Tag, source_url: str) -> datetime | None:
    """Release time in UTC, or None when the page states no time.

    The date comes from ``.article__time`` and must agree with the date in the URL.
    """
    url_date = statement_date_from_url(source_url)
    page_date = None
    time_tag = article.find(class_="article__time")
    if time_tag is not None:
        try:
            page_date = datetime.strptime(_text(time_tag), "%B %d, %Y").date()
        except ValueError:
            raise PermanentSourceError(
                f"{source_url}: unreadable release date {_text(time_tag)!r}"
            ) from None
    if page_date and url_date and page_date != url_date:
        raise PermanentSourceError(f"{source_url}: page date {page_date} != URL date {url_date}")
    release_date = page_date or url_date
    if release_date is None:
        raise PermanentSourceError(f"{source_url}: release date not found")

    release_tag = article.find(class_="releaseTime")
    match = RELEASE_TIME.search(_text(release_tag)) if release_tag else None
    if match is None:
        return None
    hour, minute = int(match.group(1)) % 12, int(match.group(2))
    if match.group(3).lower() == "p":
        hour += 12
    local = datetime.combine(release_date, time(hour, minute), tzinfo=NEW_YORK)
    return local.astimezone(UTC)


class FomcStatementAdapter:
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
            for url in discover_statement_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        ordered = sorted(urls, key=lambda u: statement_date_from_url(u) or date.min)
        return [
            DiscoveredDocument(CENTRAL_BANK, DOCUMENT_TYPE, url, observed_at) for url in ordered
        ]

    def fetch(self, document: DiscoveredDocument) -> FetchedContent:
        response = self._fetcher.get(document.source_url)
        return FetchedContent(
            # Keep the canonical (requested) URL; redirects do not change document identity.
            source_url=document.source_url,
            content=response.content,
            content_type=response.content_type,
            fetched_at=self._clock(),
        )

    def parse(self, content: FetchedContent) -> ParsedDocument:
        html = content.content.decode("utf-8", errors="replace")
        return parse_statement_html(html, content.source_url)
