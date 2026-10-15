"""Historical analogues for one meeting, with the reactions that followed them.

Point-in-time rules:
- candidates are meetings of the same bank and type published before the cutoff;
- only analyses produced by the same model, prompt and schema as the target's are compared,
  because scores from other versions are not on the same scale;
- an analogue's market metric is shown only if its end observation was fixed before the
  cutoff; later ones are withheld, so a backtest never sees prices after its cutoff.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fedtone.application.ports import (
    ComparisonRepository,
    EventMetricRepository,
    PolicyDocumentRepository,
    ToneAnalysisRepository,
)
from fedtone.application.tone_analysis import HAS_ANALYSIS
from fedtone.domain.analogues import AnalogueResult, MeetingFeatures, find_analogues
from fedtone.domain.metrics import EventMetric
from fedtone.domain.models import PolicyDocument, ToneAnalysis


@dataclass(frozen=True)
class AnalogueReport:
    result: AnalogueResult
    # Per analogue: metrics whose end observation was fixed before the cutoff.
    reactions: dict[UUID, list[EventMetric]]
    withheld_after_cutoff: int
    excluded_other_versions: int


class AnalogueService:
    def __init__(
        self,
        *,
        documents: PolicyDocumentRepository,
        analyses: ToneAnalysisRepository,
        comparisons: ComparisonRepository,
        metrics: EventMetricRepository,
    ) -> None:
        self._documents = documents
        self._analyses = analyses
        self._comparisons = comparisons
        self._metrics = metrics

    def _features(self, document: PolicyDocument, analysis: ToneAnalysis) -> MeetingFeatures:
        comparison = self._comparisons.latest_for_document(document.id)
        return MeetingFeatures(
            document_id=document.id,
            published_at=document.published_at,
            overall=analysis.net_tone_score,
            dimensions=analysis.dimension_scores,
            overall_delta=comparison.overall_delta if comparison else None,
        )

    def find(
        self,
        document_id: UUID,
        *,
        top_k: int = 5,
        min_sample: int = 3,
        cutoff: datetime | None = None,
    ) -> AnalogueReport:
        target_doc = self._documents.get(document_id)
        if target_doc is None:
            raise LookupError(f"document {document_id} not found")
        target_analysis = self._analyses.latest_for_document(document_id)
        if target_doc.published_at is None or target_analysis is None:
            raise ValueError("the document needs a publication time and a tone analysis")
        target = self._features(target_doc, target_analysis)
        version = _version(target_analysis)

        candidates, excluded = [], 0
        for doc in self._documents.list(
            central_bank=target_doc.central_bank,
            document_type=target_doc.document_type,
            limit=None,
        ):
            if doc.id == target_doc.id or doc.status not in HAS_ANALYSIS:
                continue
            if doc.published_at is None:
                continue
            analysis = self._analyses.latest_for_document(doc.id)
            if analysis is None:
                continue
            if _version(analysis) != version:
                excluded += 1
                continue
            candidates.append(self._features(doc, analysis))

        result = find_analogues(
            target, candidates, cutoff=cutoff, top_k=top_k, min_sample=min_sample
        )
        reactions, withheld = {}, 0
        for match in result.matches:
            visible = []
            for metric in self._metrics.for_document(match.document_id):
                if metric.end_observed_at < result.cutoff:
                    visible.append(metric)
                else:
                    withheld += 1
            reactions[match.document_id] = visible
        return AnalogueReport(result, reactions, withheld, excluded)


def _version(analysis: ToneAnalysis) -> tuple[str, str, str]:
    return analysis.model, analysis.prompt_version, analysis.schema_version
