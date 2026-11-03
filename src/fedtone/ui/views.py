"""Presentation helpers for the dashboard: API payloads in, tables and figures out.

These functions only select, label and arrange values the API returned. They never compute
scores or market metrics; metric values are shown with the API's canonical ``display`` text.
"""

from typing import Any

import plotly.graph_objects as go

OVERALL = "net_tone_score"
MISSING_COLOR = "#9e9e9e"
REVIEW_COLOR = "#d62728"
SCORE_COLOR = "#1f77b4"

POINT_IN_TIME_NOTE = (
    "Point-in-time view: analogues use only meetings released before the cutoff, and their "
    "market reactions only if the window had closed by then. Tone scores of past meetings "
    "come from today's model run over text that was public at the time."
)
BACKTEST_NOTE = (
    "Backtest mode: these metrics were computed with an earlier as-of cutoff, so windows that "
    "had not closed by then are reported as missing."
)


CROSS_PROFILE_NOTE = (
    "This view mixes {count} tone profiles ({names}). Every bank is scored on the same -1..+1 "
    "scale, but levels are calibrated within each bank and document type: compare direction "
    "and changes across banks, not score levels. Filter by central bank for one series."
)


def cross_profile_note(points: list[dict[str, Any]]) -> str | None:
    """Warning text when the points come from more than one bank/document-type profile."""
    profiles = sorted(
        {
            p.get("tone_profile") or f"{p['central_bank']}/{p['document_type']}"
            for p in points
            if p.get("net_tone_score") is not None
        }
    )
    if len(profiles) < 2:
        return None
    return CROSS_PROFILE_NOTE.format(count=len(profiles), names=", ".join(profiles))


def series_key(dimension: str) -> str:
    return OVERALL if dimension == "overall" else f"dim_{dimension}"


def meeting_label(point: dict[str, Any]) -> str:
    date = (point.get("published_at") or "unknown date")[:10]
    flag = " - needs review" if point.get("needs_review") else ""
    score = point.get(OVERALL)
    tone = "no score" if score is None else f"{score:+.2f}"
    return f"{date} ({tone}, {point['status']}{flag})"


def point_state(point: dict[str, Any], key: str) -> str:
    if point.get("needs_review"):
        return "needs_review"
    if point.get(key) is None:
        return "missing"
    return "ok"


def timeline_figure(points: list[dict[str, Any]], dimension: str = "overall") -> go.Figure:
    """Score line plus markers; meetings in review or without a score are distinguished.

    Missing scores are not drawn as values: they appear as grey markers on the zero line
    with "no score" in the hover text, and the line has a gap there.
    """
    key = series_key(dimension)
    dated = [p for p in points if p.get("published_at")]
    # One line per bank/document type: scores of different profiles are never joined.
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in dated:
        groups.setdefault(f"{p['central_bank']} {p['document_type']}", []).append(p)
    figure = go.Figure()
    for index, (name, group) in enumerate(groups.items()):
        figure.add_trace(
            go.Scatter(
                x=[p["published_at"] for p in group],
                y=[p.get(key) for p in group],
                mode="lines+markers",
                name="score" if len(groups) == 1 else name,
                customdata=[p["document_id"] for p in group],
                text=[
                    f"confidence {p['confidence']:.2f}" if p.get("confidence") is not None else ""
                    for p in group
                ],
                hovertemplate="%{x|%Y-%m-%d}<br>score %{y:+.2f}<br>%{text}<extra></extra>",
                line={"color": SCORE_COLOR} if index == 0 else {},
                connectgaps=False,
            )
        )
    for state, color, symbol, label in (
        ("needs_review", REVIEW_COLOR, "x", "needs review"),
        ("missing", MISSING_COLOR, "circle-open", "no score"),
    ):
        flagged = [p for p in dated if point_state(p, key) == state]
        if flagged:
            figure.add_trace(
                go.Scatter(
                    x=[p["published_at"] for p in flagged],
                    y=[p.get(key) if p.get(key) is not None else 0.0 for p in flagged],
                    mode="markers",
                    name=label,
                    customdata=[p["document_id"] for p in flagged],
                    marker={"color": color, "symbol": symbol, "size": 12},
                    hovertemplate=f"%{{x|%Y-%m-%d}}<br>{label}<extra></extra>",
                )
            )
    figure.update_layout(
        yaxis={"range": [-1.05, 1.05], "title": "dovish (-1) to hawkish (+1)", "zeroline": True},
        xaxis={"title": "release"},
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        legend={"orientation": "h"},
        height=360,
    )
    return figure


