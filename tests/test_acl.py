"""Tests for the Memory Permissions system — scope-based isolation and agent_type whitelist.

Tests cover:
1. PermissionScope enum and hierarchy utilities
2. ACLService scope checks (check_scope, require_scope)
3. ACLService agent_type whitelist checks
4. Backward compatibility: no scope = fallback to boolean flags
5. SQLite repository persistence of new fields
6. End-to-end integration with memory_controller patterns
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

import pytest

from memory_bridge.models import (
    AgentPermission,
    AgentPermissionUpdate,
    PermissionScope,
    scope_to_level,
    scope_implies,
    derive_scope_bools,
)
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.acl_service import ACLService


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def sqlite_repo():
    """Create a temporary SQLite repository for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    repo = SQLiteMemoryRepository(db_path=db_path)
    await repo.initialize()
    yield repo
    os.unlink(db_path)


@pytest.fixture
async def acl(sqlite_repo):
    """Create an ACL service backed by the test SQLite repo."""
    return ACLService(storage=sqlite_repo)


# ── Tests: PermissionScope Enum & Utilities ─────────────────────────────────


class TestScopeUtilities:
    """Test the scope hierarchy and utility functions."""

    def test_scope_to_level(self):
        assert scope_to_level(None) == 0
        assert scope_to_level("read") == 1
        assert scope_to_level("write") == 2
        assert scope_to_level("admin") == 3
        assert scope_to_level("unknown") == 0

    def test_scope_implies_none(self):
        """No scope (None) implies everything — backward compat."""
        assert scope_implies(None, "read") is True
        assert scope_implies(None, "write") is True
        assert scope_implies(None, "admin") is True

    def test_scope_implies_hierarchy(self):
        assert scope_implies("read", "read") is True
        assert scope_implies("read", "write") is False
        assert scope_implies("read", "admin") is False

        assert scope_implies("write", "read") is True
        assert scope_implies("write", "write") is True
        assert scope_implies("write", "admin") is False

        assert scope_implies("admin", "read") is True
        assert scope_implies("admin", "write") is True
        assert scope_implies("admin", "admin") is True

    def test_derive_scope_bools_read(self):
        """Read scope → read only."""
        perm = AgentPermission(agent_id="agent-1", scope=PermissionScope.read)
        r, w, d = derive_scope_bools(perm)
        assert r is True
        assert w is False
        assert d is False

    def test_derive_scope_bools_write(self):
        """Write scope → read + write."""
        perm = AgentPermission(agent_id="agent-1", scope=PermissionScope.write)
        r, w, d = derive_scope_bools(perm)
        assert r is True
        assert w is True
        assert d is False

    def test_derive_scope_bools_admin(self):
        """Admin scope → read + write + delete."""
        perm = AgentPermission(agent_id="agent-1", scope=PermissionScope.admin)
        r, w, d = derive_scope_bools(perm)
        assert r is True
        assert w is True
        assert d is True

    def test_derive_scope_bools_no_scope(self):
        """No scope → use individual booleans as-is (backward compat)."""
        perm = AgentPermission(
            agent_id="agent-1",
            scope=None,
            can_read=False,
            can_write=True,
            can_delete=False,
        )
        r, w, d = derive_scope_bools(perm)
        assert r is False
        assert w is True
        assert d is False

    def test_model_post_init_derives_bools(self):
        """model_post_init should derive booleans from scope."""
        perm = AgentPermission(agent_id="agent-1", scope=PermissionScope.admin)
        assert perm.can_read is True
        assert perm.can_write is True
        assert perm.can_delete is True

    def test_agent_permission_allowed_types_default(self):
        """allowed_agent_types should be None by default (all types allowed)."""
        perm = AgentPermission(agent_id="agent-1")
        assert perm.allowed_agent_types is None


# ── Tests: ACLService Scope Checks ──────────────────────────────────────────


