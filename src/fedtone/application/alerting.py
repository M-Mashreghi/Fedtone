"""Alert evaluation stage: compared documents -> stored alerts -> notifier (Task 7.5).

Each (document, rule) fires at most once (the repository ignores repeats), so re-running the
stage or the scheduler never re-sends an alert. Notifier failures are logged and do not stop
the pipeline; the alert stays stored and visible in the API.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fedtone.application.ports import (
    AlertRepository,
    ComparisonRepository,
    PolicyDocumentRepository,
    ToneAnalysisRepository,
)
from fedtone.application.tone_analysis import HAS_ANALYSIS
from fedtone.domain.alerts import Alert, AlertConfig, evaluate_alerts
from fedtone.domain.lifecycle import DocumentStatus
from fedtone.observability import correlation_context

logger = logging.getLogger(__name__)


class AlertNotifier:
    """Default notifier: a structured warning log line per alert."""

    def notify(self, alert: Alert) -> None:
        logger.warning("tone alert [%s]: %s", alert.rule, alert.message)


@dataclass
class AlertReport:
    evaluated: int = 0
    fired: list[Alert] = field(default_factory=list)
    notify_errors: int = 0


class AlertService:
    def __init__(
        self,
        *,
        config: AlertConfig,
        documents: PolicyDocumentRepository,
        analyses: ToneAnalysisRepository,
        comparisons: ComparisonRepository,
        alerts: AlertRepository,
        notifiers: Sequence[AlertNotifier] = (AlertNotifier(),),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._documents = documents
        self._analyses = analyses
        self._comparisons = comparisons
        self._alerts = alerts
        self._notifiers = list(notifiers)
        self._clock = clock

    def run(self, central_bank: str, document_type: str) -> AlertReport:
        report = AlertReport()
        for document in self._documents.list(
            central_bank=central_bank, document_type=document_type, limit=None
        ):
            if document.status not in HAS_ANALYSIS or document.status is DocumentStatus.ANALYZED:
                continue
            report.evaluated += 1
            with correlation_context(document_id=document.id):
                for alert in self._evaluate(document.id):
                    if not self._alerts.add(alert):
                        continue  # already fired for this document and rule
                    report.fired.append(alert)
                    for notifier in self._notifiers:
                        try:
                            notifier.notify(alert)
                        except Exception:  # noqa: BLE001 - a broken sink must not stop the run
                            logger.exception("alert notifier failed")
                            report.notify_errors += 1
        return report

    def _evaluate(self, document_id) -> list[Alert]:
        current = self._analyses.latest_for_document(document_id)
        comparison = self._comparisons.latest_for_document(document_id)
        if current is None or comparison is None:
            return []
        previous = (
            self._analyses.get(comparison.previous_analysis_id)
            if comparison.previous_analysis_id
            else None
        )
        return evaluate_alerts(self._config, current, previous, comparison, now=self._clock())
