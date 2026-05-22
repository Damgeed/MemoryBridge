# Memory Bridge

Cross-session memory persistence for multi-agent AI teams.

Memory Bridge is a middleware layer that lets AI agents share context across sessions. It provides:

- **Session Persistence** — Store and retrieve agent context across sessions
- **Memory Tagging** — Organize memories with tags for selective retrieval
- **Agent Handoff** — Pass context between agents with guardrails
- **Pluggable Storage** — SQLite out of the box, upgrade to PostgreSQL/Redis later

## Quick Start

### From Source

```bash
git clone https://github.com/Damgeed/MemoryBridge.git
cd MemoryBridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start the server
memory-bridge
```

### With Docker

```bash
docker build -t memory-bridge .
docker run -p 8000:8000 -v $(pwd)/data:/app memory-bridge
```

> The `-v` flag mounts a local `data/` directory so the SQLite database persists across container restarts.```

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
