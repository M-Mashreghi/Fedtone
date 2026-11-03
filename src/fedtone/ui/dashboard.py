"""FedTone research dashboard (Streamlit).

    streamlit run src/fedtone/ui/dashboard.py

Reads everything from the research API (``FEDTONE_UI__API_BASE_URL``); it holds no business
logic. Layout is meeting-centric: pick a meeting on the timeline, then read its score next
to the evidence, the language changes, the market reaction and the audit trail.
"""

from datetime import UTC, date, datetime, time
from typing import Any

import streamlit as st

from fedtone.domain.lifecycle import DocumentStatus
from fedtone.settings import get_settings
from fedtone.ui import views
from fedtone.ui.client import ApiError, FedToneApiClient

CLIENT_KEY = "fedtone_api_client"
DIMENSION_CHOICES = ["overall", "inflation", "labor", "growth", "policy_guidance", "balance_sheet"]


def get_client() -> FedToneApiClient:
    if CLIENT_KEY not in st.session_state:
        ui = get_settings().ui
        st.session_state[CLIENT_KEY] = FedToneApiClient.connect(ui.api_base_url, ui.timeout_seconds)
    return st.session_state[CLIENT_KEY]


def _filters_sidebar(points: list[dict[str, Any]]) -> dict[str, Any]:
    st.sidebar.header("Filters")
    banks = sorted({p["central_bank"] for p in points}) or ["FED"]
    types = sorted({p["document_type"] for p in points}) or ["fomc_statement"]
    models = sorted({p["model"] for p in points if p["model"]})
    filters: dict[str, Any] = {
        "central_bank": st.sidebar.selectbox("Central bank", ["(all)", *banks]),
        "document_type": st.sidebar.selectbox("Document type", ["(all)", *types]),
        "model": st.sidebar.selectbox("Model version", ["(all)", *models]),
        "status": st.sidebar.selectbox("Status", ["(all)", *(s.value for s in DocumentStatus)]),
    }
    dates = [p["published_at"][:10] for p in points if p["published_at"]]
    if dates:
        first, last = date.fromisoformat(min(dates)), date.fromisoformat(max(dates))
        chosen = st.sidebar.date_input(
            "Release dates", value=(first, last), min_value=first, max_value=last
        )
        if isinstance(chosen, tuple) and len(chosen) == 2:
            filters["published_from"] = datetime.combine(chosen[0], time.min, UTC).isoformat()
            filters["published_to"] = datetime.combine(chosen[1], time.max, UTC).isoformat()
    return {k: v for k, v in filters.items() if v != "(all)"}


def _header(detail: dict[str, Any]) -> None:
    signal, audit = detail["signal"], detail["audit"]
    st.subheader(
        f"{signal['central_bank']} - {detail['title'] or signal['document_type']} - "
        f"{signal['published_at'][:10]}"
    )
    st.caption(
        f"Source: [{audit['source_url']}]({audit['source_url']}) - "
        f"hash `{(audit['content_hash'] or 'n/a')[:12]}` - "
        f"model `{audit['model'] or 'n/a'}` / prompt `{audit['prompt_version'] or 'n/a'}` / "
        f"schema `{audit['schema_version'] or 'n/a'}`"
    )
    if signal["needs_review"]:
        reasons = "; ".join(r["reason"] for r in detail["open_reviews"]) or "status NeedsReview"
        st.warning(f"Needs review: {reasons}")


def _overview(detail: dict[str, Any]) -> None:
    analysis, comparison = detail["analysis"], detail["comparison"]
    if analysis is None:
        st.info("No validated tone analysis is stored for this meeting.")
        return
    cols = st.columns(4)
    cols[0].metric("Net tone", f"{analysis['net_tone_score']:+.2f}")
    cols[1].metric("Label", analysis["classification"])
    cols[2].metric("Confidence", f"{analysis['confidence']:.2f}")
    if comparison is None or comparison["is_baseline"]:
        cols[3].metric("Change vs previous", "baseline")
    else:
        previous = comparison["previous_net_tone_score"]
        delta = comparison["overall_delta"]
        cols[3].metric(
            "Previous score",
            "n/a" if previous is None else f"{previous:+.2f}",
            None if delta is None else f"{delta:+.2f}",
            delta_color="off",  # hawkish/dovish is not good/bad
        )
    if analysis["uncertainty_flags"]:
        st.warning("Uncertainty: " + ", ".join(analysis["uncertainty_flags"]))
    if detail["summary"]:
        st.markdown(f"**{detail['summary']['headline']}**  \n{detail['summary']['summary']}")
    left, right = st.columns(2)
    with left:
        st.markdown("**Dimension scores**")
        st.plotly_chart(views.dimension_figure(analysis))
        st.dataframe(views.dimension_rows(analysis, comparison), hide_index=True)
    with right:
        st.markdown("**Evidence**")
        st.dataframe(views.evidence_rows(analysis), hide_index=True)
        st.markdown(analysis["explanation"])


