"""Offline tests for the approval queue and the channel layer (no network)."""

import pytest

from agentd.services.approval_queue import ApprovalQueue
from agentd.channels.base import (
    ChannelManager,
    MessagingChannel,
    format_approval_card,
    format_score_badge,
    format_task_reply,
)


def committed_result(final_answer=None):
    """A workflow result the policy would auto-commit."""
    return {
        "plan": {"task_summary": "Weather in Tokyo", "steps": []},
        "executor_results": [],
        "verification": {"source": "local", "confidence": 0.95, "issues": [],
                         "final_output": {"summary": "Weather in Tokyo: 22C, Sunny", "evidence": []}},
        "needs_review": False,
        "final_answer": final_answer,
    }


def review_result(final_answer="A drafted answer"):
    """A workflow result the policy would park for human review."""
    return {
        "plan": {"task_summary": "Something uncertain", "steps": []},
        "executor_results": [],
        "verification": {"source": "local", "confidence": 0.60, "issues": ["unclear"],
                         "final_output": {"summary": "Unsure about this", "evidence": []}},
        "needs_review": True,
        "final_answer": final_answer,
    }


class FakeWorkflowService:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def run_agent(self, task, on_event=None, gate=None, history=None):
        self.calls.append(task)
        return self.results.pop(0) if self.results else committed_result()


class FakeChannel(MessagingChannel):
    name = "fake"

    def __init__(self):
        super().__init__()
        self.sent = []
        self.approval_cards = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_text(self, chat_ref, text):
        self.sent.append((chat_ref, text))

    async def send_approval(self, approval):
        self.approval_cards.append(approval)


# ------------------------------------------------------------ formatting


def test_badge_labels_per_source():
    assert "Checked 95%" in format_score_badge({"source": "local", "confidence": 0.95})
    assert "AI-checked 80%" in format_score_badge({"source": "llm", "confidence": 0.8})
    assert "Checked" in format_score_badge({"source": "local", "confidence": 0.7})


def test_task_reply_direct_answer_has_badge():
    reply = format_task_reply("check grammar", committed_result(final_answer="Fixed text."))
    assert "Fixed text." in reply
    assert "Checked 95%" in reply


def test_task_reply_tool_summary_uses_verifier_summary():
    result = committed_result()
    result["final_answer"] = None
    reply = format_task_reply("weather", result)
    assert "Weather in Tokyo: 22C, Sunny" in reply


# -------------------------------------------------------- approval queue


def test_approval_lifecycle(tmp_path):
    approved_tasks = []
    queue = ApprovalQueue(
        db_path=str(tmp_path / "approvals.db"),
        on_approve=lambda a: approved_tasks.append(a.task),
    )
    approval = queue.create(
        channel="fake", chat_ref="chat-1", task="risky task",
        plan={"steps": []}, executor_results=[],
        verification={"confidence": 0.55}, final_answer="draft",
    )
    assert queue.get(approval.id).status == "pending"
    assert [a.id for a in queue.list()] == [approval.id]

    resolved = queue.resolve(approval.id, "approved", resolved_by="telegram:me")
    assert resolved.status == "approved"
    assert approved_tasks == ["risky task"]
    assert queue.resolve(approval.id, "approved") is None  # already resolved
    assert queue.list() == []


def test_approval_reject_does_not_fire_on_approve(tmp_path):
    approved_tasks = []
    queue = ApprovalQueue(
        db_path=str(tmp_path / "approvals.db"),
        on_approve=lambda a: approved_tasks.append(a.task),
    )
    approval = queue.create(
        channel="fake", chat_ref="c", task="t",
        plan={}, executor_results=[], verification={},
    )
    resolved = queue.resolve(approval.id, "rejected", resolved_by="discord:me")
    assert resolved.status == "rejected"
    assert approved_tasks == []


def test_approval_invalid_decision(tmp_path):
    queue = ApprovalQueue(db_path=str(tmp_path / "approvals.db"))
    approval = queue.create(channel="fake", chat_ref="c", task="t", plan={}, executor_results=[], verification={})
    with pytest.raises(ValueError):
        queue.resolve(approval.id, "maybe")


# -------------------------------------------------------- channel manager


