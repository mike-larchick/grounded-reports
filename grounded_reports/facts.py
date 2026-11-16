"""Build a structured fact sheet from raw daily metrics.

The fact sheet is the single source of truth for everything the model is allowed to say.
It is computed deterministically *before* any LLM call and injected as context, so the
model never has to do arithmetic or infer direction on its own.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal

Direction = Literal["up", "down", "flat"]
Polarity = Literal["higher_is_better", "lower_is_better"]

# Change smaller than this (in percent) is reported as "flat" so the model isn't
# tempted to narrate noise as a trend.
FLAT_THRESHOLD_PCT = 1.0


@dataclass
class Metric:
    key: str
    label: str
    current: float
    prior: float
    pct_change: float | None  # None when prior == 0
    direction: Direction
    polarity: Polarity
    unit: Literal["count", "currency", "percent", "ratio"] = "count"

    def fmt(self, value: float) -> str:
        if self.unit == "currency":
            return f"${value:,.0f}" if value >= 100 else f"${value:,.2f}"
        if self.unit == "percent":
            return f"{value:.1f}%"
        if self.unit == "ratio":
            return f"{value:,.2f}"
        return f"{value:,.0f}"

    @property
    def pct_text(self) -> str:
        if self.pct_change is None:
            return "n/a (no prior data)"
        sign = "+" if self.pct_change > 0 else ""
        return f"{sign}{self.pct_change:.1f}%"


@dataclass
class FactSheet:
    period_days: int
    current_start: date
    current_end: date
    prior_start: date
    prior_end: date
    metrics: list[Metric] = field(default_factory=list)

    def get(self, key: str) -> Metric:
        for m in self.metrics:
            if m.key == key:
                return m
        raise KeyError(key)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("current_start", "current_end", "prior_start", "prior_end"):
            d[k] = d[k].isoformat()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_prompt_text(self) -> str:
        """Compact, unambiguous rendering for the system prompt."""
        lines = [
            f"Comparison window: {self.period_days} days.",
            f"Current period: {self.current_start} to {self.current_end}.",
            f"Prior period:   {self.prior_start} to {self.prior_end}.",
            "",
            "Metric | Current | Prior | Change | Direction | Better when",
        ]
        for m in self.metrics:
            lines.append(
                f"{m.label} | {m.fmt(m.current)} | {m.fmt(m.prior)} | {m.pct_text} | "
                f"{m.direction.upper()} | {'higher' if m.polarity == 'higher_is_better' else 'lower'}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ("date", "sessions", "signups", "revenue", "cost")


def load_csv(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")
        for r in reader:
            rows.append(
                {
                    "date": datetime.strptime(r["date"], "%Y-%m-%d").date(),
                    "sessions": float(r["sessions"]),
                    "signups": float(r["signups"]),
                    "revenue": float(r["revenue"]),
                    "cost": float(r["cost"]),
                }
            )
    rows.sort(key=lambda r: r["date"])
    return rows


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _sum(rows: Iterable[dict], key: str) -> float:
    return float(sum(r[key] for r in rows))


def _pct(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / prior * 100.0


def _direction(pct: float | None) -> Direction:
    if pct is None:
        return "flat"
    if pct > FLAT_THRESHOLD_PCT:
        return "up"
    if pct < -FLAT_THRESHOLD_PCT:
        return "down"
    return "flat"


def _metric(key, label, current, prior, polarity, unit="count") -> Metric:
    pct = _pct(current, prior)
    return Metric(
        key=key,
        label=label,
        current=current,
        prior=prior,
        pct_change=pct,
        direction=_direction(pct),
        polarity=polarity,
        unit=unit,
    )


def build_fact_sheet(rows: list[dict], period_days: int = 7) -> FactSheet:
    """Compare the trailing `period_days` against the `period_days` before that."""
    if len(rows) < period_days * 2:
        raise ValueError(
            f"Need at least {period_days * 2} days of data for a {period_days}-day comparison; "
            f"got {len(rows)}."
        )
    current = rows[-period_days:]
    prior = rows[-period_days * 2 : -period_days]

    sessions_c, sessions_p = _sum(current, "sessions"), _sum(prior, "sessions")
    signups_c, signups_p = _sum(current, "signups"), _sum(prior, "signups")
    revenue_c, revenue_p = _sum(current, "revenue"), _sum(prior, "revenue")
    cost_c, cost_p = _sum(current, "cost"), _sum(prior, "cost")

    cps_c = cost_c / signups_c if signups_c else 0.0
    cps_p = cost_p / signups_p if signups_p else 0.0
    cr_c = signups_c / sessions_c * 100 if sessions_c else 0.0
    cr_p = signups_p / sessions_p * 100 if sessions_p else 0.0

    metrics = [
        _metric("sessions", "Sessions", sessions_c, sessions_p, "higher_is_better"),
        _metric("signups", "Signups", signups_c, signups_p, "higher_is_better"),
        _metric("revenue", "Revenue", revenue_c, revenue_p, "higher_is_better", "currency"),
        _metric("cost", "Cost", cost_c, cost_p, "lower_is_better", "currency"),
        _metric("cost_per_signup", "Cost per signup", cps_c, cps_p, "lower_is_better", "currency"),
        _metric("conversion_rate", "Conversion rate", cr_c, cr_p, "higher_is_better", "percent"),
    ]

    return FactSheet(
        period_days=period_days,
        current_start=current[0]["date"],
        current_end=current[-1]["date"],
        prior_start=prior[0]["date"],
        prior_end=prior[-1]["date"],
        metrics=metrics,
    )
