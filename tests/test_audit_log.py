"""Unit tests for the AuditLog."""

from agentd.safety.audit_log import AuditLog


def test_log_and_stats(audit_log: AuditLog):
    audit_log.log(node="verifier", event="a", value=0.95, confidence=0.95, outcome="ok")
    audit_log.log(node="permissions", event="b", value="deny", outcome="deny")
    audit_log.log(node="verifier", event="c")  # no outcome yet

    stats = audit_log.stats()
    assert stats["total_events"] == 3
    assert stats["by_node"] == {"verifier": 2, "permissions": 1}
    assert stats["by_outcome"]["ok"] == 1
    assert stats["by_outcome"]["deny"] == 1
    assert stats["by_outcome"]["none"] == 1


def test_set_outcome(audit_log: AuditLog):
    event_id = audit_log.log(node="approvals", event="approval abc")
    audit_log.set_outcome(event_id, "approved")

    assert audit_log.stats()["by_outcome"] == {"approved": 1}
