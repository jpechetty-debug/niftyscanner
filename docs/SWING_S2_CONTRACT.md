# S2 frozen research contract (v1)

Two independent daily hypotheses: `DAILY_BREAKOUT_V1` and `DAILY_PULLBACK_V1`.
Both remain `RESEARCH_ONLY` / `CANDIDATE`. S2 creates calculations, never orders,
reservations, fills, broker calls or promotion records. S3 owns durable lifecycle.

## Data and indicator rules

Use an explicit immutable vintage/as-of and the configured NSE calendar. Exclude
active/future bars before feature calculation; reject unsorted/duplicate dates,
missing exact sessions, insufficient warm-up, wrong instrument/ticker/basis and
missing benchmark. Require max(configured history, twice slow EMA, all lookbacks
plus their necessary predecessor/slope observations) completed daily sessions.
No legacy P/E, RSI or volume-spike gate is applied before these strategies.

EMA seeds with the first close; recurrence alpha=2/(period+1), adjust=False.
ATR uses true range max(H-L,abs(H-prevClose),abs(L-prevClose)); first range H-L.
Seed with the arithmetic mean of the first ATR-period ranges, then Wilder
recurrence (previous*(period-1)+range)/period. Undefined prefixes remain null.
RS is stock simple return minus index simple return over exact 21/63 sessions.
Prior average volume/traded value exclude the signal bar. Pullback volume is the
mean of each preceding pullback bar's ratio to its own prior volume baseline.

Shared gates: close>fast>mid>slow EMA, mid rising over slope sessions, benchmark
close>its mid EMA, all configured excess RS>=0, liquid prior mean close*volume,
positive ATR, close-fast<=max extension ATR, candle range in (0,max candle ATR],
and close location within candle >=minimum. These are research hypotheses.

Breakout: close strictly above maximum previous breakout-lookback highs, extension
<=breakout extension ATR, signal relative volume>=breakout RVOL.
Pullback: at least one preceding pullback-lookback low<=its fast EMA+touch ATR;
every preceding close>=its mid EMA; peak high-to-trough low<=depth ATR;
preceding mean relative volume<=max pullback RVOL; signal volume>=reclaim RVOL;
completed signal close>previous high and signal open. Both may qualify; stable
ranking picks at most one allocation per security. No invented win probability.

## Levels, costs and sizing

Structural stop=min(signal low, preceding pullback-window lows)-buffer ATR.
Require positive stop and stop distance in [minimum,maximum] ATR.
Target=close+target-R*distance, capped by the preceding overhead-window maximum
high if that level is >close+overhead buffer ATR. Never extend a target to pass RR.
Reference levels are continuous research prices, not tick-rounded order prices.
Entry lower=max(close-dip ATR,stop+clearance ATR). Upper=min(close+gap ATR, maximum
entry satisfying cost-aware net RR). Require reference-close net RR>=floor and
nonempty entry interval. The entry window is anchored to the configured sessions
immediately following the signal. Only opens strictly after the decision remain
eligible. If all have passed, reject entry_window_expired; refreshes cannot roll
expiry forward. A late imported signal cannot buy a past open. Time exit instruction is next
tradable open after the holding-limit close; S2 does not simulate it.

Reuse PERFORMANCE_* cost parameters, frozen in each plan: buy multiplier
B=(1+slippage)*(1+buy STT+stamp+brokerage+other), sell multiplier
S=(1-slippage)*(1-sell STT-brokerage-other), rates converted from bps.
Risk/share=entry*B-stop*S; reward/share=target*S-entry*B.
Net RR=reward/risk. Maximum RR-compliant entry=S*(target+floor*stop)/(B*(1+floor)).
This is a stop-execution estimate; gaps/circuits can cause larger losses.

Sizing is a preview only, at the worst frozen entry (or explicitly supplied
in-range observed open). Require explicit marked NAV, available cash, exact-date
held and pending book, known sector and dated aligned return series for all book
items. Unset SWING_CAPITAL_INR yields quantity=null, never an invented account.
Quantity is the floor of the minimum cash/B-price, NAV risk/risk-per-share,
position exposure, gross exposure, aggregate open risk, sector exposure and prior
ADV participation limits. Costs consume cash; pending reservations consume all
limits. Pending cash/risk per share must include their frozen costs. Cash plus
marked held equities must reconcile to NAV; SWING_CAPITAL_INR is that explicitly
supplied current NAV, not an invented initial account. Correlation is Pearson on aligned simple daily returns, exact trailing
sessions, at least the configured observation count; absolute correlation above
limit blocks, missing/constant input blocks. Deterministic priority: net RR, then
63-session excess RS (longest configured horizon), volume ratio, ticker, strategy.
Re-evaluation cannot allocate a ticker already held or pending. No book mutation
is persisted in S2. A returned quantity is hypothetical and cannot arm a plan.

## Evidence and API

Each plan freezes config/costs, implementation hash, exact consumed bars/context
hash, vintage/evidence hashes, decision/session/availability, feature values,
gates, missing context, rationale, entry/stop/target/expiry/holding policy and
allocation constraints. Proof flags cannot be supplied through HTTP. Missing
historical identity/publication/action reconciliation, sector, earnings,
surveillance or strategy validation prevents paper eligibility. Source S1 unknown
basis cannot be promoted by selecting a query basis. Calendar special sessions
remain unverified. Optional delivery is reported unknown, not fabricated.

