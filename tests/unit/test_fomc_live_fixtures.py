"""Parser checks against captured official pages (skipped when none are present)."""

from datetime import date

import pytest

from fedtone.domain.text import normalize_paragraphs
from fedtone.infrastructure.sources.fomc import (
    discover_statement_links,
    parse_statement_html,
    statement_date_from_url,
)
from tests.fomc_fixtures import BASE, LIVE

STATEMENTS = sorted(LIVE.glob("statement_*.html"))
CALENDAR = LIVE / "calendar.html"


@pytest.mark.skipif(not STATEMENTS, reason="no captured statements in tests/fixtures/fomc/live")
@pytest.mark.parametrize("path", STATEMENTS, ids=lambda p: p.name)
def test_live_statement_parses(path) -> None:
    day = path.stem.removeprefix("statement_")
    url = f"{BASE}/newsevents/pressreleases/monetary{day}a.htm"

    parsed = parse_statement_html(path.read_text(encoding="utf-8"), url)
    paragraphs = normalize_paragraphs(parsed.paragraphs)
    text = " ".join(paragraphs).lower()

    assert "fomc statement" in parsed.title.lower()
    assert parsed.published_at is not None
    assert parsed.published_at.date() == statement_date_from_url(url)
    assert len(paragraphs) >= 3
    assert "federal funds rate" in text
    for boilerplate in ("implementation note issued", "for media inquiries", "last update"):
        assert boilerplate not in text


@pytest.mark.skipif(not CALENDAR.exists(), reason="no captured calendar page")
def test_live_calendar_discovers_statements() -> None:
    links = discover_statement_links(
        CALENDAR.read_text(encoding="utf-8"), f"{BASE}/monetarypolicy/fomccalendars.htm"
    )

    assert len(links) >= 8
    assert all(statement_date_from_url(link) > date(2000, 1, 1) for link in links)
