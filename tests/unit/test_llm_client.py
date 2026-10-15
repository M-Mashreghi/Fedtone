import json
import logging

import httpx
import pytest

from fedtone.application.llm import LLMPermanentError, LLMRequest, LLMTransientError
from fedtone.infrastructure.llm.openai_compatible import OpenAICompatibleClient

KEY = "sk-test-not-real-0123456789"
REQUEST = LLMRequest("system", "user", "tone_analysis", {"type": "object"}, 0.0)


def ok_body(content='{"a": 1}'):
    return {
        "model": "provider-model-2024",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def client_for(responses, max_retries=2):
    seen, sleeps = [], []
    items = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = next(items)
        if isinstance(item, Exception):
            raise item
        return item

    client = OpenAICompatibleClient(
        api_key=KEY,
        model="configured-model",
        base_url="https://llm.example.test/v1/",
        max_retries=max_retries,
        backoff_seconds=0.5,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    return client, seen, sleeps


def test_successful_structured_request() -> None:
    client, seen, _ = client_for([httpx.Response(200, json=ok_body())])

    response = client.complete(REQUEST)

    assert response.content == '{"a": 1}'
    assert response.model == "provider-model-2024"
    assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    (request,) = seen
    assert str(request.url) == "https://llm.example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "configured-model"
    assert body["temperature"] == 0.0
    assert body["messages"][0] == {"role": "system", "content": "system"}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "tone_analysis"


def test_transient_errors_retry_with_backoff() -> None:
    client, seen, sleeps = client_for(
        [httpx.ConnectTimeout("t"), httpx.Response(429), httpx.Response(200, json=ok_body())]
    )

    assert client.complete(REQUEST).content == '{"a": 1}'
    assert sleeps == [0.5, 1.0]


def test_retries_are_bounded() -> None:
    client, seen, _ = client_for([httpx.Response(503)] * 2, max_retries=1)

    with pytest.raises(LLMTransientError, match="after 2 attempts"):
        client.complete(REQUEST)
    assert len(seen) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_permanent_and_never_echo_the_key(status: int) -> None:
    body = {"error": f"invalid key {KEY}"}
    client, seen, _ = client_for([httpx.Response(status, json=body)])

    with pytest.raises(LLMPermanentError) as excinfo:
        client.complete(REQUEST)

    assert KEY not in str(excinfo.value)
    assert len(seen) == 1


@pytest.mark.parametrize("body", [{"choices": []}, {"nothing": 1}, "not json"])
def test_unexpected_response_shape_is_transient(body) -> None:
    response = (
        httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
    )
    client, _, _ = client_for([response], max_retries=0)

    with pytest.raises(LLMTransientError):
        client.complete(REQUEST)


@pytest.mark.parametrize(("key", "model"), [("", "m"), ("k", "")])
def test_missing_configuration_is_rejected(key: str, model: str) -> None:
    with pytest.raises(LLMPermanentError, match="not configured"):
        OpenAICompatibleClient(api_key=key, model=model)


def test_key_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = client_for([httpx.Response(500), httpx.Response(200, json=ok_body())])

    with caplog.at_level(logging.DEBUG):
        client.complete(REQUEST)

    assert KEY not in caplog.text
