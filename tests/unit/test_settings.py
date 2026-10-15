import os

import pytest
from pydantic import ValidationError

from fedtone.settings import REDACTED, Environment, Settings, redact

FAKE_KEY = "sk-test-not-a-real-key-123456"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name in list(os.environ):
        if name.startswith("FEDTONE_"):
            monkeypatch.delenv(name)
    # Ignore any developer .env file in the working directory.
    monkeypatch.chdir(tmp_path)


def test_defaults_are_safe_for_local_development() -> None:
    settings = Settings()

    assert settings.env is Environment.DEVELOPMENT
    assert settings.database.url.startswith("sqlite:///")
    assert settings.llm.api_key is None
    assert settings.llm.temperature == 0.0
    assert settings.scheduler.enabled is False
    assert settings.secret_values() == []


def test_environment_overrides_top_level_and_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("FEDTONE_DATABASE__URL", "sqlite:///./other.db")
    monkeypatch.setenv("FEDTONE_LLM__MODEL", "example-model")
    monkeypatch.setenv("FEDTONE_LLM__TEMPERATURE", "0.2")
    monkeypatch.setenv("FEDTONE_SCHEDULER__ENABLED", "true")
    monkeypatch.setenv("FEDTONE_MARKET_DATA__PROVIDER", "csv")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.database.url == "sqlite:///./other.db"
    assert settings.llm.model == "example-model"
    assert settings.llm.temperature == 0.2
    assert settings.scheduler.enabled is True
    assert settings.market_data.provider == "csv"


def test_dotenv_file_is_read(tmp_path) -> None:
    (tmp_path / ".env").write_text("FEDTONE_LLM__MODEL=from-dotenv\n", encoding="utf-8")

    assert Settings().llm.model == "from-dotenv"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FEDTONE_LLM__TEMPERATURE", "3"),
        ("FEDTONE_LLM__MAX_RETRIES", "-1"),
        ("FEDTONE_SCHEDULER__INGESTION_INTERVAL_MINUTES", "0"),
        ("FEDTONE_LOG_LEVEL", "VERBOSE"),
        ("FEDTONE_ENV", "staging"),
    ],
)
def test_invalid_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


def test_production_without_secrets_fails_with_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEDTONE_ENV", "production")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "FEDTONE_LLM__API_KEY" in message
    assert "FEDTONE_LLM__MODEL" in message


def test_production_with_secrets_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_ENV", "production")
    monkeypatch.setenv("FEDTONE_LLM__API_KEY", FAKE_KEY)
    monkeypatch.setenv("FEDTONE_LLM__MODEL", "example-model")

    settings = Settings()

    assert settings.env is Environment.PRODUCTION
    assert settings.secret_values() == [FAKE_KEY]


def test_empty_api_key_counts_as_missing_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_ENV", "production")
    monkeypatch.setenv("FEDTONE_LLM__API_KEY", "")
    monkeypatch.setenv("FEDTONE_LLM__MODEL", "example-model")

    with pytest.raises(ValidationError, match="FEDTONE_LLM__API_KEY"):
        Settings()


def test_secret_never_appears_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_LLM__API_KEY", FAKE_KEY)
    monkeypatch.setenv("FEDTONE_MARKET_DATA__API_KEY", FAKE_KEY + "-market")

    settings = Settings()

    for rendered in (repr(settings), str(settings), settings.model_dump_json()):
        assert FAKE_KEY not in rendered


def test_secret_never_appears_in_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_ENV", "production")
    monkeypatch.setenv("FEDTONE_LLM__API_KEY", FAKE_KEY)
    monkeypatch.setenv("FEDTONE_LLM__TEMPERATURE", "9")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in repr(excinfo.value.errors())


def test_redact_replaces_every_secret_occurrence() -> None:
    text = f"auth failed for key={FAKE_KEY}; retry with {FAKE_KEY}"

    assert redact(text, [FAKE_KEY]) == f"auth failed for key={REDACTED}; retry with {REDACTED}"


def test_redact_handles_overlapping_and_empty_secrets() -> None:
    short, long = "abc123", "abc123-extended"

    assert redact(f"{long} {short}", [short, long, ""]) == f"{REDACTED} {REDACTED}"
    assert redact("nothing here", []) == "nothing here"


def test_source_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDTONE_SOURCES__MAX_RETRIES", "5")
    monkeypatch.setenv(
        "FEDTONE_SOURCES__FOMC_LISTING_URLS",
        '["https://www.federalreserve.gov/monetarypolicy/fomchistorical2019.htm"]',
    )

    sources = Settings().sources

    assert sources.max_retries == 5
    assert sources.fomc_listing_urls == [
        "https://www.federalreserve.gov/monetarypolicy/fomchistorical2019.htm"
    ]
