"""CLI: python -m grounded_reports.cli --data data/sample_metrics.csv --mock"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .facts import build_fact_sheet, load_csv
from .generate import DEFAULT_MODEL, AnthropicGenerator, MockGenerator
from .pipeline import run_pipeline
from .validate import validate_narrative

HR = "─" * 72


def _default_data() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "sample_metrics.csv"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grounded-reports")
    ap.add_argument("--data", type=Path, default=_default_data(), help="CSV of daily metrics")
    ap.add_argument("--period", type=int, default=7, help="comparison window in days")
    ap.add_argument("--mock", action="store_true", help="run offline with the mock generator")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--validate-only", type=Path, metavar="FILE",
                    help="skip generation; validate the narrative in FILE against the data")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args(argv)

    facts = build_fact_sheet(load_csv(args.data), period_days=args.period)

    if args.validate_only:
        text = args.validate_only.read_text()
        result = validate_narrative(text, facts)
        if args.json:
            print(json.dumps({"ok": result.ok, "checked": result.checked_claims,
                              "violations": [v.__dict__ for v in result.violations]}, indent=2))
        else:
            print(result.feedback())
            print(f"\nchecked {result.checked_claims} claims")
        return 0 if result.ok else 1

    generator = MockGenerator() if args.mock else AnthropicGenerator(model=args.model)
    result = run_pipeline(facts, generator, max_attempts=args.max_attempts)

    if args.json:
        print(json.dumps({
            "facts": facts.to_dict(),
            "source": result.source,
            "narrative": result.narrative,
            "attempts": [
                {"n": a.number, "ok": a.validation.ok,
                 "violations": [str(v) for v in a.validation.violations],
                 "narrative": a.narrative}
                for a in result.attempts
            ],
        }, indent=2, default=str))
        return 0

    print(HR); print("FACT SHEET"); print(HR)
    print(facts.to_prompt_text())
    for a in result.attempts:
        print(); print(HR)
        status = "PASS" if a.validation.ok else f"FAIL ({len(a.validation.violations)} violation(s))"
        print(f"ATTEMPT {a.number} — {status}, {a.validation.checked_claims} claims checked")
        print(HR)
        print(a.narrative)
        if not a.validation.ok:
            print()
            for v in a.validation.violations:
                print(f"  ✗ {v}")
    print(); print(HR)
    print(f"FINAL  (source: {result.source})")
    print(HR)
    print(result.narrative)
    return 0


if __name__ == "__main__":
    sys.exit(main())
