# grounded-reports

LLM-written performance summaries with a deterministic gate on the numbers: every direction
word and percentage the model writes about a known metric is checked against pre-computed facts
before anything ships.

A small, runnable distillation of a pattern I run in production: compute the facts in code, let
the model write the prose, validate the claim patterns the gate knows about, and retry or fall
back when it doesn't pass. It catches the failure modes that actually show up — flipped
direction, hallucinated percentages, polarity traps — not every possible falsehood. The
[Known limitations](#known-limitations) section says exactly where it stops.

```
daily metrics ──▶ fact sheet ──▶ LLM draft ──▶ claim validator ──┬─▶ ship
                       ▲                                         │
                       └──────── retry with violations ◀─────────┘
                                                                 └─▶ (budget exhausted) deterministic fallback
```

No API key needed to try it. `--mock` runs the whole loop offline, including a deliberately
wrong first draft so you can watch the validator catch it.

## Why this exists

A narrative that says "signups grew" when they fell by 10% is worse than no narrative. The
model isn't bad at writing; it's unreliable at arithmetic and at keeping polarity straight
("cost per signup improved" means it went *down*). The fix is structural, not prompt-tuning:

1. **Ground.** Compute current vs prior, percent change, direction, and polarity *in code*.
   The model receives a fact sheet and is told to only restate it.
2. **Validate.** Parse the draft for direction words and percentages near metric mentions and
   check each one against the fact sheet. Rule-based, deterministic, free.
3. **Retry with feedback.** Violations go back to the model in the next turn. Static context is
   marked cacheable so retries are cheap.
4. **Fall back.** If the retry budget is exhausted, ship a plain template built from the facts.
   Boring, never wrong.
5. **Eval.** A fixed set of narratives with known defects keeps the validator honest as it
   evolves.

## Quickstart

```bash
git clone https://github.com/mike-larchick/grounded-reports
cd grounded-reports
python3 -m grounded_reports.cli --mock
```

You'll see the fact sheet, attempt 1 failing with a flagged sentence, attempt 2 passing, and
the final output. Then:

```bash
python3 evals/run_evals.py          # 18 validator cases (2 pinned limitations), exit 1 on any failure
pip install pytest && pytest -q     # unit tests
```

### With a real model

```bash
pip install 'anthropic>=0.40'
export ANTHROPIC_API_KEY=...
python3 -m grounded_reports.cli            # default model: claude-sonnet-4-5
python3 -m grounded_reports.cli --json     # machine-readable, includes every attempt
```

### Validate your own text

```bash
python3 -m grounded_reports.cli --validate-only my_draft.txt
```

### Bring your own data

Any CSV with `date,sessions,signups,revenue,cost` and at least 2× the comparison window:

```bash
python3 -m grounded_reports.cli --data path/to/metrics.csv --period 14 --mock
```

## As an MCP server

The same three steps are exposed as tools so an agent can call them directly.

```bash
pip install 'mcp>=1.2'
python3 -m grounded_reports.mcp_server
```

Claude Desktop / any MCP client config:

```json
{
  "mcpServers": {
    "grounded-reports": {
      "command": "python3",
      "args": ["-m", "grounded_reports.mcp_server"],
      "cwd": "/path/to/grounded-reports"
    }
  }
}
```

Tools: `fact_sheet`, `validate`, `generate_report`. An agent that drafts its own narrative can
call `validate` on it before showing it to a user.

## What the validator checks

| Claim type | Example | How it's resolved |
|---|---|---|
| Direction | "signups **grew**", "cost **eased**" | Word → up/down/flat; compared to fact sheet |
| Polarity-dependent direction | "cost per signup **improved**" | *improved* = down for lower-is-better metrics |
| Percent change | "sessions were up **16%**" | Compared to actual within ±1.5 points |
| Level, not change | "conversion rate **of 3.2%**", "from 4.1%" | Recognized by preceding word; skipped |
| Multiple metrics per sentence | "revenue climbed while CPA rose" | Each claim attaches to the nearest preceding metric |
| Negation | "signups **did not** grow" | Negator within 3 words inverts up/down; negated "flat" is dropped, not guessed |
| Percent before its metric | "a 16% rise **in sessions**" | Attaches forward when only a change-noun + preposition sits between them |

Run `python3 evals/run_evals.py` to see each of these exercised.

## Known limitations

The gate is rules, so it has a boundary. These are pinned in `evals/cases.json` as
`known_limitation: true` so they stay visible rather than quietly assumed away:

- **Spelled-out numbers.** "up forty percent" isn't parsed; a wrong value passes.
- **Implicit subjects.** "Sessions set a new high. It fell sharply." — the second sentence has
  no metric mention, so the claim can't be attributed and is skipped.
- **Metrics it doesn't know.** Anything outside the fact sheet's alias table is invisible. Safe
  for a report that must only restate given data; not a general fact-checker.
- **Causal and comparative language.** "because", "driven by", "outpaced" aren't adjudicated.
- **False positives route to the fallback.** A correct draft the rules misread ships as the
  plain template. That's the safe direction, but it lowers quality silently, so the eval set
  matters as much for what it *doesn't* flag as for what it does.

The next layer up — a model-based judge for tone, causality, and the cases above — can sit
behind this gate. It should not replace it.

## What it deliberately doesn't do

- **No second LLM as judge.** The gate is rules so it's deterministic and costs nothing per run.
  A model-based judge can layer on top for softer qualities (tone, causality) — this catches the
  hard failures first.
- **No causal claims.** The prompt forbids inferring *why* a number moved; the validator doesn't
  try to adjudicate causality.
- **Not a general fact-checker.** It knows about the metrics in the fact sheet and nothing else.
  Unknown nouns are ignored, which is the safe default for a report that must only restate given data.

## Layout

```
grounded_reports/
  facts.py        load CSV → FactSheet (current, prior, % change, direction, polarity)
  validate.py     narrative + FactSheet → ValidationResult (violations with sentence context)
  generate.py     AnthropicGenerator (cacheable system prompt) and offline MockGenerator
  fallback.py     deterministic narrative from facts
  pipeline.py     generate → validate → retry → fallback
  cli.py          command line
  mcp_server.py   FastMCP server exposing the same steps as tools
data/sample_metrics.csv   28 days of synthetic data
evals/                    cases.json + runner
tests/                    pytest
```

## Background

The production version of this pattern runs weekly against a BigQuery warehouse and ships
client-facing executive summaries with no human review step. This repo is a clean-room rewrite
on synthetic data; none of the production code, prompts, or data are included.

MIT License.
