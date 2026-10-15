"""Environment-driven application configuration.

Values come from environment variables prefixed with ``FEDTONE_`` (and an optional ``.env``
file). Nested groups use a double underscore, e.g. ``FEDTONE_LLM__API_KEY``.
Secrets are held as ``SecretStr`` so they never appear in reprs, dumps or validation errors.
"""

from collections.abc import Iterable
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REDACTED = "***REDACTED***"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///./data/fedtone.db"


class LLMSettings(BaseModel):
    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0)
    # Valid analyses below this confidence are stored but routed to human review.
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class SourceSettings(BaseModel):
    """HTTP behaviour for official-source adapters."""

    user_agent: str = "FedTone/0.1 (central-bank communication research)"
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0)
    fomc_listing_urls: list[str] = [
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    ]


class SchedulerSettings(BaseModel):
    enabled: bool = False
    ingestion_interval_minutes: int = Field(default=60, gt=0)


class InstrumentSettings(BaseModel):
    symbol: str
    name: str
    kind: Literal["price", "yield_percent"]
    calendar: Literal["nyse", "us_treasury"]
    # New York local time the daily value refers to, "HH:MM".
    observation_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


DEFAULT_INSTRUMENTS = [
    InstrumentSettings(
        symbol="SP500",
        name="S&P 500 index",
        kind="price",
        calendar="nyse",
        observation_time="16:00",
    ),
    InstrumentSettings(
        symbol="DGS2",
        name="2-Year Treasury yield",
        kind="yield_percent",
        calendar="us_treasury",
        observation_time="15:30",
    ),
    InstrumentSettings(
        symbol="DGS10",
        name="10-Year Treasury yield",
        kind="yield_percent",
        calendar="us_treasury",
        observation_time="15:30",
    ),
    InstrumentSettings(
        symbol="DTWEXBGS",
        name="Nominal broad U.S. dollar index",
        kind="price",
        calendar="us_treasury",
        observation_time="12:00",
    ),
]


class MarketDataSettings(BaseModel):
    # none | fred (public FRED CSV download, no key) | csv (local files in csv_directory)
    provider: Literal["none", "fred", "csv"] = "none"
    api_key: SecretStr | None = None
    base_url: str | None = None
    csv_directory: str = "./data/market"
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0)
    instruments: list[InstrumentSettings] = Field(default_factory=lambda: list(DEFAULT_INSTRUMENTS))
    # Event windows in return days relative to the event session T.
    windows: list[str] = ["[0,0]", "[0,+1]", "[0,+5]"]
    # Optional benchmark symbol for excess returns of price instruments on the same calendar.
    benchmark_symbol: str | None = None
    analogue_top_k: int = Field(default=5, ge=1)
    analogue_min_sample: int = Field(default=3, ge=1)

    @field_validator("benchmark_symbol", mode="before")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FEDTONE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database: DatabaseSettings = DatabaseSettings()
    llm: LLMSettings = LLMSettings()
    sources: SourceSettings = SourceSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    market_data: MarketDataSettings = MarketDataSettings()

    @model_validator(mode="after")
    def _require_production_secrets(self) -> "Settings":
        if self.env is not Environment.PRODUCTION:
            return self
        missing = []
        if not _has_secret(self.llm.api_key):
            missing.append("FEDTONE_LLM__API_KEY")
        if not self.llm.model:
            missing.append("FEDTONE_LLM__MODEL")
        if missing:
            raise ValueError(f"production configuration is missing: {', '.join(missing)}")
        return self

    def secret_values(self) -> list[str]:
        """Plain values of all configured secrets, for redaction only."""
        secrets = (self.llm.api_key, self.market_data.api_key)
        return [s.get_secret_value() for s in secrets if _has_secret(s)]


def _has_secret(value: SecretStr | None) -> bool:
    return value is not None and bool(value.get_secret_value())


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each non-empty secret in ``text``."""
    # Longest first so a secret that contains another is fully replaced.
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text


@lru_cache
def get_settings() -> Settings:
    return Settings()
