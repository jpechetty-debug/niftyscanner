# Daily breakout chart context

Authorized P1 chart improvement, 22 October 2026. Preserve the existing daily
breakout/pullback calculations, plan schema, entry/stop/target levels, evidence
gates and request-scoped verification. No provider, calendar, worker or financial
mutation is part of this phase.

Add optional typed `chart_contexts` to research responses. Each daily breakout
context is bound to a specific plan ID, ticker, signal/decision, known basis,
vintage, configuration hash and consumed-data hash. Capture the resistance level
from the evaluated rule check, the prior window's exact dates, frozen volume
baseline and signal volume, and ATR extension values/limits. Use the plan's frozen
configuration and features, never current UI settings. Both prior windows exclude
the signal session. No context is produced for missing data or pullback plans.
This metadata is outside immutable Plan/ledger records; no historical rewrite.

On compatible charts, shade the recorded resistance window and show its level,
the breakout close ceiling, a signal marker, and a volume pane with the recorded
baseline. Explain both resistance-relative breakout ATR extension and the shared
fast-EMA extension. Keep the four existing entry/stop/target lines. These are
recorded signal-session rules, not moving indicators or projected entry prices.

The UI renders HTTP metadata only; it does not calculate resistance, rolling
volume or ATR from the displayed chart subset. Bind every overlay to matching
ticker/vintage/known basis; bind new context to the exact plan identity/hashes and
signal/decision. Missing/malformed/incompatible context is explicitly unavailable.
Old payloads remain loadable. Unknown-basis source charts stay inspection-only.
Clip visual windows to displayed bars without filling missing dates or extending
the configured observation limit. Captions retain the full recorded dates/counts.

Verify numerical provenance and window exclusion, API compatibility, mismatch and
missing-value handling, no-lookahead behavior, existing level preservation and
HTTP-only UI integration. Synthetic fixtures remain isolated tests; browser checks
use only the actual archive, with workers and mutations disabled. Current source
gaps may prevent a populated real candidate chart; report that limitation.
