"""Narrative generators.

Two implementations share one interface:

    generator(facts, feedback: str | None, attempt: int) -> str

* AnthropicGenerator — real LLM call. Fact sheet goes in the system prompt (cacheable);
  validation feedback from the previous attempt goes in the user turn.
* MockGenerator — no network. Attempt 1 deliberately flips one direction so you can
  watch the validator catch it; attempt 2 is correct. Lets the whole loop run offline.
"""

from __future__ import annotations

import os
from typing import Protocol

from .facts import FactSheet, Metric

DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM_TEMPLATE = """You write short performance summaries for a non-technical reader.

RULES
- Every number and every direction word you use must come from the FACT SHEET below.
- Do not compute anything yourself. Do not infer causes. Do not add metrics that are not listed.
- A metric marked DOWN did not "grow", "rise", or "improve" (unless it is a lower-is-better
  metric, in which case DOWN *is* an improvement — read the "Better when" column).
- If a change is marked FLAT, describe it as flat or steady; do not pick a direction.
- Two paragraphs, 3–4 sentences each. First paragraph: volume (sessions, signups, revenue, cost).
  Second paragraph: efficiency (cost per signup, conversion rate). Plain English, no bullet points.

FACT SHEET
{facts}
"""

USER_FIRST = "Write the summary."

USER_RETRY = """Your previous draft failed validation.

{feedback}

Rewrite the full summary so that every claim matches the fact sheet. Do not mention this correction."""


class Generator(Protocol):
    def __call__(self, facts: FactSheet, feedback: str | None, attempt: int) -> str: ...


# ---------------------------------------------------------------------------
# Real model
# ---------------------------------------------------------------------------

class AnthropicGenerator:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 600):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "anthropic package not installed. `pip install 'grounded-reports[llm]'`"
            ) from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def __call__(self, facts: FactSheet, feedback: str | None, attempt: int) -> str:
        system = SYSTEM_TEMPLATE.format(facts=facts.to_prompt_text())
        user = USER_FIRST if feedback is None else USER_RETRY.format(feedback=feedback)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # Static context is marked cacheable; only the user turn changes between retries.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


# ---------------------------------------------------------------------------
# Offline mock
# ---------------------------------------------------------------------------

_UP = {"sessions": "rose", "signups": "grew", "revenue": "climbed", "cost": "increased",
       "cost_per_signup": "rose", "conversion_rate": "improved"}
_DOWN = {"sessions": "fell", "signups": "declined", "revenue": "slipped", "cost": "eased",
         "cost_per_signup": "improved", "conversion_rate": "softened"}


def _phrase(m: Metric, flip: bool = False) -> str:
    d = m.direction
    if flip and d != "flat":
        d = "down" if d == "up" else "up"
    if d == "flat":
        return f"{m.label} held steady at {m.fmt(m.current)}"
    verb = (_UP if d == "up" else _DOWN)[m.key]
    return f"{m.label} {verb} {abs(m.pct_change):.1f}% to {m.fmt(m.current)}"


class MockGenerator:
    """Deterministic stand-in. Flips the direction of `flip_key` on attempt 1 only."""

    def __init__(self, flip_key: str = "signups"):
        self.flip_key = flip_key

    def __call__(self, facts: FactSheet, feedback: str | None, attempt: int) -> str:
        flip = attempt == 1

        def p(key: str) -> str:
            m = facts.get(key)
            return _phrase(m, flip=(flip and key == self.flip_key))

        para1 = (
            f"A busier period overall. {p('sessions')} week over week, and {p('signups')}. "
            f"{p('revenue')}, while {p('cost')}."
        )
        para2 = (
            f"On efficiency, {p('cost_per_signup')}. {p('conversion_rate')}, which is the "
            f"number to watch next period."
        )
        return f"{para1}\n\n{para2}"
