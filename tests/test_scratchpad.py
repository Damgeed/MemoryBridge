"""Tests for Scratchpad service and repository layers."""

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from memory_bridge.models import Scratchpad, ScratchpadCreate, ScratchpadAppend
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.scratchpad_service import ScratchpadService


@pytest_asyncio.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "scratchpad_test.db")
    r = SQLiteMemoryRepository(db_path=db_path)
    await r.initialize()
    yield r
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture
async def service(repo):
    return ScratchpadService(repo=repo)


@pytest.mark.asyncio
async def test_create_scratchpad(service):
    """Creating a scratchpad returns it with the correct initial state."""
    pad = await service.create_scratchpad(
        session_id="s1",
        agent_id="agent_a",
        project_id="proj1",
        content="Hello, world!",
        ttl_seconds=1800,
    )
    assert pad.id is not None
    assert pad.session_id == "s1"
    assert pad.agent_id == "agent_a"
    assert pad.project_id == "proj1"
    assert pad.content == ["Hello, world!"]
    assert pad.contributors == ["agent_a"]
    assert pad.ttl_seconds == 1800
    assert pad.expires_at > pad.created_at


@pytest.mark.asyncio
async def test_get_scratchpad(service):
    """Getting a scratchpad by ID returns the correct pad."""
    created = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1", content="test",
    )
    fetched = await service.get_scratchpad(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.content == ["test"]


@pytest.mark.asyncio
async def test_get_scratchpad_not_found(service):
    """Getting a non-existent scratchpad returns None."""
    fetched = await service.get_scratchpad("nonexistent")
    assert fetched is None


@pytest.mark.asyncio
async def test_append_to_scratchpad(service):
    """Appending content adds a new entry and tracks the contributor."""
    pad = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1", content="first",
    )
    updated = await service.append_to_scratchpad(
        id=pad.id,
        agent_id="agent_b",
        content="second entry",
    )
    assert len(updated.content) == 2
    assert updated.content == ["first", "second entry"]
    assert "agent_a" in updated.contributors
    assert "agent_b" in updated.contributors


@pytest.mark.asyncio
async def test_append_to_expired_scratchpad(service, repo):
    """Appending to an expired scratchpad raises ValueError."""
    # Create a scratchpad that's already expired
    pad = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1",
        content="will expire", ttl_seconds=1,
    )
    # Manually set expires_at in the past
    pad.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await repo.update_scratchpad(pad)

    with pytest.raises(ValueError, match="not found or expired"):
        await service.append_to_scratchpad(pad.id, "agent_b", "new content")


@pytest.mark.asyncio
async def test_delete_scratchpad(service):
    """Deleting a scratchpad removes it."""
    pad = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1", content="to delete",
    )
    await service.delete_scratchpad(pad.id)
    fetched = await service.get_scratchpad(pad.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_scratchpad_not_found(service):
    """Deleting a non-existent scratchpad raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        await service.delete_scratchpad("nonexistent")


@pytest.mark.asyncio
async def test_list_active_scratchpads(service, repo):
    """Listing active scratchpads only returns non-expired ones for the project."""
    # Create two active pads for proj1
    pad1 = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1", content="active 1",
    )
    pad2 = await service.create_scratchpad(
        session_id="s2", agent_id="agent_b", project_id="proj1", content="active 2",
    )
    # Create an expired pad for proj1
    pad3 = await service.create_scratchpad(
        session_id="s3", agent_id="agent_a", project_id="proj1",
        content="expired", ttl_seconds=1,
    )
    pad3.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await repo.update_scratchpad(pad3)

    # Create an active pad for a different project
    await service.create_scratchpad(
        session_id="s4", agent_id="agent_c", project_id="proj2", content="other project",
    )

    active = await service.list_active_scratchpads("proj1")
    assert len(active) == 2
    active_ids = {p.id for p in active}
    assert pad1.id in active_ids
    assert pad2.id in active_ids
    assert pad3.id not in active_ids


@pytest.mark.asyncio
async def test_cleanup_expired(service, repo):
    """Cleanup removes all expired scratchpads and returns the count."""
    # Create one active and one expired
    active_pad = await service.create_scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1", content="active",
    )
    expired_pad = await service.create_scratchpad(
        session_id="s2", agent_id="agent_b", project_id="proj1",
        content="expired", ttl_seconds=1,
    )
    expired_pad.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await repo.update_scratchpad(expired_pad)

    count = await service.cleanup_expired()
    assert count == 1

    # Active pad should still exist
    assert await service.get_scratchpad(active_pad.id) is not None
    # Expired pad should be gone
    assert await service.get_scratchpad(expired_pad.id) is None


@pytest.mark.asyncio
async def test_scratchpad_create_model():
    """ScratchpadCreate validates correctly."""
    payload = ScratchpadCreate(
        session_id="s1",
        agent_id="agent_a",
        content="hello",
        ttl_seconds=3600,
        project_id="proj1",
    )
    assert payload.session_id == "s1"
    assert payload.ttl_seconds == 3600


@pytest.mark.asyncio
async def test_scratchpad_append_model():
    """ScratchpadAppend validates correctly."""
    payload = ScratchpadAppend(agent_id="agent_a", content="more content")
    assert payload.agent_id == "agent_a"
    assert payload.content == "more content"


@pytest.mark.asyncio
async def test_repo_create_and_get(repo):
    """Direct repo operations for scratchpads."""
    now = datetime.now(timezone.utc)
    pad = Scratchpad(
        session_id="s1",
        agent_id="agent_a",
        project_id="proj1",
        content=["test"],
        contributors=["agent_a"],
        created_at=now,
        expires_at=now + timedelta(seconds=1800),
        ttl_seconds=1800,
    )
    created = await repo.create_scratchpad(pad)
    assert created.id == pad.id

    fetched = await repo.get_scratchpad(pad.id)
    assert fetched is not None
    assert fetched.content == ["test"]


@pytest.mark.asyncio
async def test_repo_delete(repo):
    """Direct repo delete operation."""
    now = datetime.now(timezone.utc)
    pad = Scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1",
        content=["x"], contributors=["agent_a"],
        created_at=now, expires_at=now + timedelta(seconds=1800), ttl_seconds=1800,
    )
    await repo.create_scratchpad(pad)
    assert await repo.delete_scratchpad(pad.id) is True
    assert await repo.delete_scratchpad("nonexistent") is False


@pytest.mark.asyncio
async def test_repo_cleanup_expired(repo):
    """Direct repo cleanup expired operation."""
    now = datetime.now(timezone.utc)
    # Create expired pad
    expired = Scratchpad(
        session_id="s1", agent_id="agent_a", project_id="proj1",
        content=["old"], contributors=["agent_a"],
        created_at=now - timedelta(hours=1),
        expires_at=now - timedelta(seconds=10),
        ttl_seconds=30,
    )
    await repo.create_scratchpad(expired)

    # Create active pad
    active = Scratchpad(
        session_id="s2", agent_id="agent_b", project_id="proj1",
        content=["new"], contributors=["agent_b"],
        created_at=now, expires_at=now + timedelta(hours=1), ttl_seconds=3600,
    )
    await repo.create_scratchpad(active)

    count = await repo.cleanup_expired_scratchpads()
    assert count == 1

    assert await repo.get_scratchpad(expired.id) is None
    assert await repo.get_scratchpad(active.id) is not None