class TestACLServiceScope:
    """Test scope-based permission checks via ACLService."""

    async def _set_perm(self, repo, agent_id: str, scope: Optional[str], project: Optional[str] = None):
        """Helper to set a permission with scope."""
        perm = AgentPermission(
            agent_id=agent_id,
            project=project,
            scope=PermissionScope(scope) if scope else None,
        )
        await repo.set_agent_permission(perm)

    async def test_no_rule_means_full_access(self, acl: ACLService):
        """No permission rule → all operations allowed (backward compat)."""
        assert await acl.check_scope("unknown-agent", "read") is True
        assert await acl.check_scope("unknown-agent", "write") is True
        assert await acl.check_scope("unknown-agent", "admin") is True

    async def test_read_scope_allows_read_only(self, acl: ACLService, sqlite_repo):
        await self._set_perm(sqlite_repo, "agent-1", "read")
        assert await acl.check_scope("agent-1", "read") is True
        assert await acl.check_scope("agent-1", "write") is False
        assert await acl.check_scope("agent-1", "admin") is False

    async def test_write_scope_allows_read_and_write(self, acl: ACLService, sqlite_repo):
        await self._set_perm(sqlite_repo, "agent-1", "write")
        assert await acl.check_scope("agent-1", "read") is True
        assert await acl.check_scope("agent-1", "write") is True
        assert await acl.check_scope("agent-1", "admin") is False

    async def test_admin_scope_allows_all(self, acl: ACLService, sqlite_repo):
        await self._set_perm(sqlite_repo, "agent-1", "admin")
        assert await acl.check_scope("agent-1", "read") is True
        assert await acl.check_scope("agent-1", "write") is True
        assert await acl.check_scope("agent-1", "admin") is True

    async def test_project_scoped_permissions(self, acl: ACLService, sqlite_repo):
        """Permissions should be project-scoped."""
        await self._set_perm(sqlite_repo, "agent-1", "read", project="proj-a")
        # agent-1 has read on proj-a
        assert await acl.check_scope("agent-1", "read", project="proj-a") is True
        assert await acl.check_scope("agent-1", "write", project="proj-a") is False
        # No rule on proj-b → full access (default)
        assert await acl.check_scope("agent-1", "read", project="proj-b") is True
        assert await acl.check_scope("agent-1", "write", project="proj-b") is True

    async def test_require_scope_raises_on_denial(self, acl: ACLService, sqlite_repo):
        await self._set_perm(sqlite_repo, "agent-1", "read")
        # Should not raise
        await acl.require_scope("agent-1", "read")
        # Should raise
        with pytest.raises(PermissionError, match="does not have 'write' scope"):
            await acl.require_scope("agent-1", "write")
        with pytest.raises(PermissionError, match="does not have 'admin' scope"):
            await acl.require_scope("agent-1", "admin")

    async def test_legacy_backward_compat(self, acl: ACLService, sqlite_repo):
        """No scope set → fall back to individual booleans."""
        perm = AgentPermission(
            agent_id="legacy-agent",
            scope=None,
            can_read=True,
            can_write=False,
            can_delete=True,
        )
        await sqlite_repo.set_agent_permission(perm)
        assert await acl.check_scope("legacy-agent", "read") is True
        assert await acl.check_scope("legacy-agent", "write") is False
        assert await acl.check_scope("legacy-agent", "admin") is True

    async def test_legacy_require_methods_still_work(self, acl: ACLService, sqlite_repo):
        """require_read/require_write/require_delete should still work."""
        await self._set_perm(sqlite_repo, "agent-1", "read")
        await acl.require_read("agent-1")  # Should not raise
        with pytest.raises(PermissionError):
            await acl.require_write("agent-1")
        with pytest.raises(PermissionError):
            await acl.require_delete("agent-1")


# ── Tests: Agent-Type Whitelist ─────────────────────────────────────────────


class TestACLServiceAgentType:
    """Test agent_type whitelist checks."""

    async def test_no_whitelist_allows_all(self, acl: ACLService, sqlite_repo):
        """No allowed_agent_types set → all types allowed."""
        perm = AgentPermission(agent_id="agent-1", allowed_agent_types=None)
        await sqlite_repo.set_agent_permission(perm)
        assert await acl.check_agent_type("agent-1", "default") is True
        assert await acl.check_agent_type("agent-1", "researcher") is True
        assert await acl.check_agent_type("agent-1", "coder") is True

    async def test_empty_whitelist_allows_all(self, acl: ACLService, sqlite_repo):
        """Empty allowed_agent_types list → all types allowed."""
        perm = AgentPermission(agent_id="agent-1", allowed_agent_types=[])
        await sqlite_repo.set_agent_permission(perm)
        assert await acl.check_agent_type("agent-1", "default") is True
        assert await acl.check_agent_type("agent-1", "anything") is True

    async def test_whitelist_restricts_types(self, acl: ACLService, sqlite_repo):
        """Set whitelist → only allowed types."""
        perm = AgentPermission(
            agent_id="agent-1",
            allowed_agent_types=["researcher", "analyst"],
        )
        await sqlite_repo.set_agent_permission(perm)
        assert await acl.check_agent_type("agent-1", "researcher") is True
        assert await acl.check_agent_type("agent-1", "analyst") is True
        assert await acl.check_agent_type("agent-1", "coder") is False
        assert await acl.check_agent_type("agent-1", "default") is False

    async def test_require_agent_type_raises(self, acl: ACLService, sqlite_repo):
        perm = AgentPermission(
            agent_id="agent-1",
            allowed_agent_types=["researcher"],
        )
        await sqlite_repo.set_agent_permission(perm)
        await acl.require_agent_type("agent-1", "researcher")  # Should not raise
        with pytest.raises(PermissionError, match="not allowed"):
            await acl.require_agent_type("agent-1", "coder")

    async def test_no_perm_rule_allows_all_types(self, acl: ACLService):
        """No permission rule → all types allowed."""
        assert await acl.check_agent_type("unknown-agent", "anything") is True


