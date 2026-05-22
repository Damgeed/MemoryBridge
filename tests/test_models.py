import pytest
from datetime import datetime, timezone
from memory_bridge.models import (
    MemoryEntry, Session, HandoffPayload,
    MemoryCreate, MemoryQuery, MemorySearchResult,
)


class TestMemoryEntry:
    def test_default_id_generated(self):
        entry = MemoryEntry(session_id="s1", agent_id="a1", key="k", value="v")
        assert entry.id is not None
        assert len(entry.id) > 0

    def test_timestamps_auto_set(self):
        entry = MemoryEntry(session_id="s1", agent_id="a1", key="k", value="v")
        assert isinstance(entry.created_at, datetime)
        assert isinstance(entry.updated_at, datetime)

    def test_custom_tags(self):
        entry = MemoryEntry(
            session_id="s1", agent_id="a1", key="k", value="v",
            tags=["important", "user-preference"]
        )
        assert len(entry.tags) == 2


class TestSession:
    def test_minimal_session(self):
        s = Session(session_id="s1", agent_id="a1")
        assert s.parent_session_id is None
        assert s.metadata == {}

    def test_session_with_parent(self):
        s = Session(session_id="s1", agent_id="a1", parent_session_id="s0")
        assert s.parent_session_id == "s0"


class TestHandoffPayload:
    def test_default_handoff_type(self):
        p = HandoffPayload(
            from_agent_id="agent_a",
            to_agent_id="agent_b",
            session_id="s1",
            context={"key": "value"},
        )
        assert p.handoff_type == "full"
        assert p.timestamp is not None
