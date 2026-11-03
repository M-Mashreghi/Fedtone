"""Signal exports (CSV/JSON) and the Markdown research note.

Exports serialize the records returned by ``ResearchQueryService`` unchanged; numbers are
written with ``repr`` precision, never rounded, so an export reproduces the API values.
"""

import csv
import io
import json
from collections.abc import Sequence

from fedtone.application.research_queries import (
    DocumentDetail,
    is_backtest,
    metric_record,
    signal_record,
)
from fedtone.application.research_summary import metric_display
from fedtone.domain.models import ToneAnalysis


def signals_to_json(records: Sequence[dict[str, object]]) -> str:
    return json.dumps(list(records), indent=2, ensure_ascii=False)


def signals_to_csv(records: Sequence[dict[str, object]]) -> str:
    buffer = io.StringIO()
    if not records:
        return ""
    writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow({k: "" if v is None else v for k, v in record.items()})
    return buffer.getvalue()


def _score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def research_note(detail: DocumentDetail, previous: ToneAnalysis | None = None) -> str:
    """Markdown note built only from stored records and their canonical display strings."""
    document, analysis, comparison = detail.document, detail.analysis, detail.comparison
    released = f"{document.published_at:%Y-%m-%d}" if document.published_at else "unknown date"
    title = detail.text.title if detail.text else document.document_type
    lines = [f"# {document.central_bank} {title} ({released})", ""]
    if detail.row.needs_review:
        lines += ["> **Needs review** - this document has open review items.", ""]
    if detail.summary is not None:
        lines += [f"**{detail.summary.headline}**", "", detail.summary.summary, ""]

    lines += ["## Policy tone", ""]
    if analysis is None:
        lines += ["No validated tone analysis is stored for this document.", ""]
    else:
        lines += [
            f"- Net tone score: {_score(analysis.net_tone_score)} ({analysis.classification})",
            f"- Confidence: {analysis.confidence:.2f}",
        ]
        if comparison is not None:
            delta = comparison.overall_delta
            lines.append(
                "- Change vs previous statement: "
                + ("baseline (no previous statement)" if delta is None else _score(delta))
            )
        if previous is not None:
            lines.append(f"- Previous score: {_score(previous.net_tone_score)}")
        lines += ["", "| Dimension | Score |", "|---|---|"]
        for name, value in sorted(analysis.dimension_scores.items()):
            lines.append(f"| {name} | {'not addressed' if value is None else _score(value)} |")
        lines += ["", analysis.explanation, "", "### Evidence", ""]
        for item in analysis.evidence:
            lines.append(f'- ({item.dimension}, {item.direction}) "{item.text}"')
        if analysis.uncertainty_flags:
            lines += ["", "Uncertainty flags: " + ", ".join(analysis.uncertainty_flags)]
        lines.append("")

    diff = comparison.diff if comparison else None
    if diff:
        lines += ["## Language changes", ""]
        for sentence in diff.get("added", []):
            lines.append(f'- Added: "{sentence}"')
        for sentence in diff.get("removed", []):
            lines.append(f'- Removed: "{sentence}"')
        for change in diff.get("changed", []):
            lines.append(f'- Changed: "{change["previous"]}" -> "{change["current"]}"')
        lines.append("")

    if detail.metrics:
        lines += [
            "## Market reaction",
            "",
            "Observed moves around the release; association, not causation.",
            "",
            "| Instrument | Measure | Window | Value |",
            "|---|---|---|---|",
        ]
        for metric in detail.metrics:
            measure = metric.kind.value + (
                f" vs {metric.benchmark_symbol}" if metric.benchmark_symbol else ""
            )
            lines.append(
                f"| {metric.symbol} | {measure} | {metric.window} | {metric_display(metric)} |"
            )
        if any(is_backtest(m) for m in detail.metrics):
            as_of = min(m.as_of for m in detail.metrics)
            lines += [
                "",
                "Point-in-time (backtest) metrics: only data available as of "
                f"{as_of:%Y-%m-%d %H:%M}Z.",
            ]
        lines.append("")

    lines += ["## Audit", ""]
    record = signal_record(detail.row)
    for key in ("document_id", "source_url", "content_hash"):
        lines.append(f"- {key}: {record[key]}")
    lines.append(f"- parser_version: {document.parser_version}")
    if analysis is not None:
        for key in ("analysis_id", "model", "prompt_version", "schema_version", "analyzed_at"):
            lines.append(f"- {key}: {record[key]}")
    if detail.metrics:
        lines.append(f"- metric_version: {metric_record(detail.metrics[0])['metric_version']}")
    if detail.summary is not None:
        lines.append(
            f"- summary: {detail.summary.model} / {detail.summary.prompt_version} / "
            f"{detail.summary.schema_version}"
        )
    return "\n".join(lines) + "\n"
