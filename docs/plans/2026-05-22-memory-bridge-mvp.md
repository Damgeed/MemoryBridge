# Memory Bridge MVP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a cross-session memory persistence system for multi-agent teams — a middleware layer that lets AI agents share context across sessions.

**Architecture:** FastAPI server with SQLite storage, RESTful API for CRUD memory operations, plus a handoff protocol for agent-to-agent context passing. Pluggable storage backend for future upgrades (PostgreSQL, Redis).

**Tech Stack:** Python 3.9+, FastAPI, SQLite (via aiosqlite), Pydantic v2, pytest

**MVP Scope:** "Shed before cathedral" — a working API server with session read/write, memory tagging, and agent handoff guardrails.

---

### Task 1: Project scaffolding

**Objective:** Set up Python project structure with pyproject.toml, dependencies, and package layout

**Files:**
- Create: `pyproject.toml`
- Create: `src/memory_bridge/__init__.py`
- Create: `src/memory_bridge/main.py` (minimal FastAPI app)
- Create: `tests/__init__.py`
- Create: `tests/test_server.py` (smoke test)
- Create: `.gitignore`

**Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "memory-bridge"
version = "0.1.0"
description = "Cross-session memory persistence for multi-agent teams"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "pydantic>=2.0",
    "aiosqlite>=0.19.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21.0",
    "httpx>=0.25.0",
]
```

**Step 2: Write .gitignore**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
*.db
.env
```

**Step 3: Write src/memory_bridge/__init__.py**

```python
"""Memory Bridge — cross-session memory persistence for multi-agent teams."""

__version__ = "0.1.0"
```

**Step 4: Write src/memory_bridge/main.py (minimal)**

```python
from fastapi import FastAPI

app = FastAPI(
    title="Memory Bridge",
    version="0.1.0",
    description="Cross-session memory persistence for multi-agent teams",
)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory-bridge"}
```

**Step 5: Write tests/test_server.py**

```python
from httpx import AsyncClient, ASGITransport
import pytest
from memory_bridge.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "memory-bridge"}
```

**Step 6: Install and run smoke test**

Run:
```bash
cd ~/MemoryBridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Expected: 1 passed

**Step 7: Commit**

```bash
git add .
git commit -m "chore: initial project scaffolding"
```

---

### Task 2: Data models

**Objective:** Define Pydantic models for memory entries, sessions, and handoff payloads

**Files:**
- Create: `src/memory_bridge/models.py`
- Create: `tests/test_models.py`

**Step 1: Write src/memory_bridge/models.py**

```python
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """A single memory entry stored for an agent session."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    agent_id: str
    key: str
    value: Any
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    """Represents an agent's working session."""
    session_id: str
    agent_id: str
    parent_session_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class HandoffPayload(BaseModel):
    """Payload for agent-to-agent context handoff."""
    from_agent_id: str
    to_agent_id: str
    session_id: str
    context: dict[str, Any]
    handoff_type: str = "full"  # "full", "summary", "selective"
    include_tags: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryCreate(BaseModel):
    """Request body to create a memory entry."""
    session_id: str
    agent_id: str
    key: str
    value: Any
    tags: list[str] = Field(default_factory=list)


class MemoryQuery(BaseModel):
    """Parameters for querying memories."""
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=500)


class MemorySearchResult(BaseModel):
    """Search result wrapper."""
    entries: list[MemoryEntry]
    total: int
```

Add import at top:
```python
from uuid import uuid4
```

**Step 2: Write tests/test_models.py**

```python
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
```

**Step 3: Run tests**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: 2 passed

**Step 4: Commit**

```bash
git add .
git commit -m "feat: add data models for memory, sessions, and handoff"
```

---

### Task 3: Storage backend (SQLite)

**Objective:** Implement SQLite-based storage with async CRUD operations

**Files:**
- Create: `src/memory_bridge/storage.py`
- Create: `tests/test_storage.py`

**Step 1: Write src/memory_bridge/storage.py**

```python
import aiosqlite
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from .models import MemoryEntry, Session, HandoffPayload


