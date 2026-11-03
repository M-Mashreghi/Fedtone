"""ECB, BoE and SNB source adapters: discovery, parsing and layout drift (Task 7.1)."""

from datetime import UTC, date, datetime

import httpx
import pytest

from fedtone.application.sources import (
    CentralBankSourceAdapter,
    DiscoveredDocument,
    FetchedContent,
    PermanentSourceError,
)
from fedtone.infrastructure.sources import boe, ecb, snb
from fedtone.infrastructure.sources.http import HttpFetcher
from tests.bank_fixtures import (
    BOE_AUGUST,
    BOE_RSS,
    BOE_SEPTEMBER,
    ECB_JULY,
    ECB_JUNE,
    ECB_LISTING,
    FIXTURES,
    SNB_DECISIONS,
    SNB_JUNE,
    SNB_SEPTEMBER,
    bank_transport,
    snb_assessment_pdf,
    snb_pdf_url,
)
from tests.pdf_fixture import make_pdf

NOW = datetime(2024, 10, 1, tzinfo=UTC)


def fetcher(overrides=None) -> HttpFetcher:
    client = httpx.Client(transport=bank_transport(overrides))
    return HttpFetcher(client, max_retries=0, sleep=lambda _: None)


def read(path: str) -> str:
    return (FIXTURES / path).read_text(encoding="utf-8")


ADAPTERS = {
    "ECB": lambda f: ecb.EcbDecisionAdapter(f, listing_urls=[ECB_LISTING], clock=lambda: NOW),
    "BOE": lambda f: boe.BoeSummaryAdapter(f, clock=lambda: NOW),
    "SNB": lambda f: snb.SnbAssessmentAdapter(f, clock=lambda: NOW),
}
EXPECTED = {
    "ECB": ([ECB_JUNE, ECB_JULY], "monetary_policy_decision"),
    "BOE": ([BOE_AUGUST, BOE_SEPTEMBER], "monetary_policy_summary"),
    "SNB": ([SNB_JUNE, SNB_SEPTEMBER], "monetary_policy_assessment"),
}


@pytest.mark.parametrize("bank", sorted(ADAPTERS))
def test_adapter_follows_the_shared_contract(bank: str) -> None:
    adapter = ADAPTERS[bank](fetcher())

    assert isinstance(adapter, CentralBankSourceAdapter)
    discovered = adapter.discover()

    urls, doc_type = EXPECTED[bank]
    assert [d.source_url for d in discovered] == urls  # oldest first, no duplicates
    assert {(d.central_bank, d.document_type) for d in discovered} == {(bank, doc_type)}
    for document in discovered:
        parsed = adapter.parse(adapter.fetch(document))
        assert parsed.paragraphs and parsed.published_at.utcoffset() is not None
        assert parsed.parser_version


# -- ECB -------------------------------------------------------------------------------------


def test_ecb_decision_text_date_and_release_time() -> None:
    parsed = ecb.parse_decision_html(read("ecb/synthetic/decision_240606.html"), ECB_JUNE)

    assert parsed.title == "Monetary policy decisions"
    # 14:15 Frankfurt summer time = 12:15 UTC
    assert parsed.published_at == datetime(2024, 6, 6, 12, 15, tzinfo=UTC)
    assert parsed.paragraphs[0].startswith("The Governing Council today decided to lower")
    joined = " ".join(parsed.paragraphs)
    assert "***" not in parsed.paragraphs
    assert "Key interest rates" not in joined  # section headings are not text
    assert "press conference" not in joined and "media queries" not in joined
    assert "6 June 2024" not in parsed.paragraphs
    assert parsed.parser_version == "ecb-mp-decision-html-1"


def test_ecb_release_time_before_2022() -> None:
    assert ecb.release_time(date(2019, 1, 24)) == datetime(2019, 1, 24, 12, 45, tzinfo=UTC)


def test_ecb_discovery_filters_other_documents_and_hosts() -> None:
    links = ecb.discover_decision_links(read("ecb/synthetic/listing_2024.html"), ECB_LISTING)

    assert links == [ECB_JULY, ECB_JUNE]


def test_ecb_default_listing_covers_this_and_last_year() -> None:
    urls = ecb.default_listing_urls(date(2026, 3, 1))

    assert [u.split("/mopo/")[1][:4] for u in urls] == ["2025", "2026"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h.replace("<h1>Monetary policy decisions</h1>", "<h1>Account</h1>"), "not a"),
        (lambda h: h.replace('class="section"', 'class="body"'), "div.section"),
        (lambda h: h.replace("6 June 2024", "7 June 2024", 1), "URL date"),
        (lambda h: h.replace('class="ecb-publicationDate"', 'class="date"'), "publication date"),
    ],
)
def test_ecb_layout_drift_is_a_permanent_error(mutate, message: str) -> None:
    html = mutate(read("ecb/synthetic/decision_240606.html"))

    with pytest.raises(PermanentSourceError, match=message):
        ecb.parse_decision_html(html, ECB_JUNE)


