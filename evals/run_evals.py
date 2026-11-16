"""Run the validator against a fixed set of narratives with known defects.

    python evals/run_evals.py

Exit code is non-zero if any case fails, so this can gate CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grounded_reports import build_fact_sheet, load_csv, validate_narrative  # noqa: E402


def main() -> int:
    facts = build_fact_sheet(load_csv(ROOT / "data" / "sample_metrics.csv"), period_days=7)
    cases = json.loads((ROOT / "evals" / "cases.json").read_text())

    failures = 0
    print(f"{'case':34} {'expected':10} {'got':10} result")
    print("─" * 72)
    for c in cases:
        r = validate_narrative(c["narrative"], facts)
        kinds = sorted({v.kind for v in r.violations})
        ok_match = r.ok == c["expect_ok"]
        kinds_match = kinds == sorted(set(c["expect_kinds"]))
        passed = ok_match and kinds_match
        failures += 0 if passed else 1
        print(
            f"{c['id']:34} {'ok' if c['expect_ok'] else 'violate':10} "
            f"{'ok' if r.ok else 'violate':10} {'PASS' if passed else 'FAIL'}"
        )
        if not passed:
            print(f"    expected kinds {c['expect_kinds']}, got {kinds}")
            for v in r.violations:
                print(f"    {v}")

    print("─" * 72)
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
