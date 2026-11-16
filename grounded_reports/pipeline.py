"""Orchestration: generate -> validate -> retry with feedback -> fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .facts import FactSheet
from .fallback import fallback_narrative
from .generate import Generator
from .validate import ValidationResult, validate_narrative


@dataclass
class Attempt:
    number: int
    narrative: str
    validation: ValidationResult


@dataclass
class PipelineResult:
    narrative: str
    source: Literal["llm", "fallback"]
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.source == "llm"


def run_pipeline(
    facts: FactSheet,
    generator: Generator,
    max_attempts: int = 3,
) -> PipelineResult:
    attempts: list[Attempt] = []
    feedback: str | None = None

    for n in range(1, max_attempts + 1):
        draft = generator(facts, feedback, n)
        result = validate_narrative(draft, facts)
        attempts.append(Attempt(number=n, narrative=draft, validation=result))
        if result.ok:
            return PipelineResult(narrative=draft, source="llm", attempts=attempts)
        feedback = result.feedback()

    # Retry budget exhausted: ship the boring-but-correct version.
    return PipelineResult(narrative=fallback_narrative(facts), source="fallback", attempts=attempts)
