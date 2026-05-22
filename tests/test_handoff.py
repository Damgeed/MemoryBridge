import os
import pytest
import pytest_asyncio
from memory_bridge.storage import MemoryStorage
from memory_bridge.models import MemoryEntry, Session
from memory_bridge.handoff import HandoffProtocol, HandoffGuardrails, HandoffPayload


@pytest_asyncio.fixture
async def storage(tmp_path):
    db_path = str(tmp_path / "handoff_test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()
    yield s
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_handoff_prepare_with_memories(storage):
    for key, value in [("user_name", "Alice"), ("language", "en"), ("theme", "dark")]:
        await storage.store_memory(
            MemoryEntry(
                session_id="s1",
                agent_id="agent_a",
                key=key,
                value=value,
            )
        )

    protocol = HandoffProtocol(storage)
    result = await protocol.prepare_handoff(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s1",
    )

    assert result.success
    assert "user_name" in result.context
    assert result.context["user_name"] == "Alice"
    assert len(result.context) == 3


@pytest.mark.asyncio
async def test_handoff_with_tags(storage):
    await storage.store_memory(
        MemoryEntry(
            session_id="s1",
            agent_id="agent_a",
            key="api_endpoint",
            value="https://api.example.com",
            tags=["config"],
        )
    )
    await storage.store_memory(
        MemoryEntry(
            session_id="s1",
            agent_id="agent_a",
            key="user_name",
            value="Bob",
            tags=["user-preference"],
        )
    )

    protocol = HandoffProtocol(storage)
    result = await protocol.prepare_handoff(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s1",
        include_tags=["user-preference"],
    )

    assert len(result.context) == 1
    assert "user_name" in result.context


@pytest.mark.asyncio
async def test_handoff_blocks_sensitive_keys():
    payload = HandoffPayload(
        from_agent_id="a",
        to_agent_id="b",
        session_id="s1",
        context={"api_key": "sk-1234", "theme": "dark"},
    )
    sanitized = HandoffGuardrails.sanitize_context(payload.context)
    assert "api_key" not in sanitized
    assert "theme" in sanitized


@pytest.mark.asyncio
async def test_handoff_no_memories(storage):
    protocol = HandoffProtocol(storage)
    result = await protocol.prepare_handoff(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="nonexistent",
    )
    assert not result.success
    assert result.context == {}


@pytest.mark.asyncio
async def test_execute_handoff_stores_for_receiver(storage):
    await storage.store_memory(
        MemoryEntry(
            session_id="s1",
            agent_id="agent_a",
            key="project",
            value="Memory Bridge",
        )
    )

    protocol = HandoffProtocol(storage)
    result = await protocol.execute_handoff(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s1",
    )

    assert result.success
    mems = await storage.query_memories(agent_id="agent_b")
    assert len(mems) >= 1
    assert any("handoff:project" == m.key for m in mems)
