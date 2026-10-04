"""Unit tests for Universe loaders."""

from pathlib import Path
import pytest

from app.universe.nse import NSEUniverse


def test_nse_universe_loads_synthetic_fixture():
    """Verify loading synthetic universe CSV (DUMMY and RR rows are excluded)."""
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


def test_nse_universe_excludes_dummy_symbols(tmp_path: Path):
    """Symbols starting with DUMMY are excluded from the universe."""
    csv = tmp_path / "dummy_test.csv"
    csv.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Real Corp,Tech,REALCO,EQ,INE000000001\n"
        "Dummy HEG Ltd.,Capital Goods,DUMMYHEG,EQ,DUM545A01024\n"
        "Another Dummy,Test,DUMMYXYZ,EQ,DUM000000002\n"
    )
    loader = NSEUniverse(csv)
    symbols = loader.load()

    assert len(symbols) == 1
    assert symbols[0].symbol == "REALCO"
    # Verify no DUMMY symbols slipped through
    assert all(not s.symbol.upper().startswith("DUMMY") for s in symbols)


def test_nse_universe_excludes_series_rr(tmp_path: Path):
    """Series RR (REIT/InvIT units) are excluded from the universe."""
    csv = tmp_path / "rr_test.csv"
    csv.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Normal Stock,Finance,NORMAL,EQ,INE000000001\n"
        "Bagmane REIT,Realty,BAGMANE,RR,INE2OVN25015\n"
        "Embassy REIT,Realty,EMBASSY,RR,INE041025011\n"
        "Another Stock,Tech,ANOTHER,BE,INE000000002\n"
    )
    loader = NSEUniverse(csv)
    symbols = loader.load()

    assert len(symbols) == 2
    tickers = {s.symbol for s in symbols}
    assert tickers == {"NORMAL", "ANOTHER"}
    # Series BE (trade-for-trade) should NOT be excluded
    assert "ANOTHER" in tickers


def test_nse_universe_works_without_series_column(tmp_path: Path):
    """Loader should work gracefully when Series column is absent."""
    csv = tmp_path / "no_series.csv"
    csv.write_text(
        "Company Name,Symbol\n"
        "Alpha Corp,ALPHA\n"
        "Beta Corp,BETA\n"
    )
    loader = NSEUniverse(csv)
    symbols = loader.load()

    assert len(symbols) == 2
    assert symbols[0].symbol == "ALPHA"
    assert symbols[1].symbol == "BETA"


def test_nse_universe_real_file_excludes_known_issues():
    """Verify the real nifty500.csv excludes DUMMYHEG and series-RR REITs."""
    loader = NSEUniverse("data/nifty500.csv")
    symbols = loader.load()

    sym_set = {s.symbol for s in symbols}
    # DUMMYHEG must be excluded
    assert "DUMMYHEG" not in sym_set
    # Series RR REIT units must be excluded
    assert "BAGMANE" not in sym_set
    assert "BIRET" not in sym_set
    assert "EMBASSY" not in sym_set
    # Should be 497 (501 - 1 DUMMY - 3 RR)
    assert len(symbols) == 497

