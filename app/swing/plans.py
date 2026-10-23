"""Immutable research calculations. These objects cannot arm or execute orders."""
from dataclasses import dataclass
from datetime import date, datetime
from app.swing.models import utc
from app.swing.explanations import RuleCheck


@dataclass(frozen=True)
class Evidence:
    vintage: str
    source_revision: str
    available_at: datetime
    hashes: tuple[str, ...]
    identity_verified: bool = False
    publication_verified: bool = False
    trading_basis_verified: bool = False
    sector: str | None = None
    sector_verified: bool = False
    earnings_verified: bool = False
    surveillance_verified: bool = False
    delivery_status: str = "unavailable"

    def __post_init__(self):
        utc(self.available_at)
        if not self.vintage or not self.source_revision or not self.hashes:
            raise ValueError("Explicit vintage, source revision and evidence hashes required")


@dataclass(frozen=True)
class Features:
    close: float
    atr: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    volume_ratio: float
    average_volume: float
    average_traded_value: float
    excess_strength: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class Costs:
    buy_multiplier: float
    sell_multiplier: float
    parameters: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Plan:
    id: str
    strategy: str
    config_hash: str
    implementation_hash: str
    configuration: tuple[tuple[str, object], ...]
    data_hash: str
    evidence: Evidence
    ticker: str
    session: date
    decision_at: datetime
    series: str | None
    isin: str | None
    price_basis: str
    features: Features
    entry_min: float
    entry_max: float
    first_entry_session: date
    expiry_session: date
    stop: float
    target: float
    target_basis: str
    gross_rr: float
    net_rr: float
    costs: Costs
    max_hold_sessions: int
    passed_gates: tuple[str, ...]
    missing_gates: tuple[str, ...]
    rationale: str
    requested_risk_inr: float | None = None
    quantity: int | None = None
    cash_required: float | None = None
    allocation_status: str = "RECONCILED_ACCOUNT_REQUIRED"
    entry_policy: str = "Next eligible session open inside interval; otherwise unfilled"
    time_exit_policy: str = "Next tradable open after holding-limit close"
    model_status: str = "RESEARCH_ONLY"
    lifecycle: str = "CANDIDATE"
    validation_record_id: str | None = None
    paper_eligible: bool = False


@dataclass(frozen=True)
class Rejection:
    ticker: str
    strategy: str | None
    category: str  # filtered: rule rejection; failed: unavailable/invalid data
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Evaluation:
    plans: tuple[Plan, ...] = ()
    rejected: tuple[Rejection, ...] = ()
    checks: tuple[RuleCheck, ...] = ()


def priority(plan: Plan):
    """Stable research ordering; deliberately unrelated to the legacy score."""
    return (-plan.net_rr, -plan.features.excess_strength[-1][1],
            -plan.features.volume_ratio, plan.ticker, plan.strategy)
