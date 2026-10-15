"""Offline evaluation harness for the tone analyzer.

Runs every labeled statement through the real analysis path (prompt → LLM → schema →
evidence validation) several times and reports:

- direction agreement: hawkish/neutral/dovish of the mean score vs the human label;
- label agreement: exact five-way label of the mean score vs the human label;
- score stability: per-statement score range and standard deviation across repeats;
- schema and evidence failure rates per run, and rejected-run rate.

All metrics are computed here in deterministic code from the stored run results.
"""

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fedtone.application.llm import LLMTransientError
from fedtone.application.tone_analysis import Rejection, ToneAnalyzer
from fedtone.domain.models import DocumentText, PolicyDocument, ToneClassification
from fedtone.domain.text import sha256_hex
from fedtone.domain.tone_schema import label_for_score

HAWKISH = {ToneClassification.HAWKISH, ToneClassification.STRONGLY_HAWKISH}
DOVISH = {ToneClassification.DOVISH, ToneClassification.STRONGLY_DOVISH}


def direction(label: ToneClassification) -> str:
    if label in HAWKISH:
        return "hawkish"
    if label in DOVISH:
        return "dovish"
    return "neutral"


@dataclass(frozen=True)
class LabeledStatement:
    id: str
    title: str
    paragraphs: tuple[str, ...]
    expected_label: ToneClassification
    published_at: datetime | None = None


def load_dataset(path: Path) -> list[LabeledStatement]:
    """JSON list of {id, title, paragraphs, expected_label, published_at?}."""
    items = json.loads(path.read_text(encoding="utf-8"))
    return [
        LabeledStatement(
            id=item["id"],
            title=item["title"],
            paragraphs=tuple(item["paragraphs"]),
            expected_label=ToneClassification(item["expected_label"]),
            published_at=(
                datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None
            ),
        )
        for item in items
    ]


@dataclass(frozen=True)
class RunResult:
    item_id: str
    score: float | None
    confidence: float | None
    failure: str | None  # None, "schema", "evidence" or "provider"


@dataclass
class ItemSummary:
    item_id: str
    expected_label: ToneClassification
    scores: list[float] = field(default_factory=list)

    @property
    def mean_score(self) -> float | None:
        return statistics.fmean(self.scores) if self.scores else None

    @property
    def predicted_label(self) -> ToneClassification | None:
        mean = self.mean_score
        return None if mean is None else label_for_score(mean)

    @property
    def score_range(self) -> float | None:
        return max(self.scores) - min(self.scores) if self.scores else None

    @property
    def score_stdev(self) -> float | None:
        return statistics.pstdev(self.scores) if self.scores else None


@dataclass
class BenchmarkReport:
    model: str
    prompt_version: str
    repeats: int
    runs: list[RunResult]
    items: list[ItemSummary]
    created_at: datetime

    def _rate(self, predicate) -> float:
        return sum(1 for r in self.runs if predicate(r)) / len(self.runs) if self.runs else 0.0

    @property
    def metrics(self) -> dict[str, float | int | None]:
        scored = [i for i in self.items if i.predicted_label is not None]
        ranges = [i.score_range for i in scored]
        stdevs = [i.score_stdev for i in scored]
        return {
            "items": len(self.items),
            "runs": len(self.runs),
            "items_scored": len(scored),
            "direction_agreement": (
                sum(direction(i.predicted_label) == direction(i.expected_label) for i in scored)
                / len(scored)
                if scored
                else None
            ),
            "label_agreement": (
                sum(i.predicted_label == i.expected_label for i in scored) / len(scored)
                if scored
                else None
            ),
            "mean_score_range": statistics.fmean(ranges) if ranges else None,
            "max_score_range": max(ranges) if ranges else None,
            "mean_score_stdev": statistics.fmean(stdevs) if stdevs else None,
            "schema_failure_rate": self._rate(lambda r: r.failure == "schema"),
            "evidence_failure_rate": self._rate(lambda r: r.failure == "evidence"),
            "provider_failure_rate": self._rate(lambda r: r.failure == "provider"),
            "rejected_run_rate": self._rate(lambda r: r.failure is not None),
        }

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "repeats": self.repeats,
            "created_at": self.created_at.isoformat(),
            "metrics": self.metrics,
            "items": [
                {
                    "id": i.item_id,
                    "expected_label": i.expected_label.value,
                    "predicted_label": i.predicted_label.value if i.predicted_label else None,
                    "scores": i.scores,
                    "score_range": i.score_range,
                }
                for i in self.items
            ],
        }

    def to_markdown(self) -> str:
        m = self.metrics
        lines = [
            "# FedTone tone benchmark",
            "",
            f"Model `{self.model}`, prompt `{self.prompt_version}`, {self.repeats} repeat(s), "
            f"{self.created_at:%Y-%m-%d %H:%M} UTC.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in m.items():
            shown = f"{value:.3f}" if isinstance(value, float) else str(value)
            lines.append(f"| {key} | {shown} |")
        lines += ["", "| Item | Expected | Predicted | Scores |", "|---|---|---|---|"]
        for i in self.items:
            predicted = i.predicted_label.value if i.predicted_label else "—"
            scores = ", ".join(f"{s:+.2f}" for s in i.scores) or "—"
            lines.append(f"| {i.item_id} | {i.expected_label.value} | {predicted} | {scores} |")
        return "\n".join(lines) + "\n"


def run_benchmark(
    analyzer: ToneAnalyzer,
    dataset: Sequence[LabeledStatement],
    *,
    model: str,
    prompt_version: str,
    repeats: int = 3,
    now: datetime | None = None,
) -> BenchmarkReport:
    now = now or datetime.now(UTC)
    runs: list[RunResult] = []
    items: list[ItemSummary] = []
    for statement in dataset:
        summary = ItemSummary(statement.id, statement.expected_label)
        document, text = _as_document(statement, now)
        for _ in range(repeats):
            try:
                result = analyzer.analyze(document, text, now=now)
            except LLMTransientError:
                runs.append(RunResult(statement.id, None, None, "provider"))
                continue
            if isinstance(result, Rejection):
                kind = "schema" if ": schema:" in result.problems[-1] else "evidence"
                runs.append(RunResult(statement.id, None, None, kind))
                continue
            summary.scores.append(result.net_tone_score)
            runs.append(RunResult(statement.id, result.net_tone_score, result.confidence, None))
        items.append(summary)
    return BenchmarkReport(model, prompt_version, repeats, runs, items, now)


def _as_document(statement: LabeledStatement, now: datetime) -> tuple[PolicyDocument, DocumentText]:
    document = PolicyDocument(
        central_bank="BENCHMARK",
        document_type="labeled_statement",
        source_url=f"benchmark://{statement.id}",
        discovered_at=now,
    )
    text = DocumentText.from_parsed(
        document_id=document.id,
        snapshot_id=uuid4(),
        title=statement.title,
        raw_paragraphs=statement.paragraphs,
        parser_version="benchmark",
        published_at=statement.published_at,
        created_at=now,
    )
    document.content_hash = sha256_hex(text.normalized_text)
    return document, text
