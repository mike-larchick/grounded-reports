"""Expose the pipeline as MCP tools so an agent can call it directly.

Run:   python -m grounded_reports.mcp_server
Needs: pip install 'grounded-reports[mcp]'

Tools
  fact_sheet(csv_path, period_days)        -> structured facts
  validate(narrative, csv_path, period_days) -> violations, if any
  generate_report(csv_path, period_days, mock) -> validated narrative + attempt log
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit("mcp package not installed. `pip install 'grounded-reports[mcp]'`") from e

from .facts import build_fact_sheet, load_csv
from .generate import AnthropicGenerator, MockGenerator
from .pipeline import run_pipeline
from .validate import validate_narrative

mcp = FastMCP("grounded-reports")

_DEFAULT = str(Path(__file__).resolve().parent.parent / "data" / "sample_metrics.csv")


def _facts(csv_path: str, period_days: int):
    return build_fact_sheet(load_csv(csv_path), period_days=period_days)


@mcp.tool()
def fact_sheet(csv_path: str = _DEFAULT, period_days: int = 7) -> str:
    """Compute the fact sheet (current vs prior period) from a CSV of daily metrics."""
    return _facts(csv_path, period_days).to_json()


@mcp.tool()
def validate(narrative: str, csv_path: str = _DEFAULT, period_days: int = 7) -> str:
    """Check a narrative's direction and percent claims against the data. Returns violations."""
    result = validate_narrative(narrative, _facts(csv_path, period_days))
    return json.dumps(
        {"ok": result.ok, "checked_claims": result.checked_claims,
         "violations": [v.__dict__ for v in result.violations], "feedback": result.feedback()},
        indent=2,
    )


@mcp.tool()
def generate_report(csv_path: str = _DEFAULT, period_days: int = 7, mock: bool = True,
                    max_attempts: int = 3) -> str:
    """Generate a validated narrative. mock=True runs offline; mock=False calls Claude."""
    facts = _facts(csv_path, period_days)
    gen = MockGenerator() if mock else AnthropicGenerator()
    result = run_pipeline(facts, gen, max_attempts=max_attempts)
    return json.dumps(
        {"source": result.source, "narrative": result.narrative,
         "attempts": [{"n": a.number, "ok": a.validation.ok,
                       "violations": [str(v) for v in a.validation.violations]}
                      for a in result.attempts]},
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