class MemoryStorage:
    """SQLite-backed async storage for memory entries."""

    def __init__(self, db_path: str = "memory_bridge.db"):
        self.db_path = db_path

    async def initialize(self):
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    parent_session_id TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_session
                    ON memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_memories_agent
                    ON memories(agent_id);
                CREATE INDEX IF NOT EXISTS idx_memories_key
                    ON memories(key);
            """)
            await db.commit()

    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a memory entry."""
        async with aiosqlite.connect(self.db_path) as db:
            import json
            await db.execute(
                """INSERT OR REPLACE INTO memories
                   (id, session_id, agent_id, key, value, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id, entry.session_id, entry.agent_id,
                    entry.key, json.dumps(entry.value),
                    json.dumps(entry.tags),
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                )
            )
            await db.commit()
        return entry

    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """Get a memory entry by ID."""
        import json
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return MemoryEntry(
                id=row["id"],
                session_id=row["session_id"],
                agent_id=row["agent_id"],
                key=row["key"],
                value=json.loads(row["value"]),
                tags=json.loads(row["tags"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    async def query_memories(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Query memories with filters."""
        import json
        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if keys:
            placeholders = ",".join("?" for _ in keys)
            conditions.append(f"key IN ({placeholders})")
            params.extend(keys)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM memories WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
                (*params, limit)
            )
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                entry = MemoryEntry(
                    id=row["id"],
                    session_id=row["session_id"],
                    agent_id=row["agent_id"],
                    key=row["key"],
                    value=json.loads(row["value"]),
                    tags=json.loads(row["tags"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                # Client-side tag filtering
                if tags:
                    entry_tags = set(entry.tags)
                    if not entry_tags.intersection(tags):
                        continue
                results.append(entry)

            return results

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory entry. Returns True if deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def store_session(self, session: Session) -> Session:
        """Store a session record."""
        import json
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, agent_id, parent_session_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    session.session_id, session.agent_id,
                    session.parent_session_id,
                    session.created_at.isoformat(),
                    json.dumps(session.metadata),
                )
            )
            await db.commit()
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        import json
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return Session(
                session_id=row["session_id"],
                agent_id=row["agent_id"],
                parent_session_id=row["parent_session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]),
            )
```

**Step 2: Write tests/test_storage.py**

```python
import pytest
import os
from memory_bridge.storage import MemoryStorage
from memory_bridge.models import MemoryEntry, Session


@pytest.fixture
async def storage(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()
    yield s
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_store_and_get_memory(storage):
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="greeting", value="hello")
    stored = await storage.store_memory(entry)
    assert stored.id == entry.id

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "hello"


@pytest.mark.asyncio
async def test_query_by_session(storage):
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="k2", value="v2")
    e3 = MemoryEntry(session_id="s2", agent_id="a2", key="k3", value="v3")
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_by_tags(storage):
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1", tags=["important"])
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="k2", value="v2", tags=["archived"])
    await storage.store_memory(e1)
    await storage.store_memory(e2)

    results = await storage.query_memories(session_id="s1", tags=["important"])
    assert len(results) == 1
    assert results[0].key == "k1"


@pytest.mark.asyncio
async def test_delete_memory(storage):
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="k", value="v")
    await storage.store_memory(entry)
    assert await storage.delete_memory(entry.id) is True
    assert await storage.get_memory(entry.id) is None


@pytest.mark.asyncio
async def test_store_and_get_session(storage):
    session = Session(session_id="s1", agent_id="a1")
    stored = await storage.store_session(session)
    assert stored.session_id == "s1"

    retrieved = await storage.get_session("s1")
    assert retrieved is not None
    assert retrieved.agent_id == "a1"
```

**Step 3: Run storage tests**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: 7 passed

**Step 4: Commit**

```bash
git add .
git commit -m "feat: add SQLite storage backend with async CRUD"
```

---

### Task 4: REST API endpoints

**Objective:** Wire up the FastAPI server with CRUD endpoints for memories and sessions

**Files:**
- Modify: `src/memory_bridge/main.py`
- Create: `src/memory_bridge/dependencies.py`
- Modify: `tests/test_server.py`

**Step 1: Write src/memory_bridge/dependencies.py**

```python
from .storage import MemoryStorage

