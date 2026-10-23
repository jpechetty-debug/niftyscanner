# P1 — rule explanations and accurate empty states

User-selected P1, authorized by CONTINUE. Scope is daily swing research
explanations and presentation only. Existing technical rules, inequalities,
data gates, indicator formulas, costs, ranking and entry/exit policies stay intact.

## Additive read response

- Add `rule_explanations` per evaluated/failed ticker to research reads. Include
  ticker, vintage, decision/session, candidate/filtered/failed outcome, reasons,
  and recorded checks. Each check has a rule name, human label, strategy scope,
  observed value(s), operator, threshold(s), outcome, units and an optional note.
- Shared and strategy-specific checks use the backend's existing calculated
  values and actual outcomes. Compound comparisons explain their operands and
  aggregation. Evaluate only stages reached by the existing short-circuit flow.
- Data failures have no calculated checks. Missing/nonfinite evidence stays
  null/unavailable; there is no reconstruction from today's config for old plans.
- Keep explanation payloads separate from immutable `Plan` and `Rejection`
  schemas and the financial ledger. New evaluations retain implementation
  fingerprints; historical plans/fills/snapshots are not migrated or rewritten.

## Workspace/UI behavior

- Backend supplies counts and a view state distinguishing disabled research,
  candidates, candidates hidden by view filters, all evaluations unavailable,
  partial failures with no matching setups, valid rule rejections, an empty
  universe, and an unavailable/legacy evaluation summary.
- Normal rule rejections are never described as source failures. A missing
  validation/eligibility gate remains visible separately from technical matching.
- Show rule explanations for candidates and rejected stocks with explicit
  strategy scope. Unknown source fields never appear as passed technical rules.
  Selecting/changing a view does not trigger scans, downloads or mutations.
- UI reads only HTTP payloads, formats values and escapes source text. CSV and
  cards retain existing semantics. Synthetic fixtures remain test-only.

## Acceptance

Execute unit/API/UI serialization and presentation tests for each state, numeric
boundaries, unavailable evidence, shared/strategy short-circuiting and partial
failures. Compare old/new evaluator plan/rejection behavior on synthetic inputs.
Run swing/ledger/replay/validation regression tests after the targeted checks.
No source repair, calendar change, hourly strategy, provider request or automatic
execution is included. No new configuration defaults or dependencies are needed.
