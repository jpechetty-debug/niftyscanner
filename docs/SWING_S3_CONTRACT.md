# S3 frozen research replay and lifecycle contract (v1)

S3 implements durable **RESEARCH_REPLAY** accounts. It does not promote models,
enable live trading or create broker orders. Lifecycle (`CANDIDATE`, `ARMED`,
`OPEN`, `CLOSED`, `EXPIRED`, `CANCELLED`, `UNRESOLVED`) is separate from the frozen
`RESEARCH_ONLY` model status. ARMED means reserved in a simulation only. Critical
S1/S2 evidence gates remain mandatory. S4 owns paper validation/promotion.

## Store and commands

Use a separate `SWING_PAPER_DB_PATH=data/swing_paper.db`, never an S1/source/legacy
database. Schema v1 creation is transactional and refuses unrelated/future
schemas. No destructive migrations; explicit consistent backup is supported.
SQLite BEGIN IMMEDIATE serializes account changes. Plans, account seeds,
hash-chained events, accepted jobs and completed command receipts are append-only.
No cached balance is authoritative: reconstruct cash, reservations, quantities,
levels, lifecycle and NAV by replaying events, checking sequence/hash continuity.

Explicit INR funding, account ID and starting completed session are required.
No account or capital is created at startup. Each command/job supplies a unique
idempotency key and expected account event-version. Identical retries return
the original receipt; different content under the same key is a conflict.
Plan logical identity is account/ticker/strategy/signal-session/config; the
consumed data hash is checked separately for conflicting revisions.
A refreshed observation of the same setup cannot overwrite its initial plan.
Different data revisions for an already captured strategy/session/config are
reported as a conflict, not silently substituted. Active ticker reservations or
positions prevent duplicate allocation across strategies, refreshes and restart.

Accepted replay jobs are durable before HTTP 202. The existing scheduler drains
one job at a time under the same global lock as scans/outcome work, off-loop;
unfinished jobs remain queued across restart. No second scheduler or periodic
trading loop. Failures roll back the whole job's financial events/receipts and
record an immutable failure; a retry after failure needs a new key/version.
Shutdown drains accepted running work; queued requests remain durable.

## Replay timeline and information

Capture only server-generated S2 plans from a verified explicit S1 vintage.
HTTP accepts no bars, proof booleans, model promotions, quantities or prices.
Research accounts may be explicitly created even with missing data; fills may not.
Replay at most `SWING_REPLAY_MAX_SESSIONS=250` completed exchange sessions per
job (1..5000). A job's through-date must be completed, and its explicit vintage
must be available at the injected clock time. New signal decisions/availability
must precede the relevant next open; no historical signal generation from data
imported later. Outcomes are ex-post observations, visibly OHLC approximations.

Daily order: verified corporate actions, prior held exits at open/stop/target,
reserved next-open entries, same-day adverse exits, close marks/time instructions,
then newly available signal reservations. New allocations cannot spend proceeds
from an intraday exit that were unavailable at the open. Marks/returns and all
new allocation inputs must use sessions available at their decision. No future
volume or high/low creates an open fill or determines open capacity.

Use S2 frozen setup/cost parameters and the actual reconciled replay NAV/book for
risk allocation. Dynamic marked NAV is a runtime account input, not strategy
retuning. Unknown/stale holdings or missing aligned correlation history block
new allocations. Pending reservations consume cash, sector/gross/open-risk limits.
Recheck entry interval, net RR, cash and portfolio limits at the actual open;
never increase the reserved quantity. Unfilled entry remainder is cancelled after
that eligible open (IOC approximation), including supplied partial capacity.

## Fills and unresolved outcomes

Execution observations must be raw-basis, exact-session/security matched, available
by replay observation time and explicitly action/tradability verified. Per-side
capacity must be explicit; daily total volume is not opening executable volume.
Missing capacity/tradability/actions or missing bars produces UNRESOLVED, never a
guaranteed fill. An uncertain pending order retains its reservation until explicit
reconciliation; it cannot silently become cash for another idea. Unknown held
paths are permanently flagged in outcomes and excluded from resolved summaries.

Entry is the frozen eligible session open within bounds, after plan availability,
with slippage/fees applied as S2 multiplicative cash flows. Entry gaps expire the
order, no daily-high touch entries. Daily stop/target touches are approximations:
both touched => stop first, with a separate target-first sensitivity value.
Stop gap below stop => adverse actual open; target gaps use the conservative
target level. Locked/suspended/zero-capacity exits remain open and pending.
Partial exits preserve remaining quantity, stop, target and pending exit reason;
the remainder exits at a later verified tradable open, without resetting the plan.

Entry session counts as holding session 1. After max-hold close, schedule a time
exit for the next tradable open; never fill retrospectively at that close.
Known split/bonus ratios transform held quantity/mark/stop/target while preserving
original plans and total cash basis/risk. Fractional entitlements, conflicting
identities and unsupported cash distributions remain unresolved; no guessed cash
in lieu, dividends or delisting payouts. Action IDs prevent duplicate adjustment.
Pending orders spanning an action require reviewed reconciliation; an old entry
interval is never silently converted into a new executable order.

Cash uses Decimal INR with paise rounding per fill; reservations round upward.
Frozen percentage estimates are not statutory contract notes (fixed DP fees,
impact and actual tax rounding remain limitations). Event payloads retain raw
reference, cost multiplier, quantity, gross/cash values and source evidence.

## NAV, controls and API

Record exact-session close marks and cash, plus a gross Nifty 500 price-index
comparison initialized from a verified index open. Missing marks yield unavailable
NAV and explicitly stale last-known marks; no forward-filled NAV/benchmark chart.
Report realised P/L from actual simulated cash flows, open exposure, unresolved
counts and ambiguity sensitivity; no win probability or validation claim.
Later data revisions do not rewrite already processed events/NAV. Unresolved
historical paths remain visible and require an explicit reviewed reconciliation.

`SWING_REPLAY_ENABLED=false` is a second opt-in mutation/worker gate. Disabling
swing/replay prevents new jobs but preserves account/plan/event/job read access
and open liabilities. S3 does not silently change a running preview's settings.
Only NSE and local research accounts; new API family:

- POST `/api/swing/paper/accounts`: explicit ID, funding, starting session/key.
- POST `/api/swing/replay`: account, vintage, through-session, expected version/key;
  capture current eligible S2 plans and advance existing research lifecycle.
- GET `/api/swing/paper/accounts/{id}`, `/plans/{id}?account_id=...`,
  `/paper/events?account_id=...`, `/paper/jobs/{id}`, `/paper/performance?account_id=...`.
- POST `/api/swing/paper/orders/{id}/cancel` can cancel known unfilled candidates
  or reservations; it refuses open or unresolved liabilities. Key/version required.

All I/O runs off-loop. Mutations use 404 disabled/unknown, 409 conflict, 422 invalid;
reads do not initialize a missing DB. Jobs accepted durably return 202; a busy
shared worker leaves them queued, never overlaps scans. Terminal retry semantics
and versions are visible in job/account reads. No generic external fill/event
injection endpoint or model-promotion endpoint is provided.
