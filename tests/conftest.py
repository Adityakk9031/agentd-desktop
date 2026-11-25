"""Pytest fixtures for the offline unit test suite."""

import pytest

from agentd.safety.audit_log import AuditLog


@pytest.fixture
def audit_log(tmp_path) -> AuditLog:
    """Real AuditLog backed by a throwaway SQLite database."""
    return AuditLog(db_path=str(tmp_path / "audit.db"))


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    """Tests never read or write the real notes, past conversations or skills in data/."""
    monkeypatch.setenv("AGENTD_MEMORY_FILE", str(tmp_path / "memory.md"))
    monkeypatch.setenv("AGENTD_CONVERSATIONS_FILE", str(tmp_path / "conversations.jsonl"))
    monkeypatch.setenv("AGENTD_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("AGENTD_CONTROL_FRAME", "0")  # the frame has its own test
