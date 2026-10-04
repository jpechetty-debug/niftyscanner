"""Configurable NSE delivery-equity cost estimate; stored gross outcomes stay intact."""
from app.core.config import Settings


def cost_model(config: Settings) -> dict:
    return {"buy_stt_bps": config.PERFORMANCE_BUY_STT_BPS,
            "sell_stt_bps": config.PERFORMANCE_SELL_STT_BPS,
            "stamp_bps": config.PERFORMANCE_STAMP_BPS,
            "brokerage_bps_each_side": config.PERFORMANCE_BROKERAGE_BPS,
            "other_cost_bps_each_side": config.PERFORMANCE_OTHER_COST_BPS,
            "slippage_bps_each_side": config.PERFORMANCE_SLIPPAGE_BPS,
            "basis": "NSE delivery equity estimate; benchmark remains gross",
            "limitations": "Excludes fixed DP fees, statutory rounding and order-size effects; other charges are an estimate."}


def net_return(entry: float, exit_price: float, config: Settings) -> float:
    buy = (config.PERFORMANCE_BUY_STT_BPS + config.PERFORMANCE_STAMP_BPS
           + config.PERFORMANCE_BROKERAGE_BPS + config.PERFORMANCE_OTHER_COST_BPS) / 10000
    sell = (config.PERFORMANCE_SELL_STT_BPS + config.PERFORMANCE_BROKERAGE_BPS
            + config.PERFORMANCE_OTHER_COST_BPS) / 10000
    slip = config.PERFORMANCE_SLIPPAGE_BPS / 10000
    return 100 * (exit_price * (1 - slip) * (1 - sell) / (entry * (1 + slip) * (1 + buy)) - 1)
