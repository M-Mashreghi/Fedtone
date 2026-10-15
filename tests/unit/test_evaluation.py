from datetime import UTC, datetime
from pathlib import Path

import pytest

from fedtone.application.evaluation import load_dataset, run_benchmark
from fedtone.application.llm import LLMTransientError
from fedtone.application.tone_analysis import ToneAnalyzer
from tests.fake_llm import FakeLLM, tone_json

DATASET = Path(__file__).parents[1] / "fixtures" / "eval" / "mini_benchmark.json"
NOW = datetime(2024, 5, 1, tzinfo=UTC)


def test_dataset_loads() -> None:
    items = load_dataset(DATASET)

    assert [i.id for i in items] == [
        "synthetic-hawkish-1",
        "synthetic-neutral-1",
        "synthetic-dovish-1",
    ]


def test_perfect_stable_model_scores_full_agreement() -> None:
    llm = FakeLLM(
        scores={"well above": (0.7, 0.9), "roughly in balance": (0.0, 0.9), "weakened": (-0.4, 0.9)}
    )

    report = run_benchmark(
        ToneAnalyzer(llm), load_dataset(DATASET), model="fake", prompt_version="p", now=NOW
    )

    m = report.metrics
    assert m["items_scored"] == 3
    assert m["direction_agreement"] == 1.0
    assert m["label_agreement"] == 1.0
    assert m["mean_score_range"] == 0.0
    assert m["rejected_run_rate"] == 0.0
    assert m["runs"] == 9


def test_instability_and_failures_are_measured() -> None:
    dataset = load_dataset(DATASET)[:1]
    text = " ".join(dataset[0].paragraphs)
    llm = FakeLLM(
        script=[
            tone_json(text, score=0.7),
            tone_json(text, score=0.3),
            "garbage",
            "garbage",
            LLMTransientError("timeout"),
        ]
    )

    report = run_benchmark(
        ToneAnalyzer(llm), dataset, model="fake", prompt_version="p", repeats=4, now=NOW
    )

    m = report.metrics
    item = report.items[0]
    assert item.scores == [0.7, 0.3]
    assert item.score_range == pytest.approx(0.4)
    assert item.predicted_label == "hawkish"  # mean 0.5
    assert m["direction_agreement"] == 1.0
    assert m["label_agreement"] == 0.0  # expected strongly_hawkish
    assert m["schema_failure_rate"] == 0.25
    assert m["provider_failure_rate"] == 0.25
    assert m["rejected_run_rate"] == 0.5


def test_evidence_failures_are_counted_separately() -> None:
    dataset = load_dataset(DATASET)[:1]
    text = " ".join(dataset[0].paragraphs)
    fabricated = tone_json(text, quote="The Committee will raise rates by a full point.")

    report = run_benchmark(
        ToneAnalyzer(FakeLLM(script=[fabricated, fabricated])),
        dataset,
        model="fake",
        prompt_version="p",
        repeats=1,
        now=NOW,
    )

    assert report.metrics["evidence_failure_rate"] == 1.0
    assert report.metrics["direction_agreement"] is None


def test_report_renders_json_and_markdown() -> None:
    report = run_benchmark(
        ToneAnalyzer(FakeLLM()),
        load_dataset(DATASET),
        model="fake",
        prompt_version="tone-statement-v1",
        repeats=1,
        now=NOW,
    )

    data = report.to_dict()
    markdown = report.to_markdown()

    assert data["metrics"]["items"] == 3
    assert "| direction_agreement |" in markdown
    assert "synthetic-dovish-1" in markdown
