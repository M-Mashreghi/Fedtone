"""Dashboard view helpers show API values unchanged."""

from fedtone.ui import views
from fedtone.ui.client import ApiError, FedToneApiClient


def point(**overrides):
    base = {
        "document_id": "d1",
        "published_at": "2024-01-31T19:00:00Z",
        "status": "Compared",
        "needs_review": False,
        "net_tone_score": 0.2,
        "confidence": 0.8,
        "dim_inflation": 0.3,
        "central_bank": "FED",
        "document_type": "fomc_statement",
        "model": "m",
    }
    return base | overrides


def test_timeline_chart_values_equal_api_values(client) -> None:
    points = client.get("/api/v1/timeline").json()["points"]

    for dimension in ("overall", "inflation"):
        figure = views.timeline_figure(points, dimension)
        main = figure.data[0]
        key = views.series_key(dimension)
        assert list(main.y) == [p[key] for p in points]
        assert list(main.customdata) == [p["document_id"] for p in points]


def test_review_and_missing_meetings_are_distinguished() -> None:
    points = [
        point(document_id="a"),
        point(document_id="b", published_at="2024-03-20T18:00:00Z", needs_review=True),
        point(document_id="c", published_at="2024-05-01T18:00:00Z", net_tone_score=None),
    ]

    figure = views.timeline_figure(points)

    by_name = {trace.name: trace for trace in figure.data}
    assert list(by_name["needs review"].customdata) == ["b"]
    assert list(by_name["no score"].customdata) == ["c"]
    # A missing score is a gap in the line, not an imputed value.
    assert list(by_name["score"].y) == [0.2, 0.2, None]
    assert "needs review" in views.meeting_label(points[1])
    assert "no score" in views.meeting_label(points[2])


def test_reaction_rows_and_chart_use_stored_values_and_display(client, harness) -> None:
    jan = harness.doc("jan")
    metrics = client.get(f"/api/v1/documents/{jan.id}/market-reaction").json()["metrics"]

    rows = views.reaction_rows(metrics)
    yields = views.reaction_figure(metrics, "yield_change")
    returns = views.reaction_figure(metrics, "simple_return")

    assert [r["value"] for r in rows] == [m["value"] for m in metrics]
    assert [r["reaction"] for r in rows] == [m["display"] for m in metrics]
    assert {r["unit"] for r in rows if r["instrument"] == "DGS10"} == {"basis points"}
    dgs10 = [m for m in metrics if m["kind"] == "yield_change"]
    assert list(yields.data[0].y) == [m["value"] for m in dgs10]
    assert list(yields.data[0].text) == [m["display"] for m in dgs10]
    assert yields.layout.yaxis.title.text == "basis points"
    assert all(t.endswith("%") for t in returns.data[0].text)
    assert views.reaction_figure([], "yield_change") is None


def test_detail_helpers_cover_evidence_diff_dimensions_and_audit(client, harness) -> None:
    detail = client.get(f"/api/v1/documents/{harness.doc('jan').id}").json()

    evidence = views.evidence_rows(detail["analysis"])
    sections = views.diff_sections(detail["comparison"])
    dimensions = views.dimension_rows(detail["analysis"], detail["comparison"])
    audit = {r["field"]: r["value"] for r in views.audit_rows(detail["audit"])}

    assert [e["quote"] for e in evidence] == [e["text"] for e in detail["analysis"]["evidence"]]
    assert sections["added"] == detail["comparison"]["diff"]["added"]
    assert sections["removed"] == detail["comparison"]["diff"]["removed"]
    inflation = next(d for d in dimensions if d["dimension"] == "inflation")
    assert inflation["score"] == detail["analysis"]["dimension_scores"]["inflation"]
    assert inflation["change vs previous"] == detail["comparison"]["dimension_deltas"]["inflation"]
    for key in ("source_url", "content_hash", "model", "prompt_version", "schema_version"):
        assert audit[key] == detail["audit"][key]
    assert views.diff_sections(None) == {"added": [], "removed": [], "changed": []}


def test_analogue_rows_show_canonical_displays(client, harness) -> None:
    analogues = client.get(f"/api/v1/documents/{harness.doc('mar').id}/analogues").json()

    rows = views.analogue_rows(analogues)

    assert [r["rank"] for r in rows] == [1, 2]
    first = analogues["matches"][0]["reactions"]
    spx = next(
        m
        for m in first
        if (m["symbol"], m["window"], m["kind"]) == ("SP500", "[0,+1]", "simple_return")
    )
    assert rows[0]["SP500 [0,+1]"] == spx["display"]


def test_client_reports_api_errors(client) -> None:
    api = FedToneApiClient(client)

    assert api.timeline()["points"]
    try:
        api.document("00000000-0000-0000-0000-000000000000")
    except ApiError as exc:
        assert str(exc).startswith("404")
    else:
        raise AssertionError("expected ApiError")
