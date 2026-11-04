"""Parser checks against captured official ECB, BoE and SNB pages (skipped when none exist).

Capture them with ``python scripts/capture_bank_fixtures.py``.
"""

from datetime import date

import pytest

from fedtone.domain.text import normalize_paragraphs
from fedtone.infrastructure.sources import boe, ecb, fed_speeches, fomc_minutes, snb
from tests.bank_fixtures import FIXTURES

ECB_FILES = sorted((FIXTURES / "ecb" / "live").glob("decision_*.html"))
BOE_FILES = sorted((FIXTURES / "boe" / "live").glob("summary_*.html"))
SNB_FILES = sorted((FIXTURES / "snb" / "live").glob("assessment_*.pdf"))
SPEECH_FILES = sorted((FIXTURES / "fed" / "live").glob("speech_*.htm"))
MINUTES_FILES = sorted((FIXTURES / "fed" / "live").glob("minutes_*.htm"))
FED = "https://www.federalreserve.gov"


def _day(path, prefix: str) -> date:
    stamp = path.stem.removeprefix(prefix)
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]) if len(stamp) > 6 else 1)


@pytest.mark.skipif(not ECB_FILES, reason="no captured ECB decisions")
@pytest.mark.parametrize("path", ECB_FILES, ids=lambda p: p.name)
def test_live_ecb_decision_parses(path) -> None:
    day = _day(path, "decision_")
    url = f"{ecb.BASE_URL}/press/pr/date/{day:%Y}/html/ecb.mp{day:%y%m%d}~0.en.html"

    parsed = ecb.parse_decision_html(path.read_text(encoding="utf-8"), url)

    text = " ".join(normalize_paragraphs(parsed.paragraphs)).lower()
    assert parsed.published_at.date() == day
    assert len(parsed.paragraphs) >= 3
    assert "governing council" in text and "interest rate" in text
    assert "will comment on the considerations" not in text


@pytest.mark.skipif(not BOE_FILES, reason="no captured BoE summaries")
@pytest.mark.parametrize("path", BOE_FILES, ids=lambda p: p.name)
def test_live_boe_summary_parses(path) -> None:
    month = _day(path, "summary_")
    url = (
        f"{boe.BASE_URL}/monetary-policy-summary-and-minutes/"
        f"{month:%Y}/{month:%B}-{month:%Y}".lower().replace(boe.BASE_URL.lower(), boe.BASE_URL)
    )

    parsed = boe.parse_summary_html(path.read_text(encoding="utf-8"), url)

    text = " ".join(normalize_paragraphs(parsed.paragraphs)).lower()
    assert (parsed.published_at.year, parsed.published_at.month) == (month.year, month.month)
    assert len(parsed.paragraphs) >= 3
    assert "bank rate" in text
    assert "minutes of the monetary policy committee" not in text


@pytest.mark.skipif(not SNB_FILES, reason="no captured SNB assessments")
@pytest.mark.parametrize("path", SNB_FILES, ids=lambda p: p.name)
def test_live_snb_assessment_parses(path) -> None:
    day = _day(path, "assessment_")
    url = f"{snb.BASE_URL}/en/publications/communication/press-releases-restricted/pre_{day:%Y%m%d}"

    parsed = snb.parse_assessment_pdf(path.read_bytes(), url)

    text = " ".join(normalize_paragraphs(parsed.paragraphs)).lower()
    assert parsed.published_at.date() == day
    assert len(parsed.paragraphs) >= 4
    assert "inflation" in text and "snb" in text
    for boilerplate in ("page 1/", "p.o. box", "more detailed information"):
        assert boilerplate not in text


@pytest.mark.skipif(not SPEECH_FILES, reason="no captured Fed speeches")
@pytest.mark.parametrize("path", SPEECH_FILES, ids=lambda p: p.name)
def test_live_fed_speech_parses(path) -> None:
    url = f"{FED}/newsevents/speech/{path.name.removeprefix('speech_')}"

    parsed = fed_speeches.parse_speech_html(path.read_text(encoding="utf-8"), url)

    assert parsed.speaker and not parsed.speaker.lower().startswith(("governor", "chair"))
    assert parsed.published_at.date() >= fed_speeches.speech_date_from_url(url)
    assert len(parsed.paragraphs) >= 3
    assert not any("Return to text" in p for p in parsed.paragraphs)


@pytest.mark.skipif(not MINUTES_FILES, reason="no captured FOMC minutes")
@pytest.mark.parametrize("path", MINUTES_FILES, ids=lambda p: p.name)
def test_live_fomc_minutes_parse(path) -> None:
    url = f"{FED}/monetarypolicy/fomcminutes{path.stem.removeprefix('minutes_')}.htm"

    parsed = fomc_minutes.parse_minutes_html(path.read_text(encoding="utf-8"), url)

    text = " ".join(parsed.paragraphs).lower()
    assert len(parsed.paragraphs) >= 20
    assert "participants" in text and "federal funds rate" in text
    assert not text.startswith("attendance") and "return to text" not in text


@pytest.mark.skipif(not BOE_FILES, reason="no captured BoE pages")
@pytest.mark.parametrize("path", BOE_FILES, ids=lambda p: p.name)
def test_live_boe_minutes_parse(path) -> None:
    month = _day(path, "summary_")
    url = f"{boe.BASE_URL}/monetary-policy-summary-and-minutes/{month:%Y}/{month:%B}-{month:%Y}"

    parsed = boe.parse_minutes_html(path.read_text(encoding="utf-8"), url.lower())

    text = " ".join(parsed.paragraphs).lower()
    assert len(parsed.paragraphs) >= 20
    assert "were present" not in text
