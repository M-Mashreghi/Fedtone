"""Streamlit smoke tests: the dashboard renders a selected meeting from the API."""

from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from fedtone.ui.client import FedToneApiClient

DASHBOARD = Path(__file__).resolve().parents[2] / "src" / "fedtone" / "ui" / "dashboard.py"


def run_app(api_client) -> AppTest:
    app = AppTest.from_file(str(DASHBOARD), default_timeout=30)
    app.session_state["fedtone_api_client"] = FedToneApiClient(api_client)
    return app.run()


def test_dashboard_shows_meeting_with_score_evidence_and_audit(client, harness) -> None:
    app = run_app(client)

    assert not app.exception
    assert app.title[0].value.startswith("FedTone")
    meeting = app.selectbox(key="meeting")
    # Latest meeting is selected by default.
    assert meeting.value == str(harness.doc("mar").id)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Net tone"] == "-0.10"
    assert metrics["Confidence"] == "0.80"
    captions = " ".join(c.value for c in app.caption)
    assert "manual://mar" in captions and "tone-statement-v2+fed-fomc-statement-v1" in captions
    assert [t.label for t in app.tabs] == [
        "Overview",
        "Statement diff",
        "Market reaction",
        "Analogues",
        "Audit & export",
    ]
    # Analogues always show the point-in-time warning.
    assert any("Point-in-time" in i.value for i in app.info)


def test_selecting_another_meeting_updates_detail(client, harness) -> None:
    app = run_app(client)

    app.selectbox(key="meeting").set_value(str(harness.doc("dec").id)).run()

    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Net tone"] == "+0.50"
    assert metrics["Change vs previous"] == "baseline"
    assert any("no previous statement" in i.value for i in app.info)


def test_dashboard_reports_unreachable_api() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(base_url="http://api.invalid", transport=httpx.MockTransport(refuse))
    app = run_app(http)

    assert not app.exception
    assert "Cannot load data" in app.error[0].value


@pytest.mark.parametrize("series", ["inflation", "balance_sheet"])
def test_dimension_selector_renders(client, series) -> None:
    app = run_app(client)

    app.selectbox(key="dimension").set_value(series).run()

    assert not app.exception
