import io
import json
import logging
from uuid import uuid4

import pytest

from fedtone.observability import (
    JsonFormatter,
    configure_logging,
    correlation_context,
    scrub_secrets,
)
from fedtone.settings import REDACTED, Settings

FAKE_KEY = "sk-test-not-a-real-key-987654"


@pytest.fixture
def capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter([FAKE_KEY]))
    logger = logging.getLogger("fedtone.test.observability")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    def records() -> list[dict]:
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    yield logger, records, stream
    logger.handlers.clear()


def test_log_lines_are_json_with_correlation_ids(capture) -> None:
    logger, records, _ = capture
    run_id, document_id = uuid4(), uuid4()

    with correlation_context(run_id=run_id, document_id=document_id):
        logger.info("parsed document %s", "fomc-2024-01")
    logger.info("outside")

    inside, outside = records()
    assert inside["message"] == "parsed document fomc-2024-01"
    assert inside["level"] == "INFO"
    assert inside["run_id"] == str(run_id)
    assert inside["document_id"] == str(document_id)
    assert outside["run_id"] is None
    assert outside["document_id"] is None


def test_nested_correlation_context_restores_outer_ids(capture) -> None:
    logger, records, _ = capture

    with correlation_context(run_id="run-1"):
        with correlation_context(document_id="doc-1"):
            logger.info("inner")
        logger.info("outer")

    inner, outer = records()
    assert (inner["run_id"], inner["document_id"]) == ("run-1", "doc-1")
    assert (outer["run_id"], outer["document_id"]) == ("run-1", None)


def test_secret_in_message_args_and_exception_is_scrubbed(capture) -> None:
    logger, records, stream = capture

    logger.warning("calling provider with key %s", FAKE_KEY)
    try:
        raise RuntimeError(f"401 for key {FAKE_KEY}")
    except RuntimeError:
        logger.exception("provider failed")

    assert FAKE_KEY not in stream.getvalue()
    warning, error = records()
    assert REDACTED in warning["message"]
    assert REDACTED in error["exception"]


@pytest.mark.parametrize(
    "text",
    [
        "api_key=abc123secret",
        "API-KEY: abc123secret",
        "token='abc123secret'",
        "password=abc123secret&user=x",
        "Authorization: Bearer abc123secret",
    ],
)
def test_scrub_secrets_removes_credential_patterns(text: str) -> None:
    scrubbed = scrub_secrets(text)

    assert "abc123secret" not in scrubbed
    assert REDACTED in scrubbed


def test_scrub_secrets_leaves_ordinary_text() -> None:
    text = "The Committee decided to maintain the target range at 5-1/4 to 5-1/2 percent."

    assert scrub_secrets(text) == text


def test_configure_logging_uses_settings_secrets_and_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FEDTONE_LLM__API_KEY", FAKE_KEY)
    monkeypatch.setenv("FEDTONE_LOG_LEVEL", "WARNING")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(Settings(_env_file=None))
        logging.getLogger("fedtone.test").info("hidden")
        logging.getLogger("fedtone.test").warning("leak %s", FAKE_KEY)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert FAKE_KEY not in lines[0]
    assert json.loads(lines[0])["level"] == "WARNING"