def dimension_rows(
    analysis: dict[str, Any], comparison: dict[str, Any] | None
) -> list[dict[str, Any]]:
    deltas = (comparison or {}).get("dimension_deltas", {})
    rows = []
    for name, score in sorted(analysis["dimension_scores"].items()):
        rows.append(
            {
                "dimension": name,
                "score": score,
                "addressed": score is not None,
                "change vs previous": deltas.get(name),
            }
        )
    return rows


def dimension_figure(analysis: dict[str, Any]) -> go.Figure:
    names = sorted(analysis["dimension_scores"])
    scores = [analysis["dimension_scores"][n] for n in names]
    figure = go.Figure(
        go.Bar(
            x=[s if s is not None else 0.0 for s in scores],
            y=names,
            orientation="h",
            marker={"color": [SCORE_COLOR if s is not None else MISSING_COLOR for s in scores]},
            text=["not addressed" if s is None else f"{s:+.2f}" for s in scores],
            textposition="auto",
        )
    )
    figure.update_layout(
        xaxis={"range": [-1.05, 1.05], "title": "score"},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=260,
    )
    return figure


def evidence_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "quote": e["text"],
            "dimension": e["dimension"],
            "direction": e["direction"],
            "match": e["match"],
        }
        for e in analysis["evidence"]
    ]


def diff_sections(comparison: dict[str, Any] | None) -> dict[str, list[str]]:
    diff = (comparison or {}).get("diff") or {}
    return {
        "added": list(diff.get("added", [])),
        "removed": list(diff.get("removed", [])),
        "changed": [f"{c['previous']}  ->  {c['current']}" for c in diff.get("changed", [])],
    }


_UNIT_LABEL = {
    "bps": "basis points",
    "decimal_return": "return (%)",
    "log_return": "log return",
}


def reaction_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per stored metric; ``value`` is the canonical stored number."""
    return [
        {
            "instrument": m["symbol"],
            "measure": f"{m['kind']} vs {m['benchmark_symbol']}"
            if m["benchmark_symbol"]
            else m["kind"],
            "window": m["window"],
            "reaction": m["display"],
            "unit": _UNIT_LABEL.get(m["unit"], m["unit"]),
            "value": m["value"],
            "from": m["start_date"],
            "to": m["end_date"],
            "missing": m["missing_reason"] or "",
        }
        for m in metrics
    ]


def reaction_figure(metrics: list[dict[str, Any]], kind: str) -> go.Figure | None:
    """Bars of stored values for one metric kind (yields in bps, returns as fractions)."""
    chosen = [m for m in metrics if m["kind"] == kind and m["value"] is not None]
    if not chosen:
        return None
    figure = go.Figure()
    for symbol in sorted({m["symbol"] for m in chosen}):
        rows = [m for m in chosen if m["symbol"] == symbol]
        figure.add_trace(
            go.Bar(
                name=symbol,
                x=[m["window"] for m in rows],
                y=[m["value"] for m in rows],
                text=[m["display"] for m in rows],
                textposition="auto",
            )
        )
    unit = _UNIT_LABEL.get(chosen[0]["unit"], chosen[0]["unit"])
    axis = {"title": unit}
    if chosen[0]["unit"] == "decimal_return":
        axis["tickformat"] = ".2%"  # axis labels only; bar text is the canonical display
    figure.update_layout(
        yaxis=axis,
        barmode="group",
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=300,
    )
    return figure


def audit_rows(audit: dict[str, Any]) -> list[dict[str, str]]:
    keys = (
        "source_url",
        "published_at",
        "fetched_at",
        "content_hash",
        "raw_hash",
        "snapshot_version",
        "parser_version",
        "normalizer_version",
        "analysis_id",
        "model",
        "prompt_version",
        "schema_version",
        "temperature",
        "analyzed_at",
        "comparison_model",
        "comparison_prompt_version",
        "comparison_schema_version",
        "metric_version",
        "summary_model",
        "summary_prompt_version",
        "status",
        "failure_reason",
    )
    return [{"field": k, "value": "" if audit.get(k) is None else str(audit[k])} for k in keys]


def analogue_rows(analogues: dict[str, Any], window: str = "[0,+1]") -> list[dict[str, Any]]:
    rows = []
    for rank, match in enumerate(analogues["matches"], start=1):
        row: dict[str, Any] = {
            "rank": rank,
            "release": match["published_at"][:10],
            "distance": match["distance"],
        }
        for metric in match["reactions"]:
            if metric["window"] == window and metric["kind"] in ("simple_return", "yield_change"):
                row[f"{metric['symbol']} {window}"] = metric["display"]
        rows.append(row)
    return rows
