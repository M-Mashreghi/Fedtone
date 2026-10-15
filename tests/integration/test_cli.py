import logging

import httpx
import pytest
from alembic import command

from fedtone import cli
from fedtone.settings import get_settings
from tests.fomc_fixtures import fixture_transport
from tests.integration.conftest import alembic_config


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path):
    command.upgrade(alembic_config(database_url), "head")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FEDTONE_DATABASE__URL", database_url)
    monkeypatch.setenv("FEDTONE_SOURCES__MAX_RETRIES", "0")
    get_settings.cache_clear()
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = fixture_transport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli.httpx, "Client", fake_client)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    get_settings.cache_clear()


def test_ingest_command_is_idempotent(configured, capsys) -> None:
    assert cli.main(["ingest"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["ingest"]) == 0
    second = capsys.readouterr().out

    assert "succeeded" in first and "ingested=3" in first
    assert "ingested=0" in second and "unchanged=3" in second


def test_reviews_command_lists_open_items(configured, capsys) -> None:
    assert cli.main(["reviews"]) == 0

    assert "0 open review item(s)" in capsys.readouterr().out


def test_unsupported_source_exits_with_error(configured, capsys) -> None:
    assert cli.main(["ingest", "--bank", "ECB"]) == 2
    assert "no source adapter for ECB/fomc_statement" in capsys.readouterr().err


def test_analyze_without_llm_configuration_explains_what_is_missing(configured, capsys) -> None:
    assert cli.main(["analyze"]) == 2
    assert "FEDTONE_LLM__API_KEY" in capsys.readouterr().err


def test_analyze_command_runs_with_configured_client(configured, monkeypatch, capsys) -> None:
    from tests.fake_llm import FakeLLM

    monkeypatch.setattr(cli, "build_llm_client", lambda settings: FakeLLM())
    assert cli.main(["ingest"]) == 0
    capsys.readouterr()

    assert cli.main(["analyze"]) == 0
    out = capsys.readouterr().out
    assert "analyzed=3" in out
    assert "compared=3" in out


def test_benchmark_command_writes_reports(configured, monkeypatch, tmp_path, capsys) -> None:
    from pathlib import Path

    from tests.fake_llm import FakeLLM

    monkeypatch.setattr(cli, "build_llm_client", lambda settings: FakeLLM())
    dataset = Path(__file__).parents[1] / "fixtures" / "eval" / "mini_benchmark.json"

    code = cli.main(
        ["benchmark", "--dataset", str(dataset), "--repeats", "1", "--output", str(tmp_path / "r")]
    )

    assert code == 0
    assert (tmp_path / "r" / "benchmark.json").is_file()
    assert "direction_agreement" in (tmp_path / "r" / "benchmark.md").read_text()


def test_reactions_without_provider_explains_configuration(configured, capsys) -> None:
    assert cli.main(["reactions"]) == 2
    assert "FEDTONE_MARKET_DATA__PROVIDER" in capsys.readouterr().err


def test_reactions_rejects_naive_as_of(configured, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FEDTONE_MARKET_DATA__PROVIDER", "csv")
    get_settings.cache_clear()
    assert cli.main(["reactions", "--as-of", "2024-03-01T00:00:00"]) == 2
    assert "timezone" in capsys.readouterr().err


def test_market_research_commands_end_to_end(configured, monkeypatch, tmp_path, capsys) -> None:
    import re
    from datetime import date

    from tests.fake_llm import FakeLLM
    from tests.fake_market import weekday_series
    from tests.integration.test_research_summary import faithful

    market = tmp_path / "market"
    market.mkdir()
    for symbol, first, step in (
        ("SP500", 4500.0, 5.0),
        ("DGS2", 4.9, -0.01),
        ("DGS10", 4.5, -0.01),
        ("DTWEXBGS", 120.0, 0.05),
    ):
        rows = weekday_series(date(2023, 11, 1), date(2024, 5, 31), first, step)
        lines = [f"observation_date,{symbol}", *(f"{d},{v}" for d, v in rows.items())]
        (market / f"{symbol}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("FEDTONE_MARKET_DATA__PROVIDER", "csv")
    monkeypatch.setenv("FEDTONE_MARKET_DATA__CSV_DIRECTORY", str(market))
    get_settings.cache_clear()
    assert cli.main(["ingest"]) == 0
    monkeypatch.setattr(cli, "build_llm_client", lambda settings: FakeLLM())
    assert cli.main(["analyze"]) == 0
    capsys.readouterr()

    assert cli.main(["reactions"]) == 0
    assert "linked=3" in capsys.readouterr().out

    assert cli.main(["documents"]) == 0
    listing = capsys.readouterr().out
    assert listing.count("MarketLinked") == 3
    last_id = re.findall(r"^(\S+) 2024-03-20", listing, re.M)[0]

    assert cli.main(["analogues", last_id, "--min-sample", "1"]) == 0
    out = capsys.readouterr().out
    assert "2 earlier meeting(s)" in out
    assert "SP500 simple_return [0,+1]:" in out

    monkeypatch.setattr(cli, "build_llm_client", lambda settings: FakeLLM(responder=faithful))
    assert cli.main(["summarize"]) == 0
    assert "summarized=3" in capsys.readouterr().out


def test_analogues_for_unknown_document(configured, capsys) -> None:
    assert cli.main(["analogues", "00000000-0000-0000-0000-000000000000"]) == 2
    assert "not found" in capsys.readouterr().err


def test_summarize_without_llm_configuration(configured, capsys) -> None:
    assert cli.main(["summarize"]) == 2
    assert "FEDTONE_LLM__API_KEY" in capsys.readouterr().err
