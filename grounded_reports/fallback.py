"""Deterministic narrative built straight from the fact sheet.

Used when the LLM cannot produce a narrative that passes validation within the retry
budget. It is plain, but it is never wrong — which is the property that matters when
the output goes to a reader without human review.
"""

from __future__ import annotations

from .facts import FactSheet, Metric

_VERB = {"up": "increased", "down": "decreased", "flat": "was roughly flat"}


def _sentence(m: Metric) -> str:
    if m.direction == "flat":
        return f"{m.label} {_VERB['flat']} at {m.fmt(m.current)} (prior period {m.fmt(m.prior)})."
    return (
        f"{m.label} {_VERB[m.direction]} {abs(m.pct_change):.1f}% to {m.fmt(m.current)} "
        f"from {m.fmt(m.prior)}."
    )


def fallback_narrative(facts: FactSheet) -> str:
    header = (
        f"Summary for {facts.current_start} to {facts.current_end} compared with the prior "
        f"{facts.period_days} days."
    )
    volume = " ".join(_sentence(facts.get(k)) for k in ("sessions", "signups", "revenue", "cost"))
    efficiency = " ".join(
        _sentence(facts.get(k)) for k in ("cost_per_signup", "conversion_rate")
    )
    return f"{header}\n\n{volume}\n\n{efficiency}"
