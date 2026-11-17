"""Validate an LLM-written narrative against the fact sheet.

Two classes of claim are checked:

1. DIRECTION  — "signups rose", "cost fell", "CPA improved". The direction word is
   resolved to up/down, taking metric polarity into account ("improved" means DOWN
   for a cost metric), and compared to the fact sheet.
2. PERCENT    — "sessions were up 16.2%". The stated percentage is compared to the
   actual change within a tolerance.

This is intentionally rule-based. The point is that the gate is deterministic and
cheap, so it can run on every generation with no second model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .facts import FactSheet, Metric

ViolationKind = Literal["direction_mismatch", "percent_mismatch"]

# Longest alias first so "cost per signup" wins over "cost" and "signup".
METRIC_ALIASES: dict[str, list[str]] = {
    "cost_per_signup": [
        "cost per signup", "cost per sign-up", "cost per acquisition", "cost-per-signup",
        "cpa", "cps", "acquisition cost",
    ],
    "conversion_rate": ["conversion rate", "signup rate", "sign-up rate", "conversion %"],
    "sessions": ["sessions", "traffic", "visits", "site visits"],
    "signups": ["signups", "sign-ups", "sign ups", "registrations", "conversions", "new accounts"],
    "revenue": ["revenue", "sales", "bookings"],
    "cost": ["cost", "spend", "ad spend", "investment"],
}

UP_WORDS = [
    "rose", "rise", "rising", "increased", "increase", "increasing", "grew", "grow", "growth",
    "climbed", "climb", "jumped", "jump", "up", "higher", "expanded", "gained", "gain",
    "accelerated", "surged",
]
DOWN_WORDS = [
    "fell", "fall", "falling", "decreased", "decrease", "declined", "decline", "declining",
    "dropped", "drop", "down", "lower", "eased", "softened", "pulled back", "slipped",
    "contracted", "shrank", "dipped", "slowed",
]
# Polarity-dependent: for lower_is_better metrics these invert.
BETTER_WORDS = ["improved", "improvement", "strengthened", "healthier", "better", "more efficient"]
WORSE_WORDS = ["worsened", "weakened", "deteriorated", "worse", "less efficient"]
FLAT_WORDS = ["flat", "unchanged", "steady", "held steady", "stable", "level"]

# "did not grow", "didn't fall", "never improved", "no increase" — a negator within a few
# words before a direction word inverts it (up <-> down). A negated "flat" is ambiguous
# ("not flat" = moved, direction unknown) and is dropped rather than guessed.
NEGATORS = ["not", "n't", "never", "no", "without", "hardly", "barely"]
NEGATION_WINDOW_WORDS = 3

# "a 16% rise in sessions" — percent precedes its metric, joined by a change noun + preposition.
PCT_FORWARD_RE = re.compile(
    r"^\s*(?:rise|increase|gain|growth|jump|lift|improvement|drop|decline|decrease|fall|"
    r"pullback|reduction|change|swing)?\s*(?:in|for|to|on)\s*$",
    re.IGNORECASE,
)

PCT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s?%")
PCT_TOLERANCE_POINTS = 1.5


@dataclass
class Violation:
    kind: ViolationKind
    metric: str
    sentence: str
    claimed: str
    actual: str

    def __str__(self) -> str:
        return (
            f"[{self.kind}] {self.metric}: claimed '{self.claimed}', actual '{self.actual}' "
            f"— in: \"{self.sentence.strip()}\""
        )


@dataclass
class ValidationResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    checked_claims: int = 0

    def feedback(self) -> str:
        """Human/LLM-readable list of what to fix, suitable for a retry prompt."""
        if self.ok:
            return "All claims consistent with the fact sheet."
        lines = ["The following claims contradict the fact sheet and must be corrected:"]
        for v in self.violations:
            lines.append(f"- {v}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tokenizing helpers
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    return [s for s in _SENT_SPLIT.split(text) if s.strip()]


def _find_all(haystack: str, needles: list[str]) -> list[tuple[int, str]]:
    """Return (position, needle) for whole-word matches, lowercase comparison."""
    out: list[tuple[int, str]] = []
    low = haystack.lower()
    for n in needles:
        for m in re.finditer(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", low):
            out.append((m.start(), n))
    return out


def _metric_mentions(sentence: str) -> list[tuple[int, int, str]]:
    """(start, end, metric_key) for each alias hit; longer aliases suppress overlaps."""
    hits: list[tuple[int, int, str]] = []
    low = sentence.lower()
    ordered = sorted(
        ((alias, key) for key, aliases in METRIC_ALIASES.items() for alias in aliases),
        key=lambda t: -len(t[0]),
    )
    taken: list[tuple[int, int]] = []
    for alias, key in ordered:
        for m in re.finditer(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", low):
            s, e = m.start(), m.end()
            if any(s < te and e > ts for ts, te in taken):
                continue
            taken.append((s, e))
            hits.append((s, e, key))
    hits.sort()
    return hits


def _is_negated(sentence: str, pos: int) -> bool:
    """True if a negator appears within NEGATION_WINDOW_WORDS before `pos`."""
    before = sentence[:pos].lower()
    tail = re.findall(r"[a-z']+", before)[-NEGATION_WINDOW_WORDS:]
    return any(n in w or w == n for w in tail for n in NEGATORS)


def _direction_claims(sentence: str, metric: Metric) -> list[tuple[int, str]]:
    """(position, resolved_direction) for every direction word in the sentence.

    Negation inverts up/down ("did not grow" -> down). A negated flat is dropped.
    """
    raw: list[tuple[int, str]] = []
    for pos, _ in _find_all(sentence, UP_WORDS):
        raw.append((pos, "up"))
    for pos, _ in _find_all(sentence, DOWN_WORDS):
        raw.append((pos, "down"))
    for pos, _ in _find_all(sentence, FLAT_WORDS):
        raw.append((pos, "flat"))
    better_means = "up" if metric.polarity == "higher_is_better" else "down"
    worse_means = "down" if better_means == "up" else "up"
    for pos, _ in _find_all(sentence, BETTER_WORDS):
        raw.append((pos, better_means))
    for pos, _ in _find_all(sentence, WORSE_WORDS):
        raw.append((pos, worse_means))

    claims: list[tuple[int, str]] = []
    for pos, d in raw:
        if _is_negated(sentence, pos):
            if d == "flat":
                continue
            d = "down" if d == "up" else "up"
        claims.append((pos, d))
    claims.sort()
    return claims


def _nearest_metric(pos: int, mentions: list[tuple[int, int, str]]) -> str | None:
    """Attach a claim to the closest metric mention, preferring the one before it."""
    if not mentions:
        return None
    before = [m for m in mentions if m[0] <= pos]
    if before:
        return max(before, key=lambda m: m[0])[2]
    return min(mentions, key=lambda m: abs(m[0] - pos))[2]


def _attach_percent(sentence: str, pct_end: int, mentions: list[tuple[int, int, str]]) -> str | None:
    """Percent claims usually follow their metric ("sessions rose 16%"), but "a 16% rise in
    sessions" puts the metric after. If the text between the percent and the next metric is
    just a change-noun + preposition, attach forward; otherwise fall back to nearest-before."""
    after = [m for m in mentions if m[0] >= pct_end]
    if after:
        nxt = min(after, key=lambda m: m[0])
        if PCT_FORWARD_RE.match(sentence[pct_end:nxt[0]]):
            return nxt[2]
    return _nearest_metric(pct_end, mentions)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_narrative(narrative: str, facts: FactSheet) -> ValidationResult:
    violations: list[Violation] = []
    checked = 0

    for sentence in split_sentences(narrative):
        mentions = _metric_mentions(sentence)
        if not mentions:
            continue

        # Direction claims. A sentence can mention several metrics ("revenue climbed
        # while CPA rose"), so each direction word is attached to the nearest metric
        # mention before it. Polarity words ("improved") resolve differently per metric,
        # so we scan once per mentioned metric and keep only the claims attached to it.
        seen: set[tuple[str, int]] = set()
        for _, _, key in mentions:
            metric = facts.get(key)
            for pos, claimed_dir in _direction_claims(sentence, metric):
                attached = _nearest_metric(pos, mentions)
                if attached != key or (attached, pos) in seen:
                    continue
                seen.add((attached, pos))
                checked += 1
                actual = metric.direction
                # Allow "flat" language when the real change is small, and vice versa.
                if claimed_dir != actual and not (
                    actual == "flat" and claimed_dir in ("up", "down")
                    and metric.pct_change is not None and abs(metric.pct_change) <= 3.0
                ):
                    violations.append(
                        Violation(
                            kind="direction_mismatch",
                            metric=metric.label,
                            sentence=sentence,
                            claimed=claimed_dir,
                            actual=f"{actual} ({metric.pct_text})",
                        )
                    )

        # Percent claims, each attached to the nearest metric.
        for m in PCT_RE.finditer(sentence):
            attached = _attach_percent(sentence, m.end(), mentions)
            if attached is None:
                continue
            metric = facts.get(attached)
            if metric.pct_change is None:
                continue
            # Skip percentages that are describing a percent-unit metric's *level*
            # (e.g. "conversion rate of 3.2%") rather than its change.
            window = sentence[max(0, m.start() - 12): m.start()].lower()
            if metric.unit == "percent" and re.search(
                r"\b(of|at|to|from|was|is|versus|vs\.?)\s*$", window
            ):
                continue
            checked += 1
            stated = float(m.group(1))
            if abs(stated - abs(metric.pct_change)) > PCT_TOLERANCE_POINTS:
                violations.append(
                    Violation(
                        kind="percent_mismatch",
                        metric=metric.label,
                        sentence=sentence,
                        claimed=f"{stated:g}%",
                        actual=metric.pct_text,
                    )
                )

    return ValidationResult(ok=not violations, violations=violations, checked_claims=checked)