GET /api/swing/status is always available. GET /api/swing/results?vintage=... and
GET /api/swing/data-quality?vintage=... are explicit read-only local calculations,
disabled with 404 unless SWING_ENABLED=true; NSE only (NYSE=404, unknown=422).
Reads use worker threads, do not import sources, download, write data or schedule
scans. Missing vintage=404, damaged evidence=409, store unavailable=503.
No API accepts raw bars or synthetic mode. S1's supported archive adapter remains
basis-unverified, so production results currently report data failures and zero
plans. Portfolio allocation is a tested library contract until S3 supplies a
reconciled durable account book; HTTP cannot invent or arm one.

## Settings

Defaults and exact numeric bounds are in app/swing/config.py (all inherited by
environment Settings) and .env.example. ATR multiples are dimensionless, periods
are exchange sessions, allocation fractions are fractions of marked INR NAV,
liquidity value is INR/day, participation is shares/prior observed ADV. Capital
is positive INR or unset. Only research mode is accepted. Unknown/NaN/inf inputs
fail closed. S1 diagnostic imports may use shorter windows, but S2 evaluation
always enforces full configured indicator warm-up.

| Environment key | Default | Validation bounds |
|---|---|---|
| `SWING_ENABLED` | false | boolean |
| `SWING_MODE` | research | research only |
| `SWING_CAPITAL_INR` | unset | >0, finite, or unset |
| `SWING_EMA_FAST` | 20 | >=2, <=500 |
| `SWING_EMA_MID` | 50 | >=2, <=500 |
| `SWING_EMA_SLOW` | 200 | >=2, <=1000 |
| `SWING_ATR_PERIOD` | 14 | >=2, <=500 |
| `SWING_SLOPE_SESSIONS` | 10 | >=1, <=500 |
| `SWING_RS_LOOKBACKS` | 21,63 | unique increasing integers 1..500, CSV |
| `SWING_VOLUME_LOOKBACK` | 20 | >=2, <=500 |
| `SWING_MIN_AVG_TRADED_VALUE_INR` | 10000000 | >=0, finite |
| `SWING_BREAKOUT_LOOKBACK` | 20 | >=2, <=500 |
| `SWING_BREAKOUT_RVOL` | 1.5 | >0, finite |
| `SWING_BREAKOUT_EXTENSION_ATR` | 0.75 | >0, finite |
| `SWING_PULLBACK_LOOKBACK` | 5 | >=1, <=100 |
| `SWING_PULLBACK_TOUCH_ATR` | 0.25 | >=0, finite |
| `SWING_PULLBACK_DEPTH_ATR` | 3 | >0, finite |
| `SWING_PULLBACK_MAX_RVOL` | 1.2 | >0, finite |
| `SWING_PULLBACK_RECLAIM_RVOL` | 0.8 | >0, finite |
| `SWING_MAX_EXTENSION_ATR` | 3 | >0, finite |
| `SWING_MAX_CANDLE_ATR` | 2.5 | >0, finite |
| `SWING_MIN_CLOSE_LOCATION` | 0.6 | >=0, <=1, finite |
| `SWING_STOP_BUFFER_ATR` | 0.2 | >=0, finite |
| `SWING_MIN_STOP_ATR` | 0.75 | >0, finite |
| `SWING_MAX_STOP_ATR` | 3 | >0, finite |
| `SWING_TARGET_R` | 2 | >0, finite |
| `SWING_OVERHEAD_LOOKBACK` | 63 | >=2, <=500 |
| `SWING_OVERHEAD_BUFFER_ATR` | 0.2 | >=0, finite |
| `SWING_ENTRY_DIP_ATR` | 0.5 | >=0, finite |
| `SWING_ENTRY_STOP_CLEARANCE_ATR` | 0.5 | >0, finite |
| `SWING_MAX_GAP_ATR` | 0.5 | >=0, finite |
| `SWING_MIN_NET_RR` | 1.5 | >0, finite |
| `SWING_ENTRY_VALID_SESSIONS` | 1 | >=1, <=10 |
| `SWING_BREAKOUT_MAX_HOLD` | 10 | >=1, <=100 |
| `SWING_PULLBACK_MAX_HOLD` | 5 | >=1, <=100 |
| `SWING_RISK_FRACTION` | 0.0025 | >0, <=1, finite |
| `SWING_MAX_POSITION_FRACTION` | 0.1 | >0, <=1, finite |
| `SWING_MAX_GROSS_FRACTION` | 0.6 | >0, <=1, finite |
| `SWING_MAX_OPEN_RISK_FRACTION` | 0.015 | >0, <=1, finite |
| `SWING_MAX_SECTOR_FRACTION` | 0.25 | >0, <=1, finite |
| `SWING_MAX_ADV_PARTICIPATION` | 0.005 | >0, <=1, finite |
| `SWING_MAX_CORRELATION` | 0.75 | >=0, <=1, finite |
| `SWING_CORRELATION_SESSIONS` | 63 | >=2, <=500 |
| `SWING_MIN_CORRELATION_OBSERVATIONS` | 30 | >=2, <=500 |
