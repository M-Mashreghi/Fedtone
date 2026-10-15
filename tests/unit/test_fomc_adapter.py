from datetime import UTC, date, datetime

import httpx
import pytest

from fedtone.application.sources import (
    CentralBankSourceAdapter,
    DiscoveredDocument,
    FetchedContent,
    PermanentSourceError,
    TransientSourceError,
)
from fedtone.infrastructure.sources.fomc import (
    FomcStatementAdapter,
    discover_statement_links,
    parse_statement_html,
    statement_date_from_url,
)
from fedtone.infrastructure.sources.http import HttpFetcher
from tests.fomc_fixtures import BASE, fixture_transport, read

NOW = datetime(2024, 4, 1, 12, tzinfo=UTC)
JAN = f"{BASE}/newsevents/pressreleases/monetary20240131a.htm"
DEC = f"{BASE}/newsevents/pressreleases/monetary20231213a.htm"
MAR = f"{BASE}/newsevents/pressreleases/monetary20240320a.htm"


def adapter(transport=None, max_retries=2) -> FomcStatementAdapter:
    client = httpx.Client(transport=transport or fixture_transport())
    fetcher = HttpFetcher(client, max_retries=max_retries, sleep=lambda _: None)
    return FomcStatementAdapter(fetcher, clock=lambda: NOW)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (JAN, date(2024, 1, 31)),
        (JAN + "#top", date(2024, 1, 31)),
        (f"{BASE}/newsevents/pressreleases/monetary20240131a1.htm", None),
        (f"{BASE}/newsevents/pressreleases/monetary20240131a1.pdf", None),
        (f"{BASE}/monetarypolicy/fomcminutes20240131.htm", None),
    ],
)
def test_statement_date_from_url(url: str, expected) -> None:
    assert statement_date_from_url(url) == expected


def test_discover_links_from_calendar_fixture() -> None:
    links = discover_statement_links(
        read("calendar.html"), f"{BASE}/monetarypolicy/fomccalendars.htm"
    )

    # Implementation notes, PDFs, minutes, duplicates and off-site look-alikes are excluded.
    assert links == [JAN, MAR, DEC]


def test_adapter_discovers_in_chronological_order() -> None:
    discovered = adapter().discover()

    assert [d.source_url for d in discovered] == [DEC, JAN, MAR]
    assert all(d.central_bank == "FED" and d.document_type == "fomc_statement" for d in discovered)
    assert all(d.observed_at == NOW for d in discovered)


def test_adapter_satisfies_source_contract() -> None:
    assert isinstance(adapter(), CentralBankSourceAdapter)


def test_parse_extracts_title_release_time_and_policy_paragraphs() -> None:
    parsed = parse_statement_html(read("statement_20240131.html"), JAN)

    assert parsed.title == "Federal Reserve issues FOMC statement"
    # 2:00 p.m. EST on January 31, 2024 is 19:00 UTC.
    assert parsed.published_at == datetime(2024, 1, 31, 19, 0, tzinfo=UTC)
    assert len(parsed.paragraphs) == 4
    assert parsed.paragraphs[0].startswith("Synthetic indicators suggest")
    assert parsed.paragraphs[2].startswith("In support of its goals")
    assert parsed.paragraphs[3].startswith("Voting for the monetary policy action")
    assert parsed.parser_version == "fomc-statement-html-1"


def test_parse_excludes_navigation_share_and_trailing_boilerplate() -> None:
    text = " ".join(parse_statement_html(read("statement_20240131.html"), JAN).paragraphs)

    for boilerplate in (
        "About the Fed",
        "Share",
        "Implementation Note",
        "media inquiries",
        "Last Update",
    ):
        assert boilerplate not in text


def test_parse_handles_daylight_saving_time() -> None:
    parsed = parse_statement_html(read("statement_20240320.html"), MAR)

    # 2:00 p.m. EDT on March 20, 2024 is 18:00 UTC.
    assert parsed.published_at == datetime(2024, 3, 20, 18, 0, tzinfo=UTC)


def test_parse_without_release_time_keeps_published_at_unknown() -> None:
    html = read("statement_20240131.html").replace("For release at 2:00 p.m. EST", "")

    assert parse_statement_html(html, JAN).published_at is None


@pytest.mark.parametrize(
    ("name", "url", "message"),
    [
        ("broken_layout.html", JAN, "#article container not found"),
        (
            "not_a_statement.html",
            f"{BASE}/newsevents/pressreleases/monetary20240221a.htm",
            "not an FOMC statement",
        ),
        ("statement_20240131.html", DEC, "page date 2024-01-31 != URL date 2023-12-13"),
    ],
)
def test_unexpected_pages_raise_permanent_errors(name: str, url: str, message: str) -> None:
    with pytest.raises(PermanentSourceError, match=message):
        parse_statement_html(read(name), url)


def test_fetch_keeps_canonical_url_and_records_fetch_time() -> None:
    fed = adapter()
    discovered = DiscoveredDocument("FED", "fomc_statement", JAN, NOW)

    fetched = fed.fetch(discovered)

    assert fetched.source_url == JAN
    assert fetched.fetched_at == NOW
    assert fetched.content_type.startswith("text/html")
    assert fed.parse(fetched).published_at == datetime(2024, 1, 31, 19, tzinfo=UTC)


def test_fetch_retries_transient_failure_boundedly() -> None:
    fed = adapter(fixture_transport(overrides={JAN: httpx.Response(503)}), max_retries=2)

    with pytest.raises(TransientSourceError, match="after 3 attempts"):
        fed.fetch(DiscoveredDocument("FED", "fomc_statement", JAN, NOW))


def test_parse_of_non_utf8_bytes_does_not_crash() -> None:
    content = read("statement_20240131.html").encode("utf-8") + b"\xff"

    parsed = adapter().parse(FetchedContent(JAN, content, "text/html", NOW))

    assert parsed.paragraphs
