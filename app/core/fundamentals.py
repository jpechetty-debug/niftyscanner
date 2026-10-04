"""Distinguish confirmed non-positive earnings from unavailable fundamentals."""

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class NonPositiveEarnings:
    """Provider evidence for a business filter, without inventing a P/E ratio."""
    trailing_eps: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.trailing_eps) or self.trailing_eps > 0:
            raise ValueError("Confirmed trailing EPS must be finite and non-positive")


def confirmed_nonpositive_earnings(info: dict[str, Any]) -> NonPositiveEarnings | None:
    """Use reported trailing EPS only when trailing P/E is absent or null."""
    if info.get("trailingPE") is not None:
        return None
    raw = info.get("trailingEps")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        eps = float(raw)
    except (ValueError, TypeError):
        return None
    if math.isfinite(eps) and eps <= 0:
        return NonPositiveEarnings(eps)
    return None
