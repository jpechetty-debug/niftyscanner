"""Command-line interface for running stock screening scans."""

from __future__ import annotations

import argparse
import sys
from loguru import logger

from app.core.config import load_settings
from app.market.calendar import MarketCalendar
from app.market.clock import SystemClock
from app.providers.fake_provider import FakeProvider
from app.providers.yfinance_provider import YFinanceProvider
from app.services.scanner import StockScannerService
from app.universe.nse import NSEUniverse
from app.universe.nyse import NYSEUniverse


def print_results_table(results, limit: int = 20) -> None:
    """Print top ranked stocks in a clean table format."""
    print("\n" + "=" * 105)
    print(
        f"{'#':<3} {'TICKER':<12} {'COMPANY NAME':<26} {'PRICE':>9} {'P/E':>7} "
        f"{'RSI':>7} {'VOL_RATIO':>10} {'SCORE':>8} {'PARTIAL':>8}"
    )
    print("-" * 105)

    for idx, r in enumerate(results[:limit], 1):
        name = r.name[:25]
        partial_str = "YES" if r.session_partial else "NO"
        print(
            f"{idx:<3} {r.ticker:<12} {name:<26} {r.price:>9.2f} {r.pe:>7.2f} "
            f"{r.rsi:>7.2f} {r.volume_ratio:>10.2f}x {r.score:>8.4f} {partial_str:>8}"
        )

    if not results:
        print("   No stocks met all screening criteria.")
    print("=" * 105 + "\n")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Run local-first stock screener for Nifty 500 equities."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run in offline test mode using synthetic fixtures and FakeProvider.",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="NSE",
        help="Market to screen (default: NSE).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of top ranked stocks to display (default: 20).",
    )

    args = parser.parse_args()
    config = load_settings()

    # Configure logger level
    logger.remove()
    logger.add(sys.stderr, level=config.LOG_LEVEL)

    clock = SystemClock()
    market_upper = args.market.strip().upper()
    calendar = MarketCalendar(market=market_upper)

    if args.offline:
        provider = FakeProvider(print_banner=True)
        if market_upper == "NYSE":
            universe_loader = NYSEUniverse("tests/fixtures/otherlisted_synthetic.txt", config=config)
        else:
            universe_loader = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")
    else:
        provider = YFinanceProvider(config=config, clock=clock)
        if market_upper == "NYSE":
            universe_loader = NYSEUniverse(config.NYSE_UNIVERSE_PATH, config=config)
        else:
            universe_loader = NSEUniverse(config.NSE_UNIVERSE_PATH)

    try:
        symbols = universe_loader.load()
    except Exception as e:
        logger.error(f"Failed to load universe: {e}")
        sys.exit(1)

    scanner = StockScannerService(
        config=config,
        provider=provider,
        calendar=calendar,
        clock=clock,
    )

    results, funnel, failures, req_count, scan_seconds, is_success = scanner.run_scan(symbols)
    if not is_success:
        print("\nWARNING: This scan encountered systemic issues and is marked as FAILED.")

    print_results_table(results, limit=args.limit)

    print("SCREENING METRICS & FUNNEL:")
    print(f"  Total Universe:           {funnel.universe}")
    print(f"  Successfully Fetched:     {funnel.fetched}")
    print(f"  Data / System Failures:   {funnel.failed}")
    print(f"  Filtered by RSI (<= {config.MIN_RSI}):     {funnel.filtered_rsi}")
    print(f"  Filtered by Vol (<= {config.MIN_VOLUME_RATIO}x):  {funnel.filtered_volume}")
    print(f"  Passed Stage 1 (RSI & Vol): {funnel.passed_rsi_volume}")
    print(f"  Filtered by P/E (>= {config.MAX_PE}):     {funnel.filtered_pe}")
    print(f"  Passed Stage 2 (Qualified): {funnel.passed_pe}")
    print(f"  Scan Duration:            {scan_seconds} seconds")
    print(f"  Total Requests:           {req_count}")

    if failures:
        print(f"\nFAILED SYMBOLS SUMMARY ({len(failures)} total):")
        for f in failures[:5]:
            print(f"  - [{f.stage.value.upper()}] {f.ticker}: {f.code.value} - {f.message}")
        if len(failures) > 5:
            print(f"  ... and {len(failures) - 5} more failures.")


if __name__ == "__main__":
    main()
