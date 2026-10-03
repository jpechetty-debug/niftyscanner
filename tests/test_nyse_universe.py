"""Unit tests for NYSE Universe constituent loading and exclusion heuristics."""

from pathlib import Path
import pytest

from app.core.config import Settings
from app.universe.nyse import NYSEUniverse


def test_nyse_universe_heuristic_exclusions_synthetic():
    """Verify all NYSE inclusion/exclusion rules and heuristics on synthetic data."""
    config = Settings(
        NYSE_EXCLUDE_SYMBOL_SUFFIXES=[".WS", ".WSA", ".WSB", ".U", ".UN", ".RT", ".R"],
        NYSE_EXCLUDE_NAME_PATTERN=r"\b(preferred|warrants?|units?|rights?)\b",
    )
    loader = NYSEUniverse(
        file_path="tests/fixtures/otherlisted_synthetic.txt",
        config=config,
    )
    symbols = loader.load()

    # In our synthetic fixture:
    # Included:
    # A (Exchange N, Common Stock)
    # IBM (Exchange N, Common Stock)
    # BRK.B -> converted to BRK-B
    # Total survivors must be 3
    assert len(symbols) == 3

    ticker_map = {s.symbol: s for s in symbols}
    assert "A" in ticker_map
    assert ticker_map["A"].ticker == "A"
    assert ticker_map["A"].market == "NYSE"

    assert "IBM" in ticker_map
    assert ticker_map["IBM"].ticker == "IBM"

    # Dot to hyphen conversion per Section 6
    assert "BRK.B" in ticker_map
    assert ticker_map["BRK.B"].ticker == "BRK-B"

    # Verify exclusions:
    # Non-NYSE exchange (P, Z) excluded
    assert "P1" not in ticker_map
    assert "Z1" not in ticker_map

    # ETF excluded
    assert "ETF1" not in ticker_map

    # Test Issue excluded
    assert "TESTY" not in ticker_map

    # Symbol with $ excluded
    assert "BAC$A" not in ticker_map

    # Suffixes (.WS, .U, .RT) excluded
    assert "ABC.WS" not in ticker_map
    assert "DEF.U" not in ticker_map
    assert "GHI.RT" not in ticker_map

    # Name pattern matching (preferred, units, rights, warrant) excluded
    assert "XYZ" not in ticker_map
    assert "UVW" not in ticker_map
    assert "RST" not in ticker_map
    assert "MNO" not in ticker_map


def test_nyse_missing_file_raises_filenotfound():
    """Missing NYSE file must raise FileNotFoundError."""
    loader = NYSEUniverse("data/missing_otherlisted.txt")
    with pytest.raises(FileNotFoundError, match="Required NYSE universe file not found"):
        loader.load()


def test_nyse_missing_required_columns(tmp_path: Path):
    """File missing required columns must raise ValueError."""
    bad_file = tmp_path / "bad_otherlisted.txt"
    bad_file.write_text("ACT Symbol|Security Name|Exchange\nA|Test|N\n")

    loader = NYSEUniverse(bad_file)
    with pytest.raises(ValueError, match="missing required columns"):
        loader.load()
