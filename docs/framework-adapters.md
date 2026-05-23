# Framework Adapters

Memory Bridge provides drop-in adapters for popular agent frameworks. Each adapter is a standalone
module that can be copied into your project or imported directly from `memory_bridge.adapters`.

## LangGraph Adapter

The `MemoryBridgeSaver` implements a LangGraph-compatible checkpoint saver, allowing your
LangGraph agents to persist execution state across sessions.

### Installation

```bash
pip install memory-bridge httpx
```

### Usage

```python
from langgraph.graph import StateGraph
from memory_bridge.adapters.langgraph_adapter import MemoryBridgeSaver

# Define your graph state
class AgentState(TypedDict):
    messages: list

# Create the checkpointer
checkpointer = MemoryBridgeSaver(
    api_key="mb_your_api_key",       # Optional: set via env or omit for local dev
    base_url="http://localhost:8000",  # Memory Bridge server URL
    project="my-project",              # Optional: project/namespace
    session_id="my-session",           # Optional: defaults to "langgraph-default"
)

# Compile your graph with the checkpointer
graph = StateGraph(AgentState)
# ... add nodes and edges ...
app = graph.compile(checkpointer=checkpointer)

# Run with thread persistence
result = app.invoke(
    {"messages": [{"role": "user", "content": "Hello!"}]},
    config={"configurable": {"thread_id": "user-123"}},
)

# On next run, the agent remembers its state
result2 = app.invoke(
    {"messages": [{"role": "user", "content": "What did I say before?"}]},
    config={"configurable": {"thread_id": "user-123"}},
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `put(config, checkpoint, metadata)` | Store a checkpoint for a thread |
| `get(config)` | Retrieve the latest checkpoint for a thread |
| `list(config)` | List all checkpoints for a thread |

---

## AutoGen Adapter

The `MemoryBridgeAgent` wraps AutoGen agents with persistent memory, storing conversation
history and agent state in Memory Bridge.

### Installation

```bash
pip install memory-bridge httpx
```

### Usage

```python
from memory_bridge.adapters.autogen_adapter import MemoryBridgeAgent

# Create a Memory Bridge-powered agent
agent = MemoryBridgeAgent(
    name="assistant",
    api_key="mb_your_api_key",
    base_url="http://localhost:8000",
    session_id="my-autogen-session",  # Optional
    project="my-project",              # Optional
)

# Store conversation memories
await agent.remember("user_preference", {"language": "Python", "style": "concise"})

# Recall memories across sessions
prefs = await agent.recall("user_preference")
print(prefs)  # {"language": "Python", "style": "concise"}

# Search across all stored memories
results = await agent.search("Python preferences")
```

### Key Methods

| Method | Description |
|--------|-------------|
| `remember(key, value)` | Store a key-value memory |
| `recall(key)` | Retrieve a memory by key |
| `search(query)` | Search across all memories |

---

## CrewAI Adapter

The `MemoryBridgeTool` provides a CrewAI-compatible tool that gives agents persistent
memory capabilities across crew runs.

### Installation

```bash
pip install memory-bridge httpx
```

### Usage

```python
from crewai import Crew, Agent, Task
from memory_bridge.adapters.crewai_adapter import MemoryBridgeTool

# Create the memory tool
memory_tool = MemoryBridgeTool(
    api_key="mb_your_api_key",
    base_url="http://localhost:8000",
    session_id="my-crew-session",  # Optional
    project="my-project",           # Optional
)

# Create an agent with the tool
agent = Agent(
    role="Research Analyst",
    goal="Gather and remember information",
    backstory="I remember everything across sessions.",
    tools=[memory_tool],
)

# Use within tasks — agents can call:
#   memory_tool.store("key", "value")
#   memory_tool.recall("key")
#   memory_tool.search("query")

crew = Crew(
    agents=[agent],
    tasks=[Task(description="Research the topic", agent=agent)],
)
result = crew.kickoff()
```

### Key Methods

| Method | Description |
|--------|-------------|
| `store(key, value)` | Store a memory (returns status string) |
| `recall(key)` | Recall a memory by key (returns value string) |
| `search(query)` | Search memories (returns results string) |

---

## Configuration Options

All adapters share a common set of configuration parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `api_key` | `""` | Memory Bridge API key for auth (omit for local dev) |
| `base_url` | `http://localhost:8000` | Memory Bridge server URL |
| `session_id` | Framework-specific | Session identifier for memory isolation |
| `project` | `None` | Project/namespace for multi-project setups |

---

## Running Memory Bridge Locally

If you don't have a Memory Bridge server running:

```bash
# Start the server
memory-bridge serve

# Or with Docker
docker compose up
```

The server will be available at `http://localhost:8000`.
