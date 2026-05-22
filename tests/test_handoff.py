import asyncio
import os
import pytest
import pytest_asyncio
from memory_bridge.storage import MemoryStorage
from memory_bridge.models import MemoryEntry, Session
from memory_bridge.handoff import HandoffProtocol, HandoffGuardrails, HandoffPayload, HandoffError, HandoffResult


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


@pytest.mark.asyncio
async def test_concurrent_handoff_blocked(storage):
    """Two concurrent prepare_handoffs on the same session: one succeeds, one gets HandoffError."""
    # Seed some memories for the session
    await storage.store_memory(
        MemoryEntry(session_id="s_concurrent", agent_id="agent_a", key="foo", value="bar")
    )

    protocol = HandoffProtocol(storage)

    # Monkey-patch to add delay so the lock is held long enough for the second call to time out
    original_internal = protocol._prepare_handoff_internal

    async def slow_internal(*args, **kwargs):
        await asyncio.sleep(0.5)
        return await original_internal(*args, **kwargs)

    protocol._prepare_handoff_internal = slow_internal

    # Use a short timeout so the blocked call fails fast
    original_acquire = protocol._acquire_session_lock
    protocol._acquire_session_lock = lambda sid, timeout=0.3: original_acquire(sid, timeout=timeout)

    results = await asyncio.gather(
        protocol.prepare_handoff(
            from_agent_id="agent_a", to_agent_id="agent_b", session_id="s_concurrent"
        ),
        protocol.prepare_handoff(
            from_agent_id="agent_a", to_agent_id="agent_c", session_id="s_concurrent"
        ),
        return_exceptions=True,
    )

    protocol._prepare_handoff_internal = original_internal
    protocol._acquire_session_lock = original_acquire

    successes = [r for r in results if isinstance(r, HandoffResult) and r.success]
    errors = [r for r in results if isinstance(r, HandoffError)]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(errors) == 1, f"Expected 1 HandoffError, got {len(errors)} errors: {errors}"
    assert "busy with another handoff" in errors[0].detail
    assert errors[0].status_code == 409


@pytest.mark.asyncio
async def test_handoff_different_sessions_independent(storage):
    """Concurrent prepare_handoffs on different sessions both succeed."""
    # Seed memories for both sessions
    await storage.store_memory(
        MemoryEntry(session_id="s_alpha", agent_id="agent_a", key="alpha_key", value="alpha_val")
    )
    await storage.store_memory(
        MemoryEntry(session_id="s_beta", agent_id="agent_b", key="beta_key", value="beta_val")
    )

    protocol = HandoffProtocol(storage)

    # Add a slight delay to both calls to verify they run concurrently
    original_internal = protocol._prepare_handoff_internal

    async def slow_internal(*args, **kwargs):
        await asyncio.sleep(0.15)
        return await original_internal(*args, **kwargs)

    protocol._prepare_handoff_internal = slow_internal

    results = await asyncio.gather(
        protocol.prepare_handoff(
            from_agent_id="agent_a", to_agent_id="agent_b", session_id="s_alpha"
        ),
        protocol.prepare_handoff(
            from_agent_id="agent_b", to_agent_id="agent_c", session_id="s_beta"
        ),
        return_exceptions=True,
    )

    protocol._prepare_handoff_internal = original_internal

    # Both should succeed
    assert len(results) == 2
    for r in results:
        assert isinstance(r, HandoffResult), f"Expected HandoffResult, got {type(r).__name__}: {r}"
        assert r.success, f"Handoff failed: {r.summary}"
