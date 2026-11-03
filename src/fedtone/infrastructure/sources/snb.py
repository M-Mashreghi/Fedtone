"""Swiss National Bank monetary policy assessment adapter (official snb.ch pages).

Discovery reads the monetary policy decisions page and collects press-release pages
``/en/publications/communication/press-releases[-restricted]/pre_YYYYMMDD[_n]``.

The press-release page only links a PDF; ``fetch`` downloads that PDF, and the snapshot stores
the PDF bytes under the press-release URL (the canonical identity). Parsing extracts the PDF
text with ``pypdf`` and rebuilds paragraphs from wrapped lines: a line that ends a sentence and
is clearly shorter than a full line closes a paragraph. Repeated page headers (``Press
release``, ``Page n/m``, the date line), the communications address block and the trailing
"More detailed information ..." pointer are removed. The page must state "Monetary policy
assessment of <date>" and its date must agree with the URL.

Release time is not printed in the PDF: the SNB publishes its assessment at 09:30 Zurich time.
"""

import io
import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

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
)
from fedtone.infrastructure.sources.http import HttpFetcher

CENTRAL_BANK = "SNB"
DOCUMENT_TYPE = "monetary_policy_assessment"
PARSER_VERSION = "snb-mpa-pdf-1"
BASE_URL = "https://www.snb.ch"
DEFAULT_LISTING_URLS = (f"{BASE_URL}/en/the-snb/mandates-goals/monetary-policy/decisions",)
ZURICH = ZoneInfo("Europe/Zurich")
RELEASE_AT = time(9, 30)
PRESS_RELEASE_PATH = re.compile(
    r"/en/publications/communication/press-releases(?:-restricted)?/pre_(\d{8})(?:_\d+)?$"
)
TITLE = re.compile(r"Monetary policy assessment of (\d{1,2} [A-Z][a-z]+ \d{4})")
PAGE_HEADER = re.compile(r"^(press release|page \d+/\d+|\d{1,2} [a-z]+ \d{4})$", re.IGNORECASE)
ADDRESS = re.compile(r"^(communications|p\.o\. box|telephone|[\w.+-]+@[\w.-]+|zurich, )", re.I)
TRAILING = ("more detailed information",)
SENTENCE_END = (".", ":", ";", "!", "?")
# A wrapped line is at least this share of the longest line; shorter ones end a paragraph.
FULL_LINE_SHARE = 0.85


def release_date_from_url(url: str) -> date | None:
    match = PRESS_RELEASE_PATH.search(url)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def discover_press_release_links(html: str, page_url: str) -> list[str]:
    return matching_links(html, page_url, PRESS_RELEASE_PATH, BASE_URL)


def find_pdf_link(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if href.lower().split("?", 1)[0].endswith(".pdf"):
            return urljoin(page_url, href)
    raise PermanentSourceError(f"{page_url}: no PDF linked from the press release")


def pdf_lines(content: bytes, source_url: str) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise PermanentSourceError(
            f"{source_url}: unreadable PDF ({exc.__class__.__name__})"
        ) from None
    lines = []
    for page in pages:
        lines.extend(line.strip() for line in page.splitlines())
    return lines


def rebuild_paragraphs(lines: Sequence[str]) -> list[str]:
    """Join wrapped PDF lines into paragraphs (see module docstring)."""
    longest = max((len(line) for line in lines), default=0)
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if line.endswith(SENTENCE_END) and len(line) < FULL_LINE_SHARE * longest:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def parse_assessment_pdf(content: bytes, source_url: str) -> ParsedDocument:
    lines = pdf_lines(content, source_url)
    title_index = next((i for i, line in enumerate(lines) if TITLE.search(line)), None)
    if title_index is None:
        raise PermanentSourceError(f"{source_url}: not a monetary policy assessment")
    title = TITLE.search(lines[title_index]).group(0)
    page_date = parse_day_month_year(title, source_url)
    url_date = release_date_from_url(source_url)
    if url_date is not None and url_date != page_date:
        raise PermanentSourceError(f"{source_url}: PDF date {page_date} != URL date {url_date}")

    body = []
    for line in lines[title_index + 1 :]:
        if not line or PAGE_HEADER.match(line) or ADDRESS.match(line):
            continue
        if line.lower().startswith(TRAILING):
            break
        body.append(line)
    paragraphs = rebuild_paragraphs(body)
    if len(paragraphs) < 2:
        raise PermanentSourceError(f"{source_url}: no assessment paragraphs found")
    published_at = scheduled_release(page_date, RELEASE_AT, ZURICH)
    return ParsedDocument(title, published_at, tuple(paragraphs), PARSER_VERSION)


class SnbAssessmentAdapter:
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
            for url in discover_press_release_links(html, response.url):
                urls.setdefault(url, None)
        observed_at = self._clock()
        ordered = sorted(urls, key=lambda u: (release_date_from_url(u) or date.min, u))
        return [
            DiscoveredDocument(CENTRAL_BANK, DOCUMENT_TYPE, url, observed_at) for url in ordered
        ]

    def fetch(self, document: DiscoveredDocument) -> FetchedContent:
        page = self._fetcher.get(document.source_url)
        pdf_url = find_pdf_link(page.content.decode("utf-8", errors="replace"), page.url)
        pdf = self._fetcher.get(pdf_url)
        return FetchedContent(
            source_url=document.source_url,
            content=pdf.content,
            content_type=pdf.content_type or "application/pdf",
            fetched_at=self._clock(),
        )

    def parse(self, content: FetchedContent) -> ParsedDocument:
        return parse_assessment_pdf(content.content, content.source_url)