def _diff(detail: dict[str, Any]) -> None:
    comparison = detail["comparison"]
    if comparison is None:
        st.info("No comparison stored yet.")
        return
    if comparison["is_baseline"]:
        st.info("First statement in the series: there is no previous statement to compare.")
        return
    sections = views.diff_sections(comparison)
    for title, key in (("Added", "added"), ("Removed", "removed"), ("Changed", "changed")):
        st.markdown(f"**{title} language** ({len(sections[key])})")
        for sentence in sections[key]:
            st.markdown(f"- {sentence}")
    interpretation = comparison.get("interpretation")
    if interpretation:
        st.markdown(f"**Model interpretation:** {interpretation.get('summary', '')}")


def _market(detail: dict[str, Any]) -> None:
    reaction = detail["market_reaction"]
    if not reaction["metrics"]:
        st.info("No market reaction stored yet (windows may not have closed).")
        return
    if reaction["backtest_mode"]:
        st.warning(views.BACKTEST_NOTE)
    if reaction["missing"]:
        st.warning(f"{reaction['missing']} metric(s) missing - see the 'missing' column.")
    st.caption("Observed moves around the release: association, not causation.")
    for kind, title in (("yield_change", "Yields (bps)"), ("simple_return", "Returns")):
        figure = views.reaction_figure(reaction["metrics"], kind)
        if figure is not None:
            st.markdown(f"**{title}**")
            st.plotly_chart(figure)
    st.dataframe(views.reaction_rows(reaction["metrics"]), hide_index=True)


def _analogues(client: FedToneApiClient, document_id: str) -> None:
    st.info(views.POINT_IN_TIME_NOTE)
    try:
        analogues = client.analogues(document_id)
    except ApiError as exc:
        st.warning(f"Analogues unavailable: {exc}")
        return
    st.caption(
        f"Cutoff {analogues['cutoff'][:16]}Z - {analogues['sample_size']} earlier meeting(s)"
    )
    if analogues["insufficient_history"]:
        st.warning(
            f"Fewer than {analogues['min_sample']} earlier meetings: treat the ranking as "
            "anecdotal."
        )
    if analogues["withheld_after_cutoff"]:
        st.caption(f"{analogues['withheld_after_cutoff']} metric(s) withheld (after cutoff).")
    st.dataframe(views.analogue_rows(analogues), hide_index=True)


def _audit_and_export(client: FedToneApiClient, detail: dict[str, Any], filters) -> None:
    st.dataframe(views.audit_rows(detail["audit"]), hide_index=True)
    with st.expander("Status history"):
        st.dataframe(detail["audit"]["history"], hide_index=True)
    document_id = detail["signal"]["document_id"]
    cols = st.columns(3)
    cols[0].download_button(
        "Research note (Markdown)",
        client.research_note(document_id),
        file_name=f"fedtone-note-{document_id}.md",
        mime="text/markdown",
    )
    cols[1].download_button(
        "Signals (CSV)",
        client.export_signals("csv", **filters),
        file_name="fedtone-signals.csv",
        mime="text/csv",
    )
    cols[2].download_button(
        "Signals (JSON)",
        client.export_signals("json", **filters),
        file_name="fedtone-signals.json",
        mime="application/json",
    )


def main() -> None:
    st.set_page_config(page_title="FedTone", layout="wide")
    st.title("FedTone - policy tone research")
    client = get_client()
    try:
        everything = client.timeline()["points"]
        filters = _filters_sidebar(everything)
        points = client.timeline(**filters)["points"]
    except ApiError as exc:
        st.error(f"Cannot load data from the FedTone API: {exc}")
        return

    st.markdown("### Policy tone timeline")
    dimension = st.selectbox("Series", DIMENSION_CHOICES, key="dimension")
    if not points:
        st.info("No meetings match the filters.")
        return
    st.plotly_chart(views.timeline_figure(points, dimension))
    st.caption("Red X: needs review. Grey circle: no score for this series.")
    note = views.cross_profile_note(points)
    if note:
        st.warning(note)

    labels = {p["document_id"]: views.meeting_label(p) for p in points}
    ids = list(labels)
    document_id = st.selectbox(
        "Meeting",
        ids,
        index=len(ids) - 1,
        format_func=labels.__getitem__,
        key="meeting",
    )
    try:
        detail = client.document(document_id)
    except ApiError as exc:
        st.error(f"Cannot load meeting: {exc}")
        return

    _header(detail)
    tabs = st.tabs(["Overview", "Statement diff", "Market reaction", "Analogues", "Audit & export"])
    with tabs[0]:
        _overview(detail)
    with tabs[1]:
        _diff(detail)
    with tabs[2]:
        _market(detail)
    with tabs[3]:
        _analogues(client, document_id)
    with tabs[4]:
        _audit_and_export(client, detail, filters)


main()
