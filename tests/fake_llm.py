"""Deterministic fake LLM for tests: answers from the prompt it receives, never the network."""

import json
import re
from collections.abc import Callable

from fedtone.application.llm import LLMRequest, LLMResponse
from fedtone.domain.tone_schema import label_for_score

_DOC = re.compile(r'<<<SOURCE_DOCUMENT role="(\w+)"[^>]*>>>\nTitle: [^\n]*\n\n(.*?)\n<<<END', re.S)


def documents(request: LLMRequest) -> dict[str, str]:
    return {role: body for role, body in _DOC.findall(request.user)}


def first_sentence(text: str) -> str:
    return re.split(r"(?<=\.)\s", text.strip(), maxsplit=1)[0]


def tone_json(text: str, score: float = 0.3, confidence: float = 0.8, quote: str | None = None):
    quote = quote or first_sentence(text)
    return json.dumps(
        {
            "schema_version": "tone-analysis-1.0",
            "overall_score": score,
            "label": label_for_score(score),
            "confidence": confidence,
            "dimensions": [
                {"dimension": "inflation", "score": score, "rationale": "from statement"},
                {"dimension": "balance_sheet", "score": None, "rationale": "not addressed"},
            ],
            "evidence": [{"quote": quote, "dimension": "inflation", "direction": "hawkish"}],
            "rationale": "Synthetic rationale.",
            "uncertainty_flags": [],
        }
    )


def comparison_json(request: LLMRequest) -> str:
    diff = documents(request)["diff"]
    changes = []
    for line in diff.splitlines():
        if line.startswith("ADDED: "):
            changes.append(
                {
                    "kind": "added",
                    "dimension": "policy_guidance",
                    "direction": "neutral",
                    "current_quote": line.removeprefix("ADDED: "),
                    "explanation": "New sentence.",
                }
            )
        elif line.startswith("REMOVED: "):
            changes.append(
                {
                    "kind": "removed",
                    "dimension": "policy_guidance",
                    "direction": "neutral",
                    "previous_quote": line.removeprefix("REMOVED: "),
                    "explanation": "Dropped sentence.",
                }
            )
    return json.dumps(
        {"schema_version": "tone-comparison-1.0", "summary": "Synthetic.", "changes": changes}
    )


class FakeLLM:
    """``scores`` maps a substring of the document text to (score, confidence).

    ``script`` overrides responses in order (strings returned as-is, exceptions raised).
    """

    model = "fake-model-1"

    def __init__(
        self,
        scores: dict[str, tuple[float, float]] | None = None,
        script: list | None = None,
        responder: Callable[[LLMRequest], str] | None = None,
    ) -> None:
        self.scores = scores or {}
        self.script = list(script or [])
        self.responder = responder
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return LLMResponse(item, self.model)
        if self.responder:
            return LLMResponse(self.responder(request), self.model)
        if request.schema_name == "tone_comparison":
            return LLMResponse(comparison_json(request), self.model)
        text = documents(request)["current"]
        score, confidence = next((v for k, v in self.scores.items() if k in text), (0.3, 0.8))
        return LLMResponse(tone_json(text, score, confidence), self.model)
