"""S4 preregistration. No strategy selection or implicit dates/capital."""
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings
from app.swing.config import SwingSettings
from app.swing.setups import configuration, digest


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Gates(Frozen):
    min_folds: int = Field(2, ge=1, le=20)
    min_trades: int = Field(100, ge=2, le=100000)
    min_periods: int = Field(20, ge=2, le=10000)
    min_sectors: int = Field(3, ge=1, le=100)
    min_neighbors: int = Field(2, ge=1, le=20)
    embargo_sessions: int = Field(5, ge=1, le=250)
    block_sessions: int = Field(20, ge=1, le=250)
    bootstrap_replicates: int = Field(2000, ge=100, le=10000)
    seed: int = Field(1729, ge=0, le=2147483647)
    alpha: float = Field(.05, gt=0, le=.1)
    max_drawdown: float = Field(.10, gt=0, le=1)
    max_exposure: float = Field(.60, gt=0, le=1)
    max_participation: float = Field(.005, gt=0, le=1)
    min_fill_coverage: float = Field(.80, gt=0, le=1)
    neighbor_ratio: float = Field(.50, gt=0, le=1)
    baseline_exposure: float = Field(.60, gt=0, le=1)
    extra_cost_bps: tuple[float, ...] = (10., 25.)
    entry_delay_sessions: int = Field(1, ge=1, le=10)
    adverse_gap_bps: float = Field(100., gt=0, le=1000)
    exit_halt_sessions: int = Field(1, ge=1, le=10)
    execution_stress_fraction: float = Field(.10, gt=0, le=1)
    max_parameter_change_fraction: float = Field(.20, gt=0, le=.50)

    @model_validator(mode="after")
    def costs(self):
        if not self.extra_cost_bps or tuple(sorted(set(self.extra_cost_bps))) != self.extra_cost_bps or not all(0 < x <= 1000 for x in self.extra_cost_bps):
            raise ValueError("Increasing positive extra per-side costs required")
        return self


class ValidationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_nested_delimiter="__", hide_input_in_errors=True)
    VALIDATION_DB_PATH: str = "data/swing_validation.db"
    VALIDATION_SIGNING_KEY: SecretStr = SecretStr("")
    VALIDATION_GATES: Gates = Field(default_factory=Gates)

    @model_validator(mode="after")
    def paths_and_key(self):
        if not self.VALIDATION_DB_PATH.strip():
            raise ValueError("Validation path cannot be blank")
        key = self.VALIDATION_SIGNING_KEY.get_secret_value()
        if key and len(key.encode()) < 32:
            raise ValueError("Dedicated signing key needs at least 32 bytes")
        return self


class Window(Frozen):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    start: date
    end: date


class Fold(Frozen):
    train: Window
    validation: Window
    test: Window


class Variant(Frozen):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    role: Literal["primary", "parameter"]
    configuration: dict[str, str | bool | int | float | None]
    accounts: dict[str, str]
    perturbation: str | None = None

    @property
    def config_hash(self):
        return digest(tuple(sorted(self.configuration.items())))


