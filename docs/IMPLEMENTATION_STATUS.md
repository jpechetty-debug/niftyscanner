# Implementation check against the 1 October 2026 review

Checked October 4, 2026 at commit 841587e. Scope: verify the user's checklist, without implementing backlog items or changing saved preferences.

This is the historical pre-implementation audit. The subsequent authorized implementation and current verification are recorded in [FEATURE_COMPLETION.md](FEATURE_COMPLETION.md).

## Triage coverage

| Item | Implementation status | Evidence / limit |
|---|---|---|
| Drop DUMMY* placeholders | Implemented | NSEUniverse.load excludes the prefix case-insensitively; supplied file drops 1 row |
| Skip Series RR | Implemented | Series is normalized and RR excluded; BAGMANE/BIRET/EMBASSY absent; loader returns 497 from 501 raw rows |
| January 15 calendar limitation | Documented; not repaired | XBOM still reports 2026-01-15 as a session; no NSE-specific override. “Past-only” does not establish that historical horizons are unaffected; future calendar completeness remains unverified |
| Early-session projection | Implemented and documented | Denominator floor 0.25 gives a maximum 4x multiplier. Exactly 37.5% of avg volume gives 1.5x and fails the strict >1.5 condition; 38% gives 1.52x. “Most morning breakouts missed” is not measured here |
| REFRESH_INTERVAL_SEC=300 | Implemented default; inactive in current saved settings | Settings default and .env.example are 300; load_settings and running GET /api/settings return 120. Request estimates are not measurements or guarantees against throttling |
| Circuit-risk flag | Not implemented; backlog | ScanResultItem has no circuit_risk/day-move field; no price-band detection logic found in app/UI/tests |
| Gross-return disclosure | Documented; cost model not implemented | README, review and performance UI disclose gross returns; returns() uses entry/exit price ratios without cost deductions |
| Yahoo-only P/E risk / manual spot-check | Documented | YFinanceProvider uses Ticker.info; README recommends manual checks. No independent P/E cross-check provider was added |

The immediate code changes are present. The calendar correction, circuit-risk feature, transaction-cost model and independent P/E validation are not implemented; only the disclosures requested for those items are present.

## “Confirmed working” claims

| Claim | Result |
|---|---|
| BE names HFCL/MTARTECH/STLTECH retained | VERIFIED in actual loader output; screening eligibility does not guarantee next-open execution |
| M&M/BAJAJ-AUTO/NAM-INDIA load | VERIFIED in actual loader output |
| Post-close scheduling | Implemented and covered by the previously executed regression suite; real post-close feed behavior remains NOT VERIFIED |
| Next-open entry skips October 2 and weekend | VERIFIED: _capture_schedule for an October 1 post-close capture returns October 5; 18 stored NSE cohorts have that entry |
| Exit horizons October 5/9/16 | VERIFIED in direct schedule assertions and all 54 stored pending outcomes (18 per horizon) |
| Liquidity floor ₹1 crore | Implemented: MIN_AVG_TRADED_VALUE_NSE=10000000, previous completed-bar mean Close × Volume proxy |
| Floor excluded nothing in that session | Cached funnel reports filtered_liquidity=0; not independently verified by replaying that session with the new value floor |
| Benchmark checks | Implemented: positive finite prices, exact entry/exit session lookup and excess-return calculation. Archived real probe contains 22 valid sessions; official-index reconciliation remains NOT VERIFIED |

The running API is serving a **stale October 1 data snapshot with universe=501**, despite the updated loader returning 497. New exclusions and filters apply on a subsequent successful scan; existing records are not retroactively rewritten. No full scan was triggered during this audit.

## Verification, commands and files

Earlier full-suite output in this conversation: **194 passed, 1 warning in 7.47s** (Starlette AnyIO deprecation). `git diff 556f767..HEAD -- app tests .env.example pytest.ini` is empty, so application and test code are unchanged since that executed suite. The full suite was not unnecessarily rerun for this documentation-only commit.

Current read-only assertions executed successfully:

```text
PASS: real universe/default/result schema and date-schedule assertions
PASS: SYNTHETIC early-volume check; 37.5% gives 1.50x (fails strict >1.5), 38% gives 1.52x
```

Also executed: full SPEC/AGENTS/report reads; CodeGraph exploration; git status/log/diff; scoped rg for circuit/cost/gross references; Python loader/config/calendar/schedule assertions; read-only SQLite cohort/outcome queries; HTTP GET /api/settings and /api/results?market=NSE; `python -m scripts.review_evidence --verify docs/evidence/review-evidence-2026-10-04.zip`.

Offline archive verification succeeded: 501 matching raw constituent rows, 22 valid benchmark sessions, database integrity ok, 54 NSE/39 NYSE signals and 54 pending outcome rows. Archived raw membership is distinct from the loader's 497 eligible equities.

Created: this report only. Public interfaces and configuration keys added: none. Assumptions: evaluate implementation separately from measured live outcomes; preserve saved 120-second preference; no artificial production data or full-session replay.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0: phase discipline | Done | SPEC reread; one verification phase; evidence recorded |
| 2/20: integrity/testing | Done | Read-only production probes; isolated SYNTHETIC volume arithmetic; prior executed suite unchanged |
| 5/6: config/universe contract | Partial | SPEC still says 60 and ignores extra CSV columns; implementation defaults to 300 and reads Series |
| 9/15/17: volume/scheduler/API | Done | Implemented; runtime preference and stale cached results disclosed |
| 16: NSE calendar | Partial | Proxy remains; missing holiday not repaired |
| Circuit-risk/cost model | Not in this phase | Backlog retained |

## STATE SUMMARY

```text
Phase: implementation coverage audit complete.
Created docs/IMPLEMENTATION_STATUS.md: checklist status and actual evidence.
Application/settings/universe files modified: none.
Public interfaces/config keys added: none.
DUMMY/RR exclusions implemented; loader returns 497; BE/special symbols retained.
Refresh default 300; saved and running interval 120.
Bounded volume projection implemented; SYNTHETIC boundary assertions pass.
October 1 capture entry October 5; exits October 5/9/16 verified in code/DB.
Calendar limitation documented but not repaired; circuit_risk not implemented.
Gross-return disclosure present; cost model absent; Yahoo-only P/E remains.
Liquidity floor present; cached funnel reports zero exclusions, replay not verified.
Benchmark sanity/archive verification passes; official reconciliation absent.
Prior 194-test suite passed; app/tests unchanged since; no redundant full rerun.
API still serves stale 501-universe snapshot; subsequent successful scan uses new loader.
SPEC drift remains; full market-hours/post-close checks remain unverified.
Assumptions: preserve saved preferences; distinguish code from live verification.
Next implementation phase requires CONTINUE; reread SPEC and select remaining scope.
```
