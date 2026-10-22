# S6 forward paper reconciliation contract

Register a future NSE observation window against an explicitly funded, unused S3
research account. Freeze its starting event head, configuration, implementation,
runtime, calendar sessions and minimum operational window before the first open.
No implicit capital, retrospective registration or reuse of a traded account.
Freeze a last-new-plan session before the terminal holding/entry horizon. During
that declared wind-down the operator uses S5 management-only replay; a newly
captured plan is a protocol violation. Outstanding liabilities at the end still
block review, even when a halt exceeds the declared horizon.

After each completed session, first update the source and S3 paper replay, then
explicitly capture the account and chosen immutable source vintage. A daily record
must be committed after that close and before the next session opens. Empty signal
days count only when actually captured; missed days cannot be backfilled. One
canonical capture per session is immutable. Retries return the original record;
changed sources for a sealed session are conflicts, never silent replacements.

Store in a separate append-only, hash-chained journal. Reads do not create files.
Capture incrementally freezes new ledger events/plans, exact-session execution
observations, source audit, NAV and all reconciliation failures. Verify the S3
chain and its continuation from the preceding captured head. Journal capture
never funds, trades, cancels, repairs or rewrites the paper ledger or source.
Late captures and data revisions remain visible failures, not repaired winners.

Reconcile recorded entry intervals/reservations against subsequently observed raw
opens, identity, actions and explicit side capacity. Independently verify paper
fill quantities, cash/gross/cost arithmetic and exit reference rules against
observed OHLC and the prior event-reconstructed position. Daily OHLC cannot prove
intraday sequencing or an actual market fill. Compare *simulated fills* with
observed references; never label these broker-confirmed fills. Missing basis,
capacity, marks, benchmark or source evidence is UNRESOLVED, not a zero return or
an assumed fill. No-fill reasons and data failures stay separate.

Operational readiness requires every declared session recorded and reconciled,
at least SWING_FORWARD_MIN_SESSIONS=30 actual sessions (cannot be configured below
30), no integrity/data gaps or unresolved liabilities. An incomplete final book
blocks review. Passing this operational gate is not a profitability test. Reports
always retain RESEARCH_ONLY, paper_eligible=false and promotion_allowed=false;
independent frozen S4 statistical/stress/holdout gates remain required. No automatic
promotion, strategy retuning or opening of a holdout.

SWING_FORWARD_DB_PATH=data/swing_forward.db is isolated from all source, paper,
validation, operations and legacy stores/artifacts. Explicit CLI register/capture/
report/verify performs bounded local I/O only. Capture has no hidden network or
timer: the operator runs it daily after the existing replay worker completes.
HTTP/UI are read-only monitors, off-loop, and exclude synthetic campaigns. This
phase delivers tooling; 30 future sessions cannot be completed in an agent turn.
