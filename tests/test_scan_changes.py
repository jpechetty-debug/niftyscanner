"""Scan-to-scan change tracking and new-signal webhook alerts (spec section 24).

All market data here is SYNTHETIC (fictitious TEST*.NS tickers); no network is used.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest

from app.api.models import ResultsResponse
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.core.interfaces import ScanResultItem
from app.core.outcomes import FunnelCounts
from app.market.calendar import MarketCalendar
from app.market.clock import FakeClock
from app.providers.fake_provider import FakeProvider
from app.scheduler.runner import Scheduler
from app.services.alerts import WebhookAlerter
from app.services.changes import annotate_changes
from app.services.scanner import StockScannerService
from app.services.state import ScanStateManager
from app.universe.nse import NSEUniverse

UNIVERSE = str(Path(__file__).parent / "fixtures" / "nifty500_synthetic.csv")
T0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


def item(ticker: str, partial: bool = False, bar_date: str = "2026-10-01") -> ScanResultItem:
    """SYNTHETIC qualifying result."""
    return ScanResultItem(ticker=ticker, name=f"Synthetic {ticker}", market="NSE", price=100.0, pe=12.0,
                          rsi=55.0, volume=2000, avg_volume_20d=1000.0, volume_ratio=2.0, score=0.5,
                          session_partial=partial, bar_date=bar_date)


# --------------------------------------------------------------------------- diff

def test_no_baseline_flags_nothing():
    current = [item("TEST1.NS"), item("TEST2.NS")]
    changes = annotate_changes([], current, None, T0)
    assert changes.new_entries == [] and changes.dropped == [] and changes.compared_to is None
    assert all(not r.is_new and r.scans_qualified == 1 and r.first_seen_at == T0.isoformat() for r in current)


def test_new_kept_and_dropped_with_streaks():
    first = [item("TEST1.NS"), item("TEST2.NS")]
    annotate_changes([], first, None, T0)
    t1 = T0 + timedelta(minutes=5)
    second = [item("TEST2.NS"), item("TEST3.NS")]
    changes = annotate_changes(first, second, T0, t1)

    assert changes.new_entries == ["TEST3.NS"]
    assert [d.ticker for d in changes.dropped] == ["TEST1.NS"]
    assert changes.compared_to == T0.isoformat()
    kept, new = second
    assert (kept.is_new, kept.scans_qualified, kept.first_seen_at) == (False, 2, T0.isoformat())
    assert (new.is_new, new.scans_qualified, new.first_seen_at) == (True, 1, t1.isoformat())


def test_old_snapshot_items_without_first_seen_use_previous_refresh():
    legacy = [ScanResultItem(**{**item("TEST1.NS").model_dump(), "first_seen_at": None})]
    current = [item("TEST1.NS")]
    annotate_changes(legacy, current, T0, T0 + timedelta(minutes=5))
    assert current[0].first_seen_at == T0.isoformat() and current[0].scans_qualified == 2


# --------------------------------------------------------------------------- state / API

def _state(clock):
    cal = MarketCalendar("NSE")
    return ScanStateManager(config=Settings(ENABLED_MARKETS="NSE"),
                            download_breakers={"NSE": CircuitBreaker("d", clock=clock)},
                            pe_breakers={"NSE": CircuitBreaker("p", clock=clock)},
                            calendar=cal, clock=clock)


def test_state_payload_exposes_changes_and_failed_scan_keeps_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # isolate from any real data/last_scan_*.json
    clock = FakeClock(T0)
    state = _state(clock)
    state.update_scan_success("NSE", [item("TEST1.NS")], FunnelCounts(), [], 1.0, 1, persist=False)
    clock.sleep(300)
    state.update_scan_success("NSE", [item("TEST2.NS")], FunnelCounts(), [], 1.0, 1, persist=False)
    clock.sleep(300)
    state.update_scan_failure("NSE", [], 1.0, 1)

    payload = state.get_results_payload("NSE")
    ResultsResponse(**payload)  # response model accepts the new fields
    assert payload["meta"]["new_entries"] == ["TEST2.NS"]
    assert payload["meta"]["dropped"] == [{"ticker": "TEST1.NS", "name": "Synthetic TEST1.NS"}]
    assert payload["meta"]["changes_compared_to"] == T0.isoformat()
    assert payload["results"][0]["is_new"] is True


def test_changes_survive_snapshot_restore(tmp_path, monkeypatch):
    import app.cache.persistence as persistence
    import app.services.state as state_module

    # conftest stubs disk I/O; use the real snapshot round-trip, confined to tmp_path.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state_module, "save_last_scan", persistence.save_last_scan)
    monkeypatch.setattr(state_module, "load_last_scan_on_startup", persistence.load_last_scan_on_startup)
    clock = FakeClock(T0)
    state = _state(clock)
    state.update_scan_success("NSE", [item("TEST1.NS")], FunnelCounts(), [], 1.0, 1, persist=False)
    clock.sleep(300)
    state.update_scan_success("NSE", [item("TEST1.NS"), item("TEST2.NS")], FunnelCounts(), [], 1.0, 1)

    restored = _state(clock)
    meta = restored.get_results_payload("NSE")["meta"]
    assert meta["new_entries"] == ["TEST2.NS"]
    streaks = {r.ticker: r.scans_qualified for r in restored.results_store["NSE"]}
    assert streaks == {"TEST1.NS": 2, "TEST2.NS": 1}

    # The restored snapshot is the baseline for the first scan after restart: no alert storm.
    clock.sleep(300)
    restored.update_scan_success("NSE", [item("TEST1.NS"), item("TEST2.NS")], FunnelCounts(), [], 1.0, 1, persist=False)
    assert restored.get_results_payload("NSE")["meta"]["new_entries"] == []
    assert {r.ticker: r.scans_qualified for r in restored.results_store["NSE"]} == {"TEST1.NS": 3, "TEST2.NS": 2}


# --------------------------------------------------------------------------- alerts

class FakePost:
    def __init__(self, status=200, error=None):
        self.calls, self.status, self.error = [], status, error

    def __call__(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        if self.error:
            raise self.error
        return httpx.Response(self.status, request=httpx.Request("POST", url))


def _new(*tickers, partial=False, bar_date="2026-10-01"):
    rows = [item(t, partial, bar_date) for t in tickers]
    for r in rows:
        r.is_new = True
    return rows


def test_alerts_disabled_without_url(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(httpx, "post", post)
    assert WebhookAlerter(Settings()).notify("NSE", _new("TEST1.NS")) == 0
    assert post.calls == []


def test_alert_sent_once_per_ticker_per_session(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(httpx, "post", post)
    alerter = WebhookAlerter(Settings(ALERT_WEBHOOK_URL="https://example.invalid/hook", ALERT_TIMEOUT_SEC=3))

    assert alerter.notify("NSE", _new("TEST1.NS")) == 1
    url, body, timeout = post.calls[0]
    assert timeout == 3 and body["market"] == "NSE" and body["signals"][0]["ticker"] == "TEST1.NS"
    assert "Not financial advice" in body["text"] and body["content"] == body["text"]

    # Drops out and re-enters the same session: no repeat. A new ticker still alerts.
    assert alerter.notify("NSE", _new("TEST1.NS", "TEST2.NS")) == 1
    assert [s["ticker"] for s in post.calls[1][1]["signals"]] == ["TEST2.NS"]
    # Next session resets the dedupe.
    assert alerter.notify("NSE", _new("TEST1.NS", bar_date="2026-10-03")) == 1
    # Non-new rows never alert.
    kept = [item("TEST9.NS")]
    assert alerter.notify("NSE", kept) == 0


def test_partial_rows_respect_config(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(httpx, "post", post)
    completed_only = WebhookAlerter(Settings(ALERT_WEBHOOK_URL="https://example.invalid/hook", ALERT_INCLUDE_PARTIAL=False))
    assert completed_only.notify("NSE", _new("TEST1.NS", partial=True)) == 0
    included = WebhookAlerter(Settings(ALERT_WEBHOOK_URL="https://example.invalid/hook"))
    assert included.notify("NSE", _new("TEST1.NS", partial=True)) == 1
    assert "partial-session" in post.calls[0][1]["text"]


@pytest.mark.parametrize("failure", [FakePost(status=500), FakePost(error=httpx.ConnectError("boom"))])
def test_failed_delivery_is_retried_next_scan_and_does_not_raise(monkeypatch, failure):
    monkeypatch.setattr(httpx, "post", failure)
    alerter = WebhookAlerter(Settings(ALERT_WEBHOOK_URL="https://example.invalid/hook"))
    assert alerter.notify("NSE", _new("TEST1.NS")) == 0
    ok = FakePost()
    monkeypatch.setattr(httpx, "post", ok)
    assert alerter.notify("NSE", _new("TEST1.NS")) == 1


def test_webhook_url_must_be_http():
    with pytest.raises(ValueError):
        Settings(ALERT_WEBHOOK_URL="ftp://example.invalid")


# --------------------------------------------------------------------------- scheduler wiring

class RecordingAlerter:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def notify(self, market, results):
        self.calls.append((market, [r.ticker for r in results if r.is_new]))
        if self.fail:
            raise RuntimeError("webhook exploded")
        return 0


def _scheduler(clock, alerter):
    config = Settings(ENABLED_MARKETS="NSE", MAX_PE=20, MIN_AVG_VOLUME=0)
    cal = MarketCalendar("NSE")
    state = ScanStateManager(config=config, download_breakers={"NSE": CircuitBreaker("d", clock=clock)},
                             pe_breakers={"NSE": CircuitBreaker("p", clock=clock)}, calendar=cal, clock=clock)
    scanner = StockScannerService(config=config, provider=FakeProvider(print_banner=False), calendar=cal, clock=clock)
    return Scheduler(config=config, scanner_services={"NSE": scanner}, state_manager=state,
                     universes={"NSE": NSEUniverse(UNIVERSE)}, calendars={"NSE": cal}, clock=clock,
                     alerter=alerter), state


@pytest.mark.asyncio
async def test_scheduler_notifies_after_success_and_alert_errors_do_not_fail_scan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    clock = FakeClock(T0)
    alerter = RecordingAlerter(fail=True)
    scheduler, state = _scheduler(clock, alerter)

    assert await scheduler.execute_scan("NSE") is True
    assert alerter.calls and alerter.calls[0] == ("NSE", [])  # cold start: nothing new
    assert state.last_scan_failed["NSE"] is False


def test_ui_new_only_filter():
    from ui.app import describe_changes, filter_results_dataframe

    frame = pd.DataFrame([{**item("TEST1.NS").model_dump(), "is_new": True},
                          {**item("TEST2.NS").model_dump(), "is_new": False}])
    assert list(filter_results_dataframe(frame, "", None, None, None, False, True)["ticker"]) == ["TEST1.NS"]
    assert len(filter_results_dataframe(frame, "", None, None, None, False)) == 2
    legacy = frame.drop(columns=["is_new"])
    assert filter_results_dataframe(legacy, "", None, None, None, False, True).empty

    assert describe_changes({}, "NSE") is None
    line = describe_changes({"changes_compared_to": T0.isoformat(), "new_entries": ["TEST1.NS"],
                             "dropped": [{"ticker": "TEST2.NS", "name": "x"}]}, "NSE")
    assert "1 new: TEST1.NS" in line and "1 dropped: TEST2.NS" in line
