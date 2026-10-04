# Independent review of the real-data phase

Reviewed on October 4, 2026 against main at 556f7679dbe0b50e88d70f07462a97c724abe911. This phase verifies the supplied completion report; it does not implement the circuit-risk backlog or change saved user preferences.

## Findings

1. **P2 — SPEC and implementation disagree.** docs/SPEC.md §5 still specifies REFRESH_INTERVAL_SEC=60; Settings and .env.example specify 300. Section 6 says other NSE CSV columns are ignored, whereas Series now controls RR exclusions and DUMMY symbols are excluded. Update the contract to record the accepted new rules; the submitted compliance table overstates agreement with the current SPEC.
2. **P2 — Runtime reduction has not taken effect in this workspace.** The code default is 300, but data/settings.json overrides it to 120 and the running API reports 120. This follows the specified precedence and is not a configuration-loader bug. The default change therefore does not establish that this installation scans every five minutes. Applying 300 to this installation requires deliberately changing its saved setting; this review preserved that preference.
3. **P2 — Calendar report overstates verification and understates impact.** Installed XBOM treats 2026-01-15 as a session; [NSE circular CMTR72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf) explicitly declares it a trading holiday. The mismatch is now VERIFIED. The inference from PerformanceService._exits is that this also affects historical session-based horizons if signals spanning that date are evaluated; a past holiday is not necessarily harmless to performance backfills. Full reconciliation of future sessions was not performed. [NSE's holiday page](https://www.nseindia.com/resources/exchange-communication-holidays) announces November 8 Muhurat trading, which the proxy treats as closed. Document special-session support as incomplete; treating it as closed avoids scanning then but does not accurately model all exchange sessions.
4. **P3 — Request estimates are not measurements.** README.md:26 and REAL_DATA_REVIEW.md:31 imply ~125k → ~40k requests and prevention of rate limiting. The scheduler waits after completion: start spacing is scan duration + max(config interval, 2 × last scan duration), with retries, manual scans and cross-market contention adding variability. For an illustrative constant 48-second NSE scan, a 6.25-hour session gives about 156 starts at the old 60-second setting and about 65 at 300, ignoring startup/post-close and contention; not 230 and 75. Total attempts also depend on P/E survivors, cache expiry and retries. Label estimates with assumptions; a larger interval reduces load but cannot guarantee no throttling.
5. **P3 — The real-file regression is brittle.** tests/test_universe.py:105 hardcodes 497 and the identities of current excluded names. An official constituent refresh can change row counts or series and break the suite even when the loader is correct. Keep exact totals in a versioned SYNTHETIC fixture and compare a real file's eligible rows to its own contents for integrity checks.
6. **Wording — BE does not mean unconstrained execution.** Keeping BE equities eligible is consistent with the selected screening policy. However [NSE's trade-for-trade rules](https://www.nseindia.com/static/regulations/movement-securities-periodic-review) require settlement without netting; eligibility does not guarantee an executable next-open fill. Describe BE as delivery-settlement eligible with execution/circuit limitations, rather than “delivery buying is unconstrained.” Circuit-risk implementation can remain a separate phase.

## Verified / not verified

| Item | Result | Evidence |
|---|---|---|
| Regression suite | VERIFIED | 194 passed, 1 warning in 7.47s |
| DUMMY and RR exclusion; BE retention | VERIFIED | Source, executed tests; supplied file has 501 raw rows, 1 DUMMY and 3 RR |
| Code default 300 | VERIFIED | Settings.model_fields default; .env.example |
| Running interval 300 | NOT TRUE HERE | load_settings and GET /api/settings both return 120; saved override |
| Pytest discovery scope | VERIFIED | testpaths=tests, live marker excluded; successful suite run |
| Remote Git sync | VERIFIED | git ls-remote reports main=556f7679dbe0b50e88d70f07462a97c724abe911; both earlier commits are ancestors |
| January 15 holiday mismatch | VERIFIED | Installed XBOM is_session=True versus official NSE circular |
| All future sessions align | NOT VERIFIED | No complete official reconciliation; Muhurat exception known |
| Exact load reduction / guaranteed no throttling | NOT VERIFIED | No real session request measurements in this review |
| Trading-window feeds / circuit execution | NOT VERIFIED | No full scans or execution tests performed |

Files created: this review report. Application, settings, market files and prior reports were not edited.

Commands executed: full SPEC/AGENTS/report reads; CodeGraph exploration; git status/log/show/branch/remote inspection; git merge-base --is-ancestor for 4383c5d and ff4a02a against 556f767; git ls-remote origin refs/heads/main; Python configuration/universe/calendar probes; HTTP GET /api/settings; official NSE circular and rules review; the following test command:

```powershell
$taskTmp=Join-Path (Get-Location).Path ('logs/claim-review-tests-'+[guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp $taskTmp
```

Actual output: `194 passed, 1 warning in 7.47s`. The warning is Starlette's deprecated AnyIO BlockingPortal alias. The initial sandboxed remote read failed; the approved read-only retry succeeded. An initial attempted pyproject.toml read found no such file; pytest configuration is in pytest.ini. An ancillary line-printing probe hit a Windows cp1252 emoji encoding error after reading documentation; substantive checks and tests completed independently.

Assumptions: retaining BE is the chosen screening policy; the rate example is arithmetic, not observed throughput; existing saved settings represent a user preference and remain intact. Source descriptions and successful tests do not establish future calendar completeness or executable fills.

## SPEC COMPLIANCE

| Section | Status | Note |
|---|---|---|
| 0: review discipline | Done | SPEC read; one verification phase; commands/results recorded |
| 2/20: integrity/testing | Done | Production files read only; 194 tests executed |
| 5/6: configuration/universe contract | Partial | Accepted implementation changes absent from SPEC |
| 15/17: refresh/settings | Done | Precedence verified; runtime saved interval correctly overrides default |
| 16: calendar | Partial | Holiday mismatch confirmed; special sessions unsupported |
| Circuit-risk enhancement | Not in this phase | Backlog retained; no implementation started |

## STATE SUMMARY

```text
Phase: independent review complete; no implementation changes.
Created docs/REAL_DATA_REVIEW_CHECK.md: findings, evidence and verification limits.
Public interfaces/config keys added: none.
194 tests passed; one Starlette deprecation warning.
DUMMY/RR exclusions verified; BE retained; supplied raw universe remains 501 rows.
Settings code default 300; saved/runtime interval 120.
Remote main confirmed at 556f767; 4383c5d and ff4a02a are ancestors.
SPEC still states 60 and ignores extra CSV columns: contract drift found.
NSE Jan 15 holiday confirmed; installed XBOM incorrectly marks a session.
Complete future calendar alignment and actual load reduction unverified.
Request-count estimates omit scan duration; real-file test hardcodes current count.
BE eligibility does not imply unrestricted execution.
Assumptions: preserve saved preference; circuit-risk work stays separately scoped.
Next implementation phase requires CONTINUE; first read SPEC and reconcile contract.
```