# ── Tests: SQLite Repository Persistence ────────────────────────────────────


class TestSQLitePersistence:
    """Test that scope and allowed_agent_types are stored and retrieved correctly."""

    async def test_store_and_retrieve_scope(self, sqlite_repo):
        perm = AgentPermission(
            agent_id="test-agent",
            project="test-project",
            scope=PermissionScope.admin,
            allowed_agent_types=["coder", "researcher"],
        )
        await sqlite_repo.set_agent_permission(perm)

        retrieved = await sqlite_repo.get_agent_permission("test-agent", "test-project")
        assert retrieved is not None
        assert retrieved.agent_id == "test-agent"
        assert retrieved.project == "test-project"
        assert retrieved.scope == PermissionScope.admin
        assert retrieved.allowed_agent_types == ["coder", "researcher"]
        assert retrieved.can_read is True  # Derived from admin
        assert retrieved.can_write is True
        assert retrieved.can_delete is True

    async def test_store_without_scope_backward_compat(self, sqlite_repo):
        """Storing without scope should preserve boolean flags."""
        perm = AgentPermission(
            agent_id="legacy-agent",
            project=None,
            scope=None,
            allowed_agent_types=None,
            can_read=True,
            can_write=False,
            can_delete=True,
        )
        await sqlite_repo.set_agent_permission(perm)

        retrieved = await sqlite_repo.get_agent_permission("legacy-agent")
        assert retrieved is not None
        assert retrieved.scope is None
        assert retrieved.allowed_agent_types is None
        assert retrieved.can_read is True
        assert retrieved.can_write is False
        assert retrieved.can_delete is True

    async def test_list_permissions_includes_new_fields(self, sqlite_repo):
        perm1 = AgentPermission(
            agent_id="agent-a",
            scope=PermissionScope.read,
            allowed_agent_types=["viewer"],
        )
        perm2 = AgentPermission(
            agent_id="agent-b",
            scope=PermissionScope.admin,
            allowed_agent_types=["admin"],
        )
        await sqlite_repo.set_agent_permission(perm1)
        await sqlite_repo.set_agent_permission(perm2)

        all_perms = await sqlite_repo.list_agent_permissions()
        assert len(all_perms) == 2
        for p in all_perms:
            if p.agent_id == "agent-a":
                assert p.scope == PermissionScope.read
                assert p.allowed_agent_types == ["viewer"]
            elif p.agent_id == "agent-b":
                assert p.scope == PermissionScope.admin
                assert p.allowed_agent_types == ["admin"]

    async def test_api_key_creation_with_scope(self, sqlite_repo):
        result = await sqlite_repo.create_api_key(
            label="test-key",
            project_id="proj-1",
            scope="write",
        )
        assert result["scope"] == "write"
        assert result["label"] == "test-key"
        assert result["project_id"] == "proj-1"
        assert result["is_active"] is True

        # Also verify it appears in list
        keys = await sqlite_repo.list_api_keys()
        matching = [k for k in keys if k["label"] == "test-key"]
        assert len(matching) == 1
        assert matching[0]["scope"] == "write"

    async def test_api_key_creation_without_scope(self, sqlite_repo):
        """Backward compat: no scope → full access."""
        result = await sqlite_repo.create_api_key(
            label="legacy-key",
            project_id="proj-1",
        )
        assert result["scope"] is None

        keys = await sqlite_repo.list_api_keys()
        matching = [k for k in keys if k["label"] == "legacy-key"]
        assert len(matching) == 1
        assert matching[0]["scope"] is None


# ── Tests: AgentPermissionUpdate Model ──────────────────────────────────────


class TestAgentPermissionUpdate:
    def test_update_with_scope(self):
        update = AgentPermissionUpdate(
            agent_id="agent-1",
            scope=PermissionScope.admin,
            allowed_agent_types=["admin"],
        )
        assert update.agent_id == "agent-1"
        assert update.scope == PermissionScope.admin
        assert update.allowed_agent_types == ["admin"]

    def test_update_without_scope(self):
        update = AgentPermissionUpdate(
            agent_id="agent-1",
        )
        assert update.scope is None
        assert update.allowed_agent_types is None

    def test_partial_update(self):
        update = AgentPermissionUpdate(can_read=True, can_delete=True)
        assert update.can_read is True
        assert update.can_delete is True
        assert update.can_write is None
        assert update.scope is None
