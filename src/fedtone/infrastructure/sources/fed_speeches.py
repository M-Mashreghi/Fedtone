"""Federal Reserve Board speeches adapter (official federalreserve.gov pages).

Discovery reads the speeches RSS feed (``/feeds/speeches.xml``) and collects links to
``/newsevents/speech/<name>YYYYMMDD<letter>.htm``.

Parsing uses the press-release layout: ``#article`` with the date in ``.article__time``, the
title in ``.title``, the speaker in ``.speaker`` (e.g. "Governor Michael S. Barr"; the office
is stripped so one person keeps one name across roles) and the text in the body column.
Footnotes, references and "Return to text" links are excluded.

The page does not state a delivery time. ``published_at`` is the end of the speech date in
New York (23:59), a conservative "available by" time: an analysis never treats a speech as
known before it was given.
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
from fedtone.infrastructure.sources.common import matching_links, scheduled_release, text_of
from fedtone.infrastructure.sources.http import HttpFetcher

CENTRAL_BANK = "FED"
DOCUMENT_TYPE = "speech"
PARSER_VERSION = "fed-speech-html-1"
BASE_URL = "https://www.federalreserve.gov"
DEFAULT_LISTING_URLS = (f"{BASE_URL}/feeds/speeches.xml",)
NEW_YORK = ZoneInfo("America/New_York")
AVAILABLE_BY = time(23, 59)
SPEECH_PATH = re.compile(r"/newsevents/speech/([a-z]+)(\d{8})[a-z]\.htm$")
TITLES = re.compile(
    r"^(?:chair(?:man)?|vice chair(?:man)?(?: for supervision)?|governor|president|"
    r"first vice president)\s+",
    re.IGNORECASE,
)
FOOTNOTE = re.compile(r"^(?:\d+|[ivxlc]+)\.\s")
END_SECTIONS = ("references",)


def speech_date_from_url(url: str) -> date | None:
    match = SPEECH_PATH.search(url)
    if match is None:
        return None
    return datetime.strptime(match.group(2), "%Y%m%d").date()


def speaker_name(text: str) -> str:
    """'Vice Chair for Supervision Michelle W. Bowman' -> 'Michelle W. Bowman'."""
    return TITLES.sub("", " ".join(text.split())).strip()


def discover_speech_links(html: str, page_url: str) -> list[str]:
    return matching_links(html, page_url, SPEECH_PATH, BASE_URL)


def parse_speech_html(html: str, source_url: str) -> ParsedDocument:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find(id="article")
    if not isinstance(article, Tag):
        raise PermanentSourceError(f"{source_url}: #article container not found")
    heading = article.find(class_="heading")
    speaker_tag = article.find(class_="speaker")
    title_tag = article.find(class_="title")
    if not isinstance(speaker_tag, Tag) or not isinstance(title_tag, Tag):
        raise PermanentSourceError(f"{source_url}: page is not a speech (no speaker or title)")
    speaker = speaker_name(text_of(speaker_tag))
    if not speaker:
        raise PermanentSourceError(f"{source_url}: empty speaker")

    time_tag = article.find(class_="article__time")
    try:
        page_date = datetime.strptime(text_of(time_tag), "%B %d, %Y").date() if time_tag else None
    except ValueError:
        raise PermanentSourceError(f"{source_url}: unreadable speech date") from None
    url_date = speech_date_from_url(source_url)
    if page_date and url_date and page_date != url_date:
        raise PermanentSourceError(f"{source_url}: page date {page_date} != URL date {url_date}")
    day = page_date or url_date
    if day is None:
        raise PermanentSourceError(f"{source_url}: speech date not found")

    paragraphs: list[str] = []
    for p in article.find_all("p"):
        if heading is not None and heading in p.parents:
            continue
        text = text_of(p)
        if not text:
            continue
        if text.lower() in END_SECTIONS or FOOTNOTE.match(text) or "Return to text" in text:
            break
        paragraphs.append(text)
    if not paragraphs:
        raise PermanentSourceError(f"{source_url}: no speech paragraphs found")
    return ParsedDocument(
        title=text_of(title_tag),
        published_at=scheduled_release(day, AVAILABLE_BY, NEW_YORK),
        paragraphs=tuple(paragraphs),
        parser_version=PARSER_VERSION,
        speaker=speaker,
    )


class FedSpeechAdapter:
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
            for url in discover_speech_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        ordered = sorted(urls, key=lambda u: (speech_date_from_url(u) or date.min, u))
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
        return parse_speech_html(html, content.source_url)