# -- BoE -------------------------------------------------------------------------------------


def test_boe_summary_excludes_minutes_and_chrome() -> None:
    parsed = boe.parse_summary_html(read("boe/synthetic/summary_2024_august.html"), BOE_AUGUST)

    assert parsed.title == "Monetary Policy Summary, August 2024"
    # 12:00 London summer time = 11:00 UTC
    assert parsed.published_at == datetime(2024, 8, 1, 11, 0, tzinfo=UTC)
    assert len(parsed.paragraphs) == 4
    joined = " ".join(parsed.paragraphs)
    assert "cookies" not in joined and "Monetary Policy Report" not in joined
    assert "1: Before turning" not in joined  # minutes are a separate document type


def test_boe_discovery_reads_rss_links() -> None:
    links = boe.discover_summary_links(read("boe/synthetic/news_rss.xml"), BOE_RSS)

    assert links == [BOE_SEPTEMBER, BOE_AUGUST]  # speech and mismatched year ignored


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h.replace("Monetary Policy Summary, August", "Summary, August"), "no Monetary"),
        (lambda h: h.replace("01 August 2024", "01 July 2024"), "URL month"),
        (lambda h: h.replace('class="published-date"', 'class="date"'), "published date"),
    ],
)
def test_boe_layout_drift_is_a_permanent_error(mutate, message: str) -> None:
    html = mutate(read("boe/synthetic/summary_2024_august.html"))

    with pytest.raises(PermanentSourceError, match=message):
        boe.parse_summary_html(html, BOE_AUGUST)


# -- SNB -------------------------------------------------------------------------------------


def test_snb_pdf_paragraphs_are_rebuilt_without_headers() -> None:
    parsed = snb.parse_assessment_pdf(snb_assessment_pdf("20 June 2024", "1.25%"), SNB_JUNE)

    assert parsed.title == "Monetary policy assessment of 20 June 2024"
    # 09:30 Zurich summer time = 07:30 UTC
    assert parsed.published_at == datetime(2024, 6, 20, 7, 30, tzinfo=UTC)
    joined = " ".join(parsed.paragraphs)
    for removed in ("Press release", "Page 1/2", "P.O. Box", "Telephone", "@", "More detailed"):
        assert removed not in joined
    # A paragraph that continues across the page break is joined back together.
    assert any("goods prices were stable over the past months" in p for p in parsed.paragraphs)
    assert parsed.paragraphs[-1].endswith("developments abroad.")
    assert parsed.parser_version == "snb-mpa-pdf-1"


def test_snb_fetch_downloads_the_linked_pdf_under_the_page_url() -> None:
    adapter = snb.SnbAssessmentAdapter(fetcher(), clock=lambda: NOW)
    document = DiscoveredDocument("SNB", "monetary_policy_assessment", SNB_JUNE, NOW)

    fetched = adapter.fetch(document)

    assert fetched.source_url == SNB_JUNE
    assert fetched.content.startswith(b"%PDF")
    assert fetched.content_type == "application/pdf"


def test_snb_discovery_keeps_assessments_only() -> None:
    links = snb.discover_press_release_links(read("snb/synthetic/decisions.html"), SNB_DECISIONS)

    assert links == [SNB_SEPTEMBER, SNB_JUNE]
    assert snb.release_date_from_url(f"{SNB_JUNE}_2") == date(2024, 6, 20)


def test_snb_press_page_without_pdf_is_permanent() -> None:
    overrides = {SNB_JUNE: httpx.Response(200, text="<html><a href='/x.htm'>x</a></html>")}
    adapter = snb.SnbAssessmentAdapter(fetcher(overrides), clock=lambda: NOW)

    with pytest.raises(PermanentSourceError, match="no PDF"):
        adapter.fetch(DiscoveredDocument("SNB", "monetary_policy_assessment", SNB_JUNE, NOW))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a pdf at all", "unreadable PDF"),
        (make_pdf([["Press release", "Annual report 2024", "Some text."]]), "not a monetary"),
        (snb_assessment_pdf("20 June 2024", "1%", title_date="21 June 2024"), "URL date"),
        (make_pdf([["Monetary policy assessment of 20 June 2024", "Only one line."]]), "no ass"),
    ],
)
def test_snb_pdf_drift_is_a_permanent_error(content: bytes, message: str) -> None:
    with pytest.raises(PermanentSourceError, match=message):
        snb.parse_assessment_pdf(content, SNB_JUNE)


def test_snb_pdf_link_route_exists_for_fixtures() -> None:
    adapter = snb.SnbAssessmentAdapter(fetcher(), clock=lambda: NOW)
    content = adapter.fetch(DiscoveredDocument("SNB", "x", SNB_SEPTEMBER, NOW))

    parsed = adapter.parse(FetchedContent(SNB_SEPTEMBER, content.content, "application/pdf", NOW))
    assert parsed.published_at.date() == date(2024, 9, 26)
    assert snb_pdf_url("pre_20240926").endswith("pre_20240926_e.pdf")
