"""Unit tests for SessionService."""
import pytest
from memory_bridge.models import Session
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.session_service import SessionService


@pytest.fixture
async def repo():
    import tempfile
    import os
    db_path = tempfile.mktemp(suffix=".db")
    r = SQLiteMemoryRepository(db_path=db_path)
    await r.initialize()
    yield r
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
async def service(repo):
    return SessionService(repo=repo)


@pytest.mark.asyncio
async def test_create_and_get_session(service):
    session = Session(session_id="s1", agent_id="a1")
    created = await service.create_session(session)
    assert created.session_id == "s1"
    assert created.agent_id == "a1"

    retrieved = await service.get_session("s1")
    assert retrieved is not None
    assert retrieved.session_id == "s1"


@pytest.mark.asyncio
async def test_get_nonexistent_session(service):
    result = await service.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_create_session_with_parent(service):
    parent = Session(session_id="parent", agent_id="a1")
    await service.create_session(parent)

    child = Session(session_id="child", agent_id="a2", parent_session_id="parent")
    created = await service.create_session(child)
    assert created.parent_session_id == "parent"


@pytest.mark.asyncio
async def test_lineage(service):
    grandparent = Session(session_id="gp", agent_id="a1")
    await service.create_session(grandparent)

    parent = Session(session_id="p1", agent_id="a2", parent_session_id="gp")
    await service.create_session(parent)

    child = Session(session_id="c1", agent_id="a3", parent_session_id="p1")
    await service.create_session(child)

    lineage = await service.get_lineage("c1")
    assert "gp" in lineage
    assert "p1" in lineage


@pytest.mark.asyncio
async def test_project_scoping(service):
    session = Session(session_id="s1", agent_id="a1")
    created = await service.create_session(session, project="proj-alpha")
    assert created.project == "proj-alpha"