class Study(Frozen):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    hypothesis: str = Field(min_length=20, max_length=5000)
    strategies: tuple[Literal["DAILY_BREAKOUT_V1", "DAILY_PULLBACK_V1"], ...]
    capital_inr: float = Field(gt=0)
    search_budget: int = Field(ge=1, le=30)
    variants: tuple[Variant, ...]
    folds: tuple[Fold, ...]
    holdout: Window
    gates: Gates = Field(default_factory=Gates)

    def windows(self, *, holdout=False):
        if holdout:
            return [("holdout", self.holdout)]
        return [(phase, getattr(fold, phase)) for fold in self.folds for phase in ("train", "validation", "test")]

    @model_validator(mode="after")
    def freeze_budget(self):
        from decimal import Decimal, InvalidOperation
        try:
            if Decimal(str(self.capital_inr)) % Decimal(".01"):
                raise ValueError("Capital must be an explicit amount in paise")
        except InvalidOperation as exc:
            raise ValueError("Capital exceeds supported monetary precision") from exc
        if not self.strategies or len(set(self.strategies)) != len(self.strategies):
            raise ValueError("Unique strategies required")
        if self.search_budget != len(self.variants) or len({v.name for v in self.variants}) != len(self.variants):
            raise ValueError("Every tried variant must exhaust the frozen search budget")
        if sum(v.role == "primary" for v in self.variants) != 1:
            raise ValueError("Exactly one preselected primary required")
        windows = self.windows() + self.windows(holdout=True)
        ids = {w.id for _, w in windows}
        if len(ids) != len(windows) or len(self.folds) < self.gates.min_folds:
            raise ValueError("Unique windows and sufficient folds required")
        accounts = [a for v in self.variants for a in v.accounts.values()]
        if len(accounts) != len(set(accounts)) or any(set(v.accounts) != ids for v in self.variants):
            raise ValueError("A distinct account for every variant/window is required")
        primary = next(v for v in self.variants if v.role == "primary")
        hashes = [v.config_hash for v in self.variants]
        if len(set(hashes)) != len(hashes):
            raise ValueError("Repeated configurations are not independent parameter neighbors")
        allowed = set(SwingSettings.model_fields) - {"SWING_PAPER_DB_PATH", "SWING_CAPITAL_INR", "SWING_ENABLED", "SWING_MODE", "SWING_REPLAY_ENABLED", "SWING_REPLAY_MAX_SESSIONS"}
        for variant in self.variants:
            cfg = Settings(_env_file=None, **variant.configuration)
            if dict(configuration(cfg)) != variant.configuration or cfg.SWING_CAPITAL_INR != self.capital_inr:
                raise ValueError("Freeze the complete validated S2 configuration and explicit capital")
            differences = {k for k in primary.configuration if primary.configuration[k] != variant.configuration[k]}
            if variant.role == "parameter":
                if differences != {variant.perturbation} or not (variant.perturbation or "").startswith("SWING_") or variant.perturbation in {
                    "SWING_CAPITAL_INR", "SWING_ENABLED", "SWING_MODE", "SWING_REPLAY_ENABLED", "SWING_REPLAY_MAX_SESSIONS"}:
                    raise ValueError("Parameter neighbors change exactly one declared strategy/risk parameter")
                base, value = primary.configuration[variant.perturbation], variant.configuration[variant.perturbation]
                if variant.perturbation not in allowed or isinstance(base, bool) or not isinstance(base, (int, float)) or not isinstance(value, (int, float)) or base == 0 or abs(value/base-1) > self.gates.max_parameter_change_fraction + 1e-12:
                    raise ValueError("Neighbors must be bounded numeric strategy/risk perturbations")
            elif differences:
                raise ValueError("Execution variants freeze the same strategy/cost configuration")
        return self


def schedule(study, calendar):
    """Freeze exact sessions, purge signal tails, embargo information boundaries."""
    cal = calendar.calendar
    horizon = max(max(int(v.configuration[k]) for k in ("SWING_BREAKOUT_MAX_HOLD", "SWING_PULLBACK_MAX_HOLD"))
                  + int(v.configuration["SWING_ENTRY_VALID_SESSIONS"]) + 1 for v in study.variants)
    result = {}
    for phase, w in study.windows() + study.windows(holdout=True):
        if w.start > w.end or not cal.is_session(str(w.start)) or not cal.is_session(str(w.end)):
            raise ValueError("Window endpoints must be ordered exchange sessions")
        days = [str(s.date()) for s in cal.sessions_in_range(w.start, w.end)]
        if len(days) <= horizon:
            raise ValueError("Window too short after purging the maximum label horizon")
        result[w.id] = {"phase": phase, "sessions": days, "signal_end": days[-horizon-1],
                        "seed": str(cal.previous_session(str(w.start)).date())}
    def separated(left, right):
        if left.end >= right.start or len(cal.sessions_in_range(left.end, right.start))-2 < study.gates.embargo_sessions:
            raise ValueError("Windows overlap or lack the declared session embargo")
    for i, fold in enumerate(study.folds):
        separated(fold.train, fold.validation)
        separated(fold.validation, fold.test)
        if i:
            previous = study.folds[i-1]
            if fold.train.start != previous.train.start or fold.train.end <= previous.train.end or fold.train.end < previous.test.end:
                raise ValueError("Training must expand through the preceding test")
            separated(previous.test, fold.test)
    separated(study.folds[-1].test, study.holdout)
    return {"windows": result, "horizon": horizon, "block_sessions": max(horizon, study.gates.block_sessions)}


def code_hashes():
    # Includes provider, calendar, settings, and protocol/research implementation.
    root = Path(__file__).parents[2]
    files = sorted((root / "app/swing").glob("*.py")) + [root/p for p in (
        "app/core/config.py", "app/market/calendar.py", "app/providers/swing_archive.py",
        "scripts/swing_validate.py", "docs/SWING_S4_CONTRACT.md", "requirements.txt")]
    import hashlib
    return {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def runtime_fingerprint():
    import importlib.metadata
    import platform
    import sqlite3
    return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            **{name: importlib.metadata.version(name) for name in ("numpy", "pydantic", "pandas", "exchange-calendars")}}
