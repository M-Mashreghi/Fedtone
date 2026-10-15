"""Document lifecycle states and the allowed-transition table.

Happy path:
    Discovered → Fetched → Parsed → Normalized → QueuedForAnalysis → Analyzed → Compared
    → MarketLinked (optional) → Published

Any non-terminal state may branch to NeedsReview or Failed. From either branch a document
resumes only at its last successful stage, so the pipeline re-attempts the step that broke.
Published is terminal: reprocessing creates a new version instead of mutating a published one.
"""

from enum import StrEnum


class DocumentStatus(StrEnum):
    DISCOVERED = "Discovered"
    FETCHED = "Fetched"
    PARSED = "Parsed"
    NORMALIZED = "Normalized"
    QUEUED_FOR_ANALYSIS = "QueuedForAnalysis"
    ANALYZED = "Analyzed"
    COMPARED = "Compared"
    MARKET_LINKED = "MarketLinked"
    PUBLISHED = "Published"
    NEEDS_REVIEW = "NeedsReview"
    FAILED = "Failed"


PIPELINE_STAGES: tuple[DocumentStatus, ...] = (
    DocumentStatus.DISCOVERED,
    DocumentStatus.FETCHED,
    DocumentStatus.PARSED,
    DocumentStatus.NORMALIZED,
    DocumentStatus.QUEUED_FOR_ANALYSIS,
    DocumentStatus.ANALYZED,
    DocumentStatus.COMPARED,
    DocumentStatus.MARKET_LINKED,
    DocumentStatus.PUBLISHED,
)

EXCEPTION_STATES = frozenset({DocumentStatus.NEEDS_REVIEW, DocumentStatus.FAILED})
TERMINAL_STATES = frozenset({DocumentStatus.PUBLISHED})


def _forward_transitions() -> dict[DocumentStatus, frozenset[DocumentStatus]]:
    table: dict[DocumentStatus, set[DocumentStatus]] = {}
    for current, following in zip(PIPELINE_STAGES, PIPELINE_STAGES[1:], strict=False):
        table[current] = {following}
    # Market linking is optional enrichment.
    table[DocumentStatus.COMPARED].add(DocumentStatus.PUBLISHED)
    for stage, targets in table.items():
        targets.update(EXCEPTION_STATES)
        table[stage] = targets
    return {stage: frozenset(targets) for stage, targets in table.items()}


FORWARD_TRANSITIONS = _forward_transitions()


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition is not allowed."""


def is_forward_transition_allowed(current: DocumentStatus, target: DocumentStatus) -> bool:
    return target in FORWARD_TRANSITIONS.get(current, frozenset())
