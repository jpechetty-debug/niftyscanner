"""Unit tests for Universe loaders."""

from pathlib import Path
import pytest

from app.universe.nse import NSEUniverse


def test_nse_universe_loads_synthetic_fixture():
    """Verify loading synthetic universe CSV."""
    loader = NSEUniverse("tests/fixtures/nifty500_synthetic.csv")
    symbols = loader.load()

    assert len(symbols) == 10
    assert symbols[0].ticker == "TEST1.NS"
    assert symbols[0].symbol == "TEST1"
    assert symbols[0].company_name == "Synthetic Alpha Inc"
    assert symbols[0].market == "NSE"


def test_missing_file_raises_filenotfound():
    """Missing universe file must raise FileNotFoundError."""
    loader = NSEUniverse("data/non_existent_universe.csv")
    with pytest.raises(FileNotFoundError, match="Required universe file not found"):
        loader.load()


def test_missing_columns_raises_valueerror(tmp_path: Path):
    """Universe file missing required columns must raise ValueError."""
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("WrongCol1,WrongCol2\nA,B\n")

    loader = NSEUniverse(bad_csv)
    with pytest.raises(ValueError, match="must contain 'Symbol' and 'Company Name'"):
        loader.load()
