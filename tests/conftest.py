"""Test isolation: keep tests from reading/writing the real data/ directory."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_disk_state(monkeypatch):
    import app.api.routes as routes
    import app.services.state as state

    monkeypatch.setattr(state, "save_last_scan", lambda *a, **k: None)
    monkeypatch.setattr(state, "load_last_scan_on_startup", lambda *a, **k: None)
    monkeypatch.setattr(routes, "save_settings_file", lambda *a, **k: None)
