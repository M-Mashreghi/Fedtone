"""Fed speeches, FOMC minutes and BoE minutes adapters (Task 7.3)."""

from datetime import UTC, date, datetime

import pytest

from fedtone.application.sources import PermanentSourceError
from fedtone.domain.linking import previous_comparable_links
from fedtone.domain.models import PolicyDocument
from fedtone.infrastructure.sources import boe, fed_speeches, fomc_minutes
from tests.bank_fixtures import BOE_AUGUST, FIXTURES

FED = "https://www.federalreserve.gov"
SPEECH = f"{FED}/newsevents/speech/barr20240305a.htm"
MINUTES = f"{FED}/monetarypolicy/fomcminutes20240131.htm"


def read(name: str) -> str:
    return (FIXTURES / "fed" / "synthetic" / name).read_text(encoding="utf-8")


# -- speeches --------------------------------------------------------------------------------


def test_speech_has_speaker_text_and_conservative_time() -> None:
    parsed = fed_speeches.parse_speech_html(read("speech_barr20240305a.htm"), SPEECH)

    assert parsed.speaker == "Synthetic A. Barr"  # office stripped
    assert parsed.title == "The Synthetic Outlook"
    # End of the speech day in New York (23:59 EST = 04:59 UTC next day): never earlier.
    assert parsed.published_at == datetime(2024, 3, 6, 4, 59, tzinfo=UTC)
    assert parsed.paragraphs[1].startswith("Inflation has come down")
    joined = " ".join(parsed.paragraphs)
    assert "views expressed here" not in joined and "Return to text" not in joined
    assert "Washington" not in joined  # heading block (location) excluded


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Governor Michelle W. Bowman", "Michelle W. Bowman"),
        ("Vice Chair for Supervision Michelle W. Bowman", "Michelle W. Bowman"),
        ("Chair  Jerome H. Powell", "Jerome H. Powell"),
        ("President John C. Williams", "John C. Williams"),
        ("Lisa D. Cook", "Lisa D. Cook"),
    ],
)
def test_speaker_names_are_canonical_across_roles(text: str, name: str) -> None:
    assert fed_speeches.speaker_name(text) == name


def test_speech_discovery_reads_cdata_rss_links() -> None:
    links = fed_speeches.discover_speech_links(
        read("speeches_feed.xml"), f"{FED}/feeds/speeches.xml"
    )

    assert links == [
        f"{FED}/newsevents/speech/barr20240610a.htm",
        f"{FED}/newsevents/speech/cook20240401a.htm",
        SPEECH,
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h.replace('class="speaker"', 'class="byline"'), "not a speech"),
        (lambda h: h.replace("March 05, 2024", "March 06, 2024"), "URL date"),
        (lambda h: h.replace('id="article"', 'id="main"'), "#article"),
    ],
)
def test_speech_layout_drift_is_permanent(mutate, message: str) -> None:
    with pytest.raises(PermanentSourceError, match=message):
        fed_speeches.parse_speech_html(mutate(read("speech_barr20240305a.htm")), SPEECH)


def test_speeches_link_to_the_same_speakers_previous_speech() -> None:
    def doc(url: str, speaker: str, day: datetime) -> PolicyDocument:
        return PolicyDocument("FED", "speech", url, day, published_at=day, speaker=speaker)

    barr1 = doc("a", "Barr", datetime(2024, 3, 5, tzinfo=UTC))
    cook = doc("b", "Cook", datetime(2024, 4, 1, tzinfo=UTC))
    barr2 = doc("c", "Barr", datetime(2024, 6, 10, tzinfo=UTC))

    links = previous_comparable_links([barr1, cook, barr2])

    assert links == {barr1.id: None, cook.id: None, barr2.id: barr1.id}


# -- FOMC minutes ----------------------------------------------------------------------------


def test_minutes_keep_the_narrative_and_follow_the_release_schedule() -> None:
    parsed = fomc_minutes.parse_minutes_html(read("minutes_20240131.htm"), MINUTES)

    assert parsed.title.startswith("Minutes of the Federal Open Market Committee")
    # Three weeks after the 31 January decision, 2:00 p.m. EST = 19:00 UTC.
    assert parsed.published_at == datetime(2024, 2, 21, 19, 0, tzinfo=UTC)
    joined = " ".join(parsed.paragraphs)
    assert "Many participants noted" in joined
    for excluded in ("Notation Vote", "Attendance", "Synthetic Member", "Return to text"):
        assert excluded not in joined


def test_minutes_discovery_skips_minutes_not_yet_released() -> None:
    import httpx

    from fedtone.infrastructure.sources.http import HttpFetcher
    from tests.fomc_fixtures import fixture_transport

    fetcher = HttpFetcher(httpx.Client(transport=fixture_transport()), max_retries=0)
    before = fomc_minutes.FomcMinutesAdapter(
        fetcher, clock=lambda: datetime(2024, 2, 20, tzinfo=UTC)
    )
    after = fomc_minutes.FomcMinutesAdapter(
        fetcher, clock=lambda: datetime(2024, 2, 22, tzinfo=UTC)
    )

    assert before.discover() == []  # no look-ahead: minutes are three weeks behind
    assert [d.source_url for d in after.discover()] == [MINUTES]
    assert fomc_minutes.meeting_date_from_url(MINUTES) == date(2024, 1, 31)


def test_page_without_minutes_heading_is_permanent() -> None:
    with pytest.raises(PermanentSourceError, match="not FOMC minutes"):
        fomc_minutes.parse_minutes_html(
            read("minutes_20240131.htm").replace("Minutes of the", "Notes of the"), MINUTES
        )


# -- BoE minutes -----------------------------------------------------------------------------


def test_boe_minutes_are_a_separate_section_of_the_same_page() -> None:
    html = (FIXTURES / "boe" / "synthetic" / "summary_2024_august.html").read_text("utf-8")

    minutes = boe.parse_minutes_html(html, BOE_AUGUST)
    summary = boe.parse_summary_html(html, BOE_AUGUST)

    assert minutes.title.startswith("Minutes of the Monetary Policy Committee")
    assert minutes.paragraphs[0].startswith("1: Before turning")
    assert not set(minutes.paragraphs) & set(summary.paragraphs)
    assert minutes.parser_version == "boe-mpc-minutes-html-1"


def test_boe_minutes_stop_at_the_attendance_list() -> None:
    html = (FIXTURES / "boe" / "synthetic" / "summary_2024_august.html").read_text("utf-8")
    html = html.replace(
        "<p>2: Global activity",
        "<p>3: The following members of the Committee were present:</p><p>2: Global activity",
    )

    minutes = boe.parse_minutes_html(html, BOE_AUGUST)

    assert [p[:2] for p in minutes.paragraphs] == ["1:"]
