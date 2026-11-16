from pathlib import Path

import pytest

from grounded_reports import build_fact_sheet, load_csv, run_pipeline, validate_narrative
from grounded_reports.fallback import fallback_narrative
from grounded_reports.generate import MockGenerator

DATA = Path(__file__).resolve().parent.parent / "data" / "sample_metrics.csv"


@pytest.fixture(scope="module")
def facts():
    return build_fact_sheet(load_csv(DATA), period_days=7)


def test_fact_sheet_directions(facts):
    assert facts.get("sessions").direction == "up"
    assert facts.get("signups").direction == "down"
    assert facts.get("cost_per_signup").direction == "up"
    assert facts.get("conversion_rate").direction == "down"


def test_validator_catches_flipped_direction(facts):
    r = validate_narrative("Signups grew this week.", facts)
    assert not r.ok
    assert r.violations[0].kind == "direction_mismatch"


def test_validator_respects_polarity(facts):
    # cost per signup went UP, so "improved" is wrong and "worsened" is right
    assert not validate_narrative("Cost per signup improved.", facts).ok
    assert validate_narrative("Cost per signup worsened.", facts).ok


def test_validator_catches_wrong_percent(facts):
    r = validate_narrative("Sessions rose 40% week over week.", facts)
    assert not r.ok
    assert r.violations[0].kind == "percent_mismatch"


def test_fallback_always_validates(facts):
    assert validate_narrative(fallback_narrative(facts), facts).ok


def test_pipeline_retries_then_passes(facts):
    result = run_pipeline(facts, MockGenerator(), max_attempts=3)
    assert result.source == "llm"
    assert len(result.attempts) == 2
    assert not result.attempts[0].validation.ok
    assert result.attempts[1].validation.ok


def test_pipeline_falls_back_when_budget_exhausted(facts):
    always_wrong = lambda f, fb, n: "Signups grew and cost per signup improved."  # noqa: E731
    result = run_pipeline(facts, always_wrong, max_attempts=2)
    assert result.source == "fallback"
    assert len(result.attempts) == 2
    assert validate_narrative(result.narrative, facts).ok