@pytest.mark.asyncio
async def test_manager_committed_path_replies_with_badge():
    service = FakeWorkflowService([committed_result(final_answer="Fixed text.")])
    queue = ApprovalQueue(db_path="approvals_test_commit.db")
    try:
        manager = ChannelManager(service, queue)
        channel = FakeChannel()
        manager.register(channel)

        reply = await manager.handle_text(channel, "check grammar", "chat-1")

        assert reply is not None and "Fixed text." in reply
        assert "Checked" in reply
        assert channel.approval_cards == []
    finally:
        import os
        os.remove("approvals_test_commit.db")


@pytest.mark.asyncio
async def test_manager_review_path_creates_approval_card():
    approved_tasks = []
    service = FakeWorkflowService([review_result()])
    db = "approvals_test_review.db"
    queue = ApprovalQueue(db_path=db, on_approve=lambda a: approved_tasks.append(a.task))
    try:
        manager = ChannelManager(service, queue)
        channel = FakeChannel()
        manager.register(channel)

        reply = await manager.handle_text(channel, "risky task", "chat-9")

        assert reply is None  # approval card already sent, no double reply
        assert len(channel.approval_cards) == 1
        approval = channel.approval_cards[0]
        assert approval.channel == "fake"
        assert approval.chat_ref == "chat-9"
        assert "Approval needed" in format_approval_card(approval)

        note = await manager.handle_approval_callback(
            approval.id, "approved", resolved_by="slack:me"
        )
        assert "Approved" in note
        assert approved_tasks == ["risky task"]
    finally:
        import os
        os.remove(db)


@pytest.mark.asyncio
async def test_manager_unknown_approval_callback(tmp_path):
    service = FakeWorkflowService([])
    queue = ApprovalQueue(db_path=str(tmp_path / "approvals.db"))
    manager = ChannelManager(service, queue)
    note = await manager.handle_approval_callback("nope", "approved")
    assert "already handled" in note


@pytest.mark.asyncio
async def test_manager_ignores_blank_text(tmp_path):
    service = FakeWorkflowService([])
    manager = ChannelManager(service, ApprovalQueue(db_path=str(tmp_path / "approvals.db")))
    assert await manager.handle_text(FakeChannel(), "   ", "c") is None
    assert service.calls == []


# ------------------------------------------------- direct-answer graph path


class AgentService:
    """Workflow service whose agent asks before writing a file."""

    def __init__(self):
        self.histories = []

    async def run_agent(self, task, on_event=None, gate=None, history=None):
        self.histories.append(list(history or []))
        allowed = await gate.request("write", "Create file notes.txt", "hello")
        return {"final_answer": "Saved notes.txt" if allowed else "I did not write the file.",
                "verification": {"source": "local", "confidence": 0.9}}


@pytest.mark.asyncio
@pytest.mark.parametrize("decision, expected", [("approved", "Saved notes.txt"), ("rejected", "I did not write the file.")])
async def test_agent_question_is_answered_from_the_chat(tmp_path, decision, expected):
    import asyncio

    manager = ChannelManager(AgentService(), ApprovalQueue(db_path=str(tmp_path / "a.db")))
    channel = FakeChannel()
    manager.register(channel)

    run = asyncio.create_task(manager.handle_text(channel, "make a note", "chat-1"))
    while not channel.approval_cards:
        await asyncio.sleep(0.01)
    card = channel.approval_cards[0]
    assert "The agent wants to: Create file notes.txt" in format_approval_card(card) and not run.done()

    note = await manager.handle_approval_callback(card.id, decision, "telegram:me")
    reply = await run

    assert expected in reply and ("Allowed" in note or "Denied" in note)
    assert manager.pending_actions == {}


@pytest.mark.asyncio
async def test_chat_keeps_the_conversation_per_chat(tmp_path):
    import asyncio

    service = AgentService()
    manager = ChannelManager(service, ApprovalQueue(db_path=str(tmp_path / "a.db")))
    channel = FakeChannel()
    manager.register(channel)

    for turn in range(2):
        run = asyncio.create_task(manager.handle_text(channel, "make a note", "chat-1"))
        while len(channel.approval_cards) <= turn:  # wait for this turn's question
            await asyncio.sleep(0.01)
        await manager.handle_approval_callback(channel.approval_cards[-1].id, "approved")
        await run

    assert service.histories[0] == [] and len(service.histories[1]) == 2
