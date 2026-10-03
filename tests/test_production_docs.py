"""Tests for Phase 5 documentation and production readiness specifications."""
from pathlib import Path

def test_production_documentation_integrity():
    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    prod_doc = docs_dir / "PRODUCTION.md"
    assert prod_doc.exists(), "docs/PRODUCTION.md must exist"

    content = prod_doc.read_text(encoding="utf-8")
    
    # Must contain required Phase 5 subjects
    assert "Redis" in content, "Must include Redis migration guide"
    assert "Horizontal Scaling" in content or "Celery" in content, "Must include scaling guidance"
    assert "yfinance" in content, "Must mention yfinance"
    assert "unofficial" in content.lower(), "Must note yfinance is unofficial"
    assert "personal" in content.lower(), "Must note yfinance is intended for personal use"
    assert "session_partial" in content, "Must document session_partial limitations"
    assert "Docker" in content, "Must provide containerization instructions"
