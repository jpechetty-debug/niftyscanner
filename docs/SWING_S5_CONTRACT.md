# S5 Trade Ideas UI and operations contract (v1)

S5 adds a compact Streamlit workspace alongside the existing screener, using only
HTTP to FastAPI. NSE daily swing hypotheses remain RESEARCH_ONLY; the current S1
archive has no verified Nifty 500/price basis and produces no eligible ideas.
Empty, disabled, unavailable, stale and unverified states must remain explicit.
No fabricated examples appear in a real API/UI. No broker or model-promotion API.

## Read contracts

New `/api/swing` reads provide visible real vintages, workspace results/source
coverage, observed reference charts, filtered CSV, existing research accounts and
validation-report summaries/details. All SQLite/hash/research I/O runs off-loop.
Reads never initialize missing databases, download providers, fund accounts or
open terminal holdouts. A selected vintage is explicit; synthetic artifacts are
excluded from live discovery. The source must be available at the injected Clock.
Unknown basis charts are labelled inspection only; gaps remain gaps. Levels are
overlaid only when chart/plan basis and vintage match and are known.

Shared server view filters (setup, sector, ticker/name search) and stable priority
drive cards, detail selection, allocation preview and CSV. Export retains research
status, lifecycle, decision/session, evidence vintage, basis, entry/stop/target,
net RR, missing gates and nullable quantity. No account means no invented size.
The UI downloads CSV bundled in the same workspace HTTP response as its cards,
so a later clock tick cannot silently change the exported view. The standalone CSV
endpoint is an explicit fresh read with the same filters, not a historical view ID.
Risk previews require an existing reconciled account; stale/unresolved books fail
closed. Filtered business rejections and failed data remain distinct.

Paper results show net realised cash flows, unresolved liabilities and exact NAV
marks; missing NAV is not carried forward. S4 validation stays a separate signed/
unsigned report view; reading it cannot open a holdout or change plan eligibility.
Actual scheduler, nightly outcomes, replay worker and feature-flag states appear
in Operations. A stopped worker is never labelled running or scheduled.

## Operations, reconciliation and rollback

Use a separate `SWING_OPS_DB_PATH=data/swing_ops.db`, with validated schema,
append-only revisioned controls/receipts and durable lifecycle-alert outbox. Default
new-work pause is false to preserve S3 opt-in flags; alerts default paused.
Every control mutation requires an idempotency key and expected control revision.
Rollback selects a previously recorded control revision; it never rewrites plans,
cash, fills or model parameters, and forces new work/alerts paused for review.
Operators explicitly resume after reviewing the restored selection. Existing
source/data/legacy/paper/validation stores cannot be reused as the operations DB.

Pause blocks new replay acceptance and unstarted new-work jobs. A worker already
holding the shared scan lock completes before a pause is acknowledged. Control
changes use that same lock and return 429/retry_after when it is occupied. This
prevents a pause acknowledgement racing an opening fill. Reads and known unfilled
cancellation remain available, preserving unresolved/open liabilities.

An explicit management-only replay can update existing holdings while paused:
no new plans, reservations or entries. Known unfilled candidates/reservations are
cancelled with an explicit management-only reason; unresolved liabilities remain.
Existing positions use unchanged S3 action/exit/mark rules. Management-only is
clearly research simulation, never a claim of historical operator intervention.
Global swing/replay opt-in flags still apply. Durable jobs remain queued across
pause/restart; version and original command retry semantics are retained.

## Lifecycle notifications

Separate `SWING_LIFECYCLE_WEBHOOK_URL=` defaults empty, never implicitly reuse the
legacy scanner destination. A user-configured destination and explicit unpause
are required. Empty/paused configuration performs no external POSTs. Alerts omit
capital/cash, quantities and private account names; they include stable event ID,
ticker, lifecycle, research/delayed-data notice and event session only.

Collect newly committed lifecycle events into an idempotent outbox by account and
source sequence, checking ledger chains and storing the source event hash. Only
new events after opting in are collected; enabling must not replay old history.
Claim before POST, include an Idempotency-Key, then persist delivery receipt.
Crashes/timeouts after claim remain UNKNOWN, not automatically resent. Explicit
review/retry keeps the same event ID; recipients must honor it for network-level
deduplication. Outbox items are bound to their original destination fingerprint;
changed URLs never redirect prior events. Exactly-once remote delivery is not promised. Notification failure
never changes financial jobs or scanner success. Dispatch shares the existing
scheduler, never adds a new scheduler/timer, and honors scan/stop reservations.

## UI and verification

Use the existing local font/theme, consistent compact spacing and responsive cards.
Trade Ideas, Source quality, Paper research, Validation and Operations have clear
labels. Do not use buy/execute language or make unavailable fields look safe.
Mutation controls act on explicit user submissions only; retain request keys and
uncertain submissions for retry rather than generating a new financial intent.
No auto-funded account/default capital, no automatic replay, no automatic enabling
of NSE/NYSE scans, outcomes or outbound alerts during verification.

Tests cover HTTP-only boundaries, filtering/export consistency, safe empty/error
views, chart basis/gaps, allocation limits, revision/idempotency/rollback races,
paused queue/reconciliation, alert restart/delivery uncertainty and responsive UI.
Real browser checks use the actual archive with a scan-free local preview; no
synthetic API/UI demo and no real account creation. S6 prospective observation
requires separate authorization and actual trading sessions.
