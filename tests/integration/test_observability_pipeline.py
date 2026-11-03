"""Logs of a full scheduled cycle are JSON, carry correlation ids and contain no secret."""

import io
import json
import logging

import pytest

from fedtone.application.llm import LLMRequest, LLMResponse, LLMTransientError
from fedtone.application.pipeline import PipelineService
from fedtone.application.scheduler import PipelineScheduler
from fedtone.infrastructure.pipeline import default_stage_runners
from fedtone.observability import JsonFormatter
from tests.e2e_support import PipelineLLM, e2e_settings, fixture_factories

FAKE_KEY = "sk-test-secret-value-424242"


class LeakyLLM(PipelineLLM):
    """A provider whose first error message echoes the API key."""

    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.failed_once:
            self.failed_once = True
            raise LLMTransientError(f"upstream said: invalid api_key={FAKE_KEY} for {FAKE_KEY}")
        return super().complete(request)


@pytest.fixture
def captured_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG)
    yield handler, stream
    root.handlers[:], level = saved
    root.setLevel(level)


def test_cycle_logs_have_correlation_ids_and_no_secret(
    database_url, session_factory, captured_logs
) -> None:
    handler, stream = captured_logs
    settings = e2e_settings(database_url, llm={"api_key": FAKE_KEY, "model": "m"})
    handler.setFormatter(JsonFormatter(settings.secret_values()))
    scheduler = PipelineScheduler(
        PipelineService(
            default_stage_runners(settings, session_factory, fixture_factories(llm=LeakyLLM()))
        )
    )

    scheduler.run_cycle()

    output = stream.getvalue()
    assert FAKE_KEY not in output
    lines = [json.loads(line) for line in output.splitlines()]
    assert lines and all({"timestamp", "level", "message", "run_id"} <= set(x) for x in lines)
    assert all(line["run_id"] for line in lines)  # every line belongs to a cycle or run
    assert any(line["document_id"] for line in lines)
    assert any("pipeline cycle finished" in line["message"] for line in lines)