storage = MemoryStorage()


async def get_storage() -> MemoryStorage:
    return storage
```

**Step 2: Rewrite src/memory_bridge/main.py**

```python
from fastapi import FastAPI, Depends, HTTPException
from .dependencies import get_storage
from .models import MemoryEntry, MemoryCreate, MemoryQuery, Session, HandoffPayload
from .storage import MemoryStorage

app = FastAPI(
    title="Memory Bridge",
    version="0.1.0",
    description="Cross-session memory persistence for multi-agent teams",
)


@app.on_event("startup")
async def startup():
    storage = get_storage()
    await storage.initialize()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory-bridge"}


# --- Memory CRUD ---

@app.post("/memories", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    storage: MemoryStorage = Depends(get_storage),
):
    entry = MemoryEntry(
        session_id=payload.session_id,
        agent_id=payload.agent_id,
        key=payload.key,
        value=payload.value,
        tags=payload.tags,
    )
    return await storage.store_memory(entry)


@app.get("/memories/{memory_id}", response_model=MemoryEntry)
async def get_memory(
    memory_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    entry = await storage.get_memory(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return entry


@app.post("/memories/query")
async def query_memories(
    query: MemoryQuery,
    storage: MemoryStorage = Depends(get_storage),
):
    entries = await storage.query_memories(
        session_id=query.session_id,
        agent_id=query.agent_id,
        tags=query.tags,
        keys=query.keys,
        limit=query.limit,
    )
    return {"entries": [e.model_dump() for e in entries], "total": len(entries)}


@app.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    deleted = await storage.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# --- Session CRUD ---

@app.post("/sessions", response_model=Session)
async def create_session(
    session: Session,
    storage: MemoryStorage = Depends(get_storage),
):
    return await storage.store_session(session)


@app.get("/sessions/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    session = await storage.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
```

**Step 3: Rewrite tests/test_server.py**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from memory_bridge.main import app


@pytest.fixture(autouse=True)
async def setup_storage():
    """Override storage with a test DB for each test."""
    from memory_bridge.dependencies import get_storage, storage
    import tempfile, os
    db_path = tempfile.mktemp(suffix=".db")
    storage.db_path = db_path
    await storage.initialize()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "memory-bridge"}


@pytest.mark.asyncio
async def test_create_and_get_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create
        resp = await client.post("/memories", json={
            "session_id": "s1",
            "agent_id": "a1",
            "key": "user_name",
            "value": "Alice",
            "tags": ["user-preference"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "user_name"
        mem_id = data["id"]

        # Get by ID
        resp = await client.get(f"/memories/{mem_id}")
        assert resp.status_code == 200
        assert resp.json()["value"] == "Alice"


@pytest.mark.asyncio
async def test_query_memories():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "k1", "value": "v1", "tags": ["important"]
        })
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "k2", "value": "v2", "tags": ["normal"]
        })

        resp = await client.post("/memories/query", json={
            "session_id": "s1",
            "limit": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_delete_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "temp", "value": "x"
        })
        mem_id = resp.json()["id"]

        resp = await client.delete(f"/memories/{mem_id}")
        assert resp.status_code == 200

        resp = await client.get(f"/memories/{mem_id}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_on_missing_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/memories/nonexistent")
        assert resp.status_code == 404
```

**Step 4: Fix the startup event issue**

The `startup` event references `get_storage()` but that returns the module-level `storage` object. The fixture replaces `storage.db_path` but the startup event runs once. For tests, we bypass startup by calling `await storage.initialize()` directly in the fixture, which works since the test client doesn't trigger the lifespan events by default.

**Step 5: Run tests**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: 6-7 passed (all server + model + storage tests)

**Step 6: Commit**

```bash
git add .
git commit -m "feat: add REST API endpoints for memories and sessions"
```

---

### Task 5: Handoff protocol

**Objective:** Implement the agent-to-agent context handoff logic with guardrails

**Files:**
- Create: `src/memory_bridge/handoff.py`
- Create: `tests/test_handoff.py`

**Step 1: Write src/memory_bridge/handoff.py**

```python
"""Agent-to-agent context handoff with guardrails."""

from typing import Any, Optional
from .models import HandoffPayload, MemoryEntry, MemoryQuery
from .storage import MemoryStorage


class HandoffResult:
    """Result of a handoff operation."""
    def __init__(
        self,
        success: bool,
        summary: str,
        context: dict[str, Any],
        warnings: list[str] = None,
    ):
        self.success = success
        self.summary = summary
        self.context = context
        self.warnings = warnings or []


class HandoffGuardrails:
    """Guardrails for safe agent-to-agent context handoff."""

    MAX_CONTEXT_SIZE = 100_000  # characters
    BLOCKED_KEYS = {"credentials", "api_key", "token", "password", "secret"}

    @classmethod
    def validate_payload(cls, payload: HandoffPayload) -> list[str]:
        """Validate handoff payload. Returns list of warnings."""
        warnings = []

        if payload.from_agent_id == payload.to_agent_id:
            warnings.append("Source and destination agents are identical")

        context_size = len(str(payload.context))
        if context_size > cls.MAX_CONTEXT_SIZE:
            warnings.append(
                f"Context size ({context_size} chars) exceeds limit "
                f"({cls.MAX_CONTEXT_SIZE})"
            )

        # Check for blocked keys
        for key in payload.context:
            if key.lower() in cls.BLOCKED_KEYS:
                warnings.append(f"Context contains potentially sensitive key: '{key}'")

        if payload.handoff_type not in ("full", "summary", "selective"):
            warnings.append(f"Unknown handoff type: {payload.handoff_type}")

        return warnings

    @classmethod
    def sanitize_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive keys from context."""
        return {
            k: v for k, v in context.items()
            if k.lower() not in cls.BLOCKED_KEYS
        }


class HandoffProtocol:
    """Orchestrates agent-to-agent handoff with guardrails."""

    def __init__(self, storage: MemoryStorage):
        self.storage = storage

    async def prepare_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
    ) -> HandoffResult:
        """Prepare context for handoff between agents."""
        # Gather memories for the session
        memories = await self.storage.query_memories(
            session_id=session_id,
            agent_id=from_agent_id,
            tags=include_tags,
        )

        if not memories:
            return HandoffResult(
                success=False,
                summary=f"No memories found for agent '{from_agent_id}' in session '{session_id}'",
                context={},
                warnings=["No memories to hand off"],
            )

        # Build context from memories
        context: dict[str, Any] = {}
        for mem in memories:
            context[mem.key] = mem.value

        # Create handoff payload
        payload = HandoffPayload(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            session_id=session_id,
            context=context,
            handoff_type=handoff_type,
            include_tags=include_tags or [],
        )

        # Run guardrails
        warnings = HandoffGuardrails.validate_payload(payload)
        sanitized = HandoffGuardrails.sanitize_context(payload.context)

        summary = (
            f"Handoff from '{from_agent_id}' to '{to_agent_id}': "
            f"{len(sanitized)} context keys, type={handoff_type}"
        )

        return HandoffResult(
            success=len(warnings) == 0 or all(
                "sensitive" in w or "identical" in w for w in warnings
            ),
            summary=summary,
            context=sanitized,
            warnings=warnings,
        )

    async def execute_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
        new_session_id: Optional[str] = None,
    ) -> HandoffResult:
        """Execute a handoff: prepare context, store it for the receiving agent."""
        result = await self.prepare_handoff(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            session_id=session_id,
            handoff_type=handoff_type,
            include_tags=include_tags,
        )

        if not result.success and not result.context:
            return result

        # Store context for the receiving agent
        target_session = new_session_id or session_id
        for key, value in result.context.items():
            entry = MemoryEntry(
                session_id=target_session,
                agent_id=to_agent_id,
                key=f"handoff:{key}",
                value=value,
                tags=["handoff", f"from:{from_agent_id}"],
            )
            await self.storage.store_memory(entry)

        result.summary += f" | Stored {len(result.context)} keys for '{to_agent_id}'"
        return result
```

**Step 2: Write tests/test_handoff.py**

```python
import pytest
import os
from memory_bridge.storage import MemoryStorage
from memory_bridge.models import MemoryEntry, Session
from memory_bridge.handoff import HandoffProtocol, HandoffGuardrails, HandoffPayload


@pytest.fixture
async def storage(tmp_path):
    db_path = str(tmp_path / "handoff_test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()
    yield s
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_handoff_prepare_with_memories(storage):
    # Seed some memories
    for key, value in [("user_name", "Alice"), ("language", "en"), ("theme", "dark")]:
        await storage.store_memory(MemoryEntry(
            session_id="s1", agent_id="agent_a",
            key=key, value=value,
        ))

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
    await storage.store_memory(MemoryEntry(
        session_id="s1", agent_id="agent_a",
        key="api_endpoint", value="https://api.example.com",
        tags=["config"],
    ))
    await storage.store_memory(MemoryEntry(
        session_id="s1", agent_id="agent_a",
        key="user_name", value="Bob",
        tags=["user-preference"],
    ))

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
    await storage.store_memory(MemoryEntry(
        session_id="s1", agent_id="agent_a",
        key="project", value="Memory Bridge",
    ))

    protocol = HandoffProtocol(storage)
    result = await protocol.execute_handoff(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s1",
    )

    assert result.success
    # Agent B should now have the memory
    mems = await storage.query_memories(agent_id="agent_b")
    assert len(mems) >= 1
    assert any("handoff:project" == m.key for m in mems)
```

**Step 3: Run all tests**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: 11+ passed

**Step 4: Commit**

```bash
git add .
git commit -m "feat: add agent-to-agent handoff protocol with guardrails"
```

---

### Task 6: Wire handoff into API

**Objective:** Expose handoff endpoints via the FastAPI server

**Files:**
- Modify: `src/memory_bridge/main.py`
- Modify: `tests/test_server.py`

**Step 1: Add handoff endpoints to main.py**

Add to `src/memory_bridge/main.py`:

```python
from .handoff import HandoffProtocol, HandoffPayload as HandoffRequest


@app.post("/handoff/prepare")
async def prepare_handoff(
    payload: HandoffRequest,
    storage: MemoryStorage = Depends(get_storage),
):
    protocol = HandoffProtocol(storage)
    result = await protocol.prepare_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }


@app.post("/handoff/execute")
async def execute_handoff(
    payload: HandoffRequest,
    storage: MemoryStorage = Depends(get_storage),
):
    protocol = HandoffProtocol(storage)
    result = await protocol.execute_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }
```

Fix the import conflict — rename the handoff model import:

```python
from .handoff import HandoffProtocol
from .models import MemoryEntry, MemoryCreate, MemoryQuery, Session
from .models import HandoffPayload as HandoffPayloadModel
```

And use `HandoffPayloadModel` in the endpoint to avoid name collision with `HandoffRequest`.

**Step 2: Add handoff tests to test_server.py**

```python
@pytest.mark.asyncio
async def test_handoff_prepare_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a memory first
        await client.post("/memories", json={
            "session_id": "s-handoff", "agent_id": "agent_a",
            "key": "project", "value": "Memory Bridge",
        })

        # Prepare handoff
        resp = await client.post("/handoff/prepare", json={
            "from_agent_id": "agent_a",
            "to_agent_id": "agent_b",
            "session_id": "s-handoff",
            "context": {},
            "handoff_type": "summary",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "project" in data["context"]
```

**Step 3: Run all tests**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: 12+ passed

**Step 4: Commit**

```bash
git add .
git commit -m "feat: wire handoff protocol into REST API"
```

---

### Task 7: Documentation and README

**Objective:** Write comprehensive README and architecture doc

**Files:**
- Modify: `README.md`
- Create: `docs/architecture.md`

**Step 1: Write README.md**

```markdown
# Memory Bridge

Cross-session memory persistence for multi-agent AI teams.

Memory Bridge is a middleware layer that lets AI agents share context across sessions. It provides:

- **Session Persistence** — Store and retrieve agent context across sessions
- **Memory Tagging** — Organize memories with tags for selective retrieval
- **Agent Handoff** — Pass context between agents with guardrails
- **Pluggable Storage** — SQLite out of the box, upgrade to PostgreSQL/Redis later

## Quick Start

```bash
pip install memory-bridge

# Start the server
memory-bridge
```

## API

### Memories

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/memories` | Create a memory entry |
| GET | `/memories/{id}` | Get a memory by ID |
| POST | `/memories/query` | Query memories with filters |
| DELETE | `/memories/{id}` | Delete a memory |

### Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions` | Create a session |
| GET | `/sessions/{id}` | Get a session |

### Handoff

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/handoff/prepare` | Prepare context for agent handoff |
| POST | `/handoff/execute` | Execute agent-to-agent handoff |

## Development

```bash
git clone https://github.com/Damgeed/MemoryBridge.git
cd MemoryBridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Architecture

See `docs/architecture.md` for detailed design.
```

**Step 2: Write docs/architecture.md**

```markdown
# Memory Bridge Architecture

## Overview

Memory Bridge is a RESTful middleware service that provides durable, cross-session memory for AI agents. It sits between agents and their storage, enabling context sharing across sessions and between different agents.

## Core Concepts

### Sessions
A session represents a continuous interaction between an agent and its environment. Sessions can have parent-child relationships (e.g., a sub-task spawned from a main task).

### Memories
Key-value pairs stored per-session, per-agent. Each memory can be tagged for selective retrieval.

### Handoff
The protocol by which one agent passes context to another. Includes guardrails to prevent sensitive data leakage.

## Storage Layer

The default storage backend is SQLite via `aiosqlite`. The `MemoryStorage` class provides:

- `store_memory()` — Create or update a memory
- `get_memory()` — Retrieve by ID
- `query_memories()` — Filter by session, agent, tags, or keys
- `delete_memory()` — Remove a memory
- `store_session()` / `get_session()` — Session lifecycle

Storage is swappable — implement the same interface for PostgreSQL, Redis, or any backend.

## Guardrails

The handoff protocol includes:

- **Context size limits** — Prevents runaway context bloat
- **Sensitive key detection** — Blocks credentials, API keys, tokens
- **Tag-based filtering** — Only hand off what's relevant
- **Agent identity validation** — Detects self-handoffs

## Future

- PostgreSQL backend for production
- Redis caching for low-latency reads
- WebSocket streaming for real-time memory sync
- Graph-based memory navigation
```

**Step 3: Commit**

```bash
git add .
git commit -m "docs: add README and architecture documentation"
```

---

### Task 8: CLI entry point

**Objective:** Add a CLI command to start the server

**Files:**
- Create: `src/memory_bridge/cli.py`
- Modify: `pyproject.toml`

**Step 1: Write src/memory_bridge/cli.py**

```python
"""CLI entry point for Memory Bridge."""
import uvicorn


def main():
    """Start the Memory Bridge server."""
    uvicorn.run(
        "memory_bridge.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
```

**Step 2: Update pyproject.toml** — add `[project.scripts]`:

```toml
[project.scripts]
memory-bridge = "memory_bridge.cli:main"
```

**Step 3: Install and verify**

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
pip install -e ".[dev]"
memory-bridge --help  # or just `memory-bridge` to start
```

Actually the CLI uses argparse or just calls uvicorn directly. Let's verify it imports.

Run:
```bash
cd ~/MemoryBridge && source .venv/bin/activate
python -c "from memory_bridge.cli import main; print('CLI OK')"
```

**Step 4: Commit**

```bash
git add .
git commit -m "feat: add CLI entry point"
```

---

## Verification

After all tasks, run the full test suite:

```bash
cd ~/MemoryBridge && source .venv/bin/activate
pytest tests/ -v
```

Expected: All tests passing

Start the server:

```bash
memory-bridge
# or
uvicorn memory_bridge.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"memory-bridge"}
```
