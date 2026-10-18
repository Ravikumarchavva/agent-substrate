"""CI guard: the shared-secret JWT scheme (JWT_SECRET, ENGINE_JWT_SECRET, HS256) is gone from the repository (ADR 0007)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

GUARD = Path(__file__).resolve().parents[3] / "scripts" / "check_no_shared_secrets.py"


def load_guard():
    spec = importlib.util.spec_from_file_location("check_no_shared_secrets", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_repository_has_no_shared_secret_jwt_scheme():
    assert load_guard().find_violations() == []


def test_the_guard_finds_what_it_looks_for(tmp_path, monkeypatch):
    guard = load_guard()
    (tmp_path / "service.py").write_text('secret = os.environ["JWT_SECRET"]\n')
    (tmp_path / "notes.md").write_text("JWT_SECRET was removed\n")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(
        guard, "tracked_files", lambda: [tmp_path / "service.py", tmp_path / "notes.md"]
    )
    assert [hit.split(":")[0] for hit in guard.find_violations()] == ["service.py"]
