# ruff: noqa: E501 - synthetic PDF lines keep the full width of the real layout
"""Synthetic ECB, BoE and SNB pages served by URL (no network)."""

from pathlib import Path

import httpx

from tests.pdf_fixture import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
ECB = "https://www.ecb.europa.eu"
BOE = "https://www.bankofengland.co.uk"
SNB = "https://www.snb.ch"

ECB_LISTING = f"{ECB}/press/govcdec/mopo/2024/html/index_include.en.html"
ECB_JUNE = f"{ECB}/press/pr/date/2024/html/ecb.mp240606~2148ecdb3c.en.html"
ECB_JULY = f"{ECB}/press/pr/date/2024/html/ecb.mp240718~b9e0ddd9d5.en.html"
BOE_RSS = f"{BOE}/rss/news"
BOE_AUGUST = f"{BOE}/monetary-policy-summary-and-minutes/2024/august-2024"
BOE_SEPTEMBER = f"{BOE}/monetary-policy-summary-and-minutes/2024/september-2024"
SNB_DECISIONS = f"{SNB}/en/the-snb/mandates-goals/monetary-policy/decisions"
SNB_PRESS = f"{SNB}/en/publications/communication/press-releases-restricted/"
SNB_JUNE = f"{SNB_PRESS}pre_20240620"
SNB_SEPTEMBER = f"{SNB_PRESS}pre_20240926"


def snb_pdf_url(release: str) -> str:
    return (
        f"{SNB}/public/asset/en/www-snb-ch/publications/communication/"
        f"press-releases-restricted/{release}/publications0_en/{release}_e.pdf"
    )


def snb_assessment_pdf(day: str, rate: str, *, title_date: str | None = None) -> bytes:
    """A synthetic two-page assessment laid out like SNB press-release PDFs."""
    title_date = title_date or day
    page_one = [
        "Press release",
        "Page 1/2",
        "Communications",
        "P.O. Box, CH-8022 Zurich",
        "Telephone +41 58 631 00 00",
        "communications@example.org",
        f"Zurich, {day}",
        f"Monetary policy assessment of {title_date}",
        f"Synthetic National Bank sets its policy rate at {rate}",
        f"The Synthetic National Bank is setting its policy rate at {rate}. The change applies from",
        "tomorrow. The bank remains willing to be active in the foreign exchange market as necessary.",
        "Underlying inflationary pressure has decreased again compared to the previous quarter in",
        "the synthetic data, and monetary conditions remain appropriate.",
        "Inflation has risen slightly since the last assessment and was driven above all by higher",
        "prices for domestic services in the synthetic economy, while goods prices were stable over",
    ]
    page_two = [
        day,
        "Press release",
        "Page 2/2",
        "the past months according to the synthetic statistics published by the statistical office.",
        "The forecast for the synthetic economy is subject to significant uncertainty, and risks to",
        "growth stem mainly from developments abroad.",
        "More detailed information on the monetary policy decision can be found in the introductory",
        "remarks of the Governing Board.",
    ]
    return make_pdf([page_one, page_two])


def _html(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


def _press_page(release: str) -> bytes:
    return _html("snb/synthetic/press_release.html").replace(b"PRE", release.encode())


def bank_routes() -> dict[str, tuple[bytes, str]]:
    html, xml, pdf = "text/html; charset=utf-8", "application/rss+xml", "application/pdf"
    return {
        ECB_LISTING: (_html("ecb/synthetic/listing_2024.html"), html),
        f"{ECB}/press/govcdec/mopo/2023/html/index_include.en.html": (b"<dl></dl>", html),
        ECB_JUNE: (_html("ecb/synthetic/decision_240606.html"), html),
        ECB_JULY: (_html("ecb/synthetic/decision_240718.html"), html),
        BOE_RSS: (_html("boe/synthetic/news_rss.xml"), xml),
        BOE_AUGUST: (_html("boe/synthetic/summary_2024_august.html"), html),
        BOE_SEPTEMBER: (_html("boe/synthetic/summary_2024_september.html"), html),
        SNB_DECISIONS: (_html("snb/synthetic/decisions.html"), html),
        SNB_JUNE: (_press_page("pre_20240620"), html),
        SNB_SEPTEMBER: (_press_page("pre_20240926"), html),
        snb_pdf_url("pre_20240620"): (snb_assessment_pdf("20 June 2024", "1.25%"), pdf),
        snb_pdf_url("pre_20240926"): (snb_assessment_pdf("26 September 2024", "1.0%"), pdf),
    }


def bank_transport(overrides: dict[str, httpx.Response] | None = None) -> httpx.MockTransport:
    routes = bank_routes()
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in overrides:
            return overrides[url]
        if url in routes:
            content, content_type = routes[url]
            return httpx.Response(200, content=content, headers={"content-type": content_type})
        return httpx.Response(404)

    return httpx.MockTransport(handler)
