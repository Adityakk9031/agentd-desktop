"""Pytest fixtures for the offline unit test suite."""

import pytest

from agentd.safety.audit_log import AuditLog


@pytest.fixture
def audit_log(tmp_path) -> AuditLog:
    """Real AuditLog backed by a throwaway SQLite database."""
    return AuditLog(db_path=str(tmp_path / "audit.db"))
