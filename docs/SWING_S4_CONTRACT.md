# S4 frozen validation contract (v1, 17 October 2026)

S4 is an offline, read-only evaluator of S3 research ledgers. It never creates
accounts, backdates plans, fabricates fills or changes a live plan's status. The
current archive is unsuitable for historical validation; a blocked report is an
expected result. S5 UI/operations and S6 actual prospective observation remain
separate phases.

## Preregistration and evidence

Before evaluation, register an immutable JSON study: hypothesis, strategy IDs,
complete S2 configurations, every variant/account, intended capital/capacity,
expanding train/validation/test folds, terminal holdout, statistical and failure
thresholds. No optimizer or post-test variant selection. One primary, declared
one-parameter neighbors exhaust the declared search budget. Execution stresses
replay primary accounts in isolated
in-memory books under frozen adverse policies; account names never attest stress.
Each window/parameter variant uses a distinct flat-start S3
account, seeded on the prior session with the same explicit capital. This avoids
carrying training holdings into a test NAV. No external return/fill-file input.

Window endpoints must be actual NSE sessions. Fold tests do not overlap, training
expands, and validation/test/holdout are separated by a configured embargo.
Trailing signal dates are purged by the maximum configured holding horizon plus
entry validity plus the next-open exit session. Signals outside the remaining
window, overlapping labels, unclosed liabilities and missing session marks block
that window; they are never silently removed to improve the sample.

Every fold test and the terminal holdout must start after registration, never on already inspected
historical data. The local registry rejects overlapping holdout reservations for
the same strategy, even in another study. Opening is an explicit one-time claim
after the entire holdout completes. The claim is committed before reading its
outcomes; crashes consume it. Identical completed reads return the saved report;
there is no holdout retry/tune/reset operation. Development reports never read
holdout accounts. These controls cannot detect evaluation outside this registry.

Freeze calendar session schedules and source-code hashes at registration.
Freeze Python/SQLite/numpy/pandas/pydantic/calendar-library versions as well.
Accounts are read in a single SQLite snapshot, hash chains and plan hashes are
verified, and evaluated event heads/plan IDs/data vintages are included in the
manifest. Require plan configuration, implementation, strategy, availability and
dated source evidence to match the study. No current universe, sector or price
adjustment is accepted as historical evidence. S1 verification/coverage is audited
independently. Missing PIT membership/publication/adjustment/benchmark/action or
capacity evidence blocks validation. Synthetic fixture results cannot qualify.

## Metrics, uncertainty and stresses

Report all windows and variants, counts/rejection reasons, net trade expectancy
in INR and initial-risk R, wins/losses/payoff, holding duration, sectors, fill and
data coverage, turnover, daily exposure, maximum drawdown and underwater duration.
Use exact daily net portfolio NAV, including flat days, with no forward fill.
Chain disjoint OOS test windows by normalized NAV; report holdout separately.
Do not pool repeated training/validation dates with OOS results.

Compare gross Nifty 500 buy/hold, zero-return cash and a frozen constant-exposure
Nifty 500/cash baseline (default 60% index, daily rebalance, estimated frozen
transaction costs). Also show a lagged-exposure index/cash diagnostic; it is not
an independently tradable strategy. Compare at equal starting capital. Gross
index versus net strategy and residual exposure differences remain explicit.

Primary uncertainty: seeded moving-block bootstrap on ordered signal-date
clusters, including sessions with no signals, with blocks at least the maximum
label horizon. Resample whole clusters, then compute trade-weighted expectancy;
report 95% percentile bounds, number of non-overlapping periods and sparse-cluster
replicates. Never treat overlapping trades as independent observations. The frozen
primary avoids selecting the best of variants; all tried variants are disclosed.

Stress frozen filled cash flows with additional per-side basis points. This is
an isolated cost sensitivity, not a new portfolio path. Parameter robustness
requires separately captured S3 accounts; delay/gap/halt robustness requires the
isolated replays below. Missing stress
paths fail their gates, rather than being synthesized by shifting a winning exit.
Counterfactual books use S3 risk/IOC/adverse-OHLC/cost rules and the recorded capture
and observation timeline. Seeded plan-ID hashes select a frozen 10% of opportunities:
one session without opening capacity, adverse execution impact of 100 bps on both
sides (with entry bounds/net RR rechecked), or one-session zero exit capacity from
the first exit trigger. These defaults/fraction are configurable and registered.
No observed OHLC prices are changed; original observations and production ledgers
remain untouched. Changed holding paths require explicit additional verified data;
unavailable observations are unresolved. Each scenario must affect an opportunity,
and all books obey the same evidence/NAV/sample checks. Real-world fill
uncertainty and unsupported historical actions remain failures, not imputed losses.

## Gates, defaults and manifests

Environment-configurable defaults are frozen into each study: >=2 folds, >=100
resolved trades, >=20 non-overlapping periods, >=3 sectors, >=2 one-parameter
neighbors, 5-session embargo, 20-session bootstrap blocks, 2000 replicates, seed
1729, alpha .05, max drawdown 10%, max gross exposure 60%, max participation .5%
ADV, fill coverage >=80%, neighbor expectancy >=50% of primary, extra costs
10/25 bps per side. These are research defaults, not mandated standards.
Neighbors must be distinct one-parameter numeric strategy/risk perturbations,
within a configured 20% relative change, rather than duplicate configurations.
Block length is raised to the label horizon when necessary, never shortened.
No implicit account capital or dates. All finite bounds are validated.

A paper-validation recommendation requires independently sufficient OOS and
terminal samples, positive lower expectancy bounds in INR and R, positive net
return, nonnegative excess versus the frozen simple baseline, all parameter and
execution stresses, drawdown/exposure/capacity/fill gates and zero integrity gaps.
Training/validation summaries never promote. An unsigned or development-only
report stays RESEARCH_ONLY. A recommendation is a separate report, never an
automatic mutation of S2/S3 plans or eligibility. Failed holdouts cannot be cured
by retuning the same holdout.

Use a separate append-only SQLite registry, refusing unrelated/future schemas.
Freeze report content hashes and optional HMAC-SHA256 signatures with a dedicated
environment key (>=32 bytes). HMAC is local shared-key authentication, not an
independent auditor's public signature; an absent key is disclosed and fails the
promotion gate. Reports include code, protocol, calendar and ledger input hashes.
No keys appear in manifests. A deterministic report contains no wall-clock timing
noise. CLI register/development/holdout/show/verify plus source audit are explicit;
no scheduler, HTTP mutation, network requests or UI changes in S4.
