"""Optional webhook alerts for stocks that newly qualify (section 24).

Disabled unless ALERT_WEBHOOK_URL is set. Alerts are best-effort: a failed delivery is
logged and retried on the next scan, and never affects scan success or stored results.
The webhook URL may embed a secret token, so it is never logged.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Set, Tuple

import httpx
from loguru import logger

from app.core.config import Settings
from app.core.interfaces import ScanResultItem

NOTICE = "Yahoo Finance data is delayed and not real-time. For research purposes. Not financial advice."
TEXT_LIMIT = 10  # tickers listed in the human-readable line; the full list is in "signals"


class WebhookAlerter:
    """Posts newly qualified stocks to a webhook at most once per ticker per session."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self._lock = threading.Lock()
        # market -> (session date, tickers already alerted for that session)
        self._sent: Dict[str, Tuple[str, Set[str]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.ALERT_WEBHOOK_URL)

    def select(self, market: str, results: List[ScanResultItem]) -> List[ScanResultItem]:
        """New entries not yet alerted this session, honouring ALERT_INCLUDE_PARTIAL."""
        session = max((item.bar_date for item in results), default="")
        with self._lock:
            sent_session, sent = self._sent.get(market, ("", set()))
            already = sent if sent_session == session else set()
        return [
            item for item in results
            if item.is_new and item.ticker not in already
            and (self.config.ALERT_INCLUDE_PARTIAL or not item.session_partial)
        ]

    def _mark_sent(self, market: str, items: List[ScanResultItem]) -> None:
        session = max(item.bar_date for item in items)
        with self._lock:
            sent_session, sent = self._sent.get(market, ("", set()))
            if sent_session != session:
                sent = set()
            sent.update(item.ticker for item in items if item.bar_date == session)
            self._sent[market] = (session, sent)

    @staticmethod
    def build_payload(market: str, items: List[ScanResultItem]) -> dict:
        """Generic JSON body: `text` (Slack-style), `content` (Discord-style) and structured signals."""
        names = ", ".join(item.ticker for item in items[:TEXT_LIMIT])
        more = f" (+{len(items) - TEXT_LIMIT} more)" if len(items) > TEXT_LIMIT else ""
        partial = " Includes partial-session volume (indicative)." if any(i.session_partial for i in items) else ""
        text = f"[{market}] {len(items)} new qualifying stock(s): {names}{more}.{partial} {NOTICE}"
        signals = [
            {key: getattr(item, key) for key in (
                "ticker", "name", "price", "score", "rsi", "volume_ratio", "pe",
                "session_partial", "bar_date", "first_seen_at")}
            for item in items
        ]
        return {"text": text, "content": text[:2000], "market": market, "signals": signals, "notice": NOTICE}

    def notify(self, market: str, results: List[ScanResultItem]) -> int:
        """Send one webhook for this scan's new entries. Returns the number of stocks alerted."""
        if not self.enabled:
            return 0
        items = self.select(market, results)
        if not items:
            return 0
        try:
            response = httpx.post(
                self.config.ALERT_WEBHOOK_URL,
                json=self.build_payload(market, items),
                timeout=self.config.ALERT_TIMEOUT_SEC,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            logger.warning(f"[{market}] Alert webhook rejected the request: HTTP {error.response.status_code}")
            return 0
        except Exception as error:
            # Transport errors may echo the URL; log only the error type.
            logger.warning(f"[{market}] Alert webhook delivery failed: {type(error).__name__}")
            return 0
        self._mark_sent(market, items)
        logger.info(f"[{market}] Alerted {len(items)} new qualifying stock(s).")
        return len(items)
