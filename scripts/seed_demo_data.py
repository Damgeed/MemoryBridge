"""Seed demo data into Memory Bridge under a dedicated 'demo' project.

Run:  python scripts/seed_demo_data.py

Demo data is isolated under project='demo' so it never mixes with
real user data. Only visible when using the env API key or when
explicitly querying the demo project.
"""

import asyncio
import os
import sys

# Ensure the src directory is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_bridge.dependencies import get_storage
from memory_bridge.models import MemoryEntry


DEMO_PROJECT = "demo"

DEMO_MEMORIES = [
    # ── Agent: planner ──────────────────────────────────
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-planner",
        key="sprint_goals",
        value={"goal": "Q2 release", "deadline": "2026-06-30"},
        tags=["task", "planning"],
        project=DEMO_PROJECT,
    ),
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-planner",
        key="meeting_notes",
        value={"topic": "Sprint retro", "notes": "Ship auth module first"},
        tags=["meeting"],
        project=DEMO_PROJECT,
    ),
    # ── Agent: coder ────────────────────────────────────
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-coder",
        key="implemented_features",
        value={"feature": "JWT refresh", "status": "merged"},
        tags=["code", "done"],
        project=DEMO_PROJECT,
    ),
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-coder",
        key="open_prs",
        value=[{"url": "https://github.com/org/repo/pull/42", "title": "Add rate limiting"}],
        tags=["code", "review"],
        project=DEMO_PROJECT,
    ),
    # ── Agent: tester (shares sprint-23 session) ────────
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-tester",
        key="test_report",
        value={"passed": 42, "failed": 0, "skipped": 3},
        tags=["qa", "done"],
        project=DEMO_PROJECT,
    ),
    MemoryEntry(
        session_id="sprint-23",
        agent_id="agent-tester",
        key="regression_suite",
        value={"scenarios": ["auth", "billing", "graph"]},
        tags=["qa", "automation"],
        project=DEMO_PROJECT,
    ),
    # ── Agent: designer (separate session) ──────────────
    MemoryEntry(
        session_id="design-v2",
        agent_id="agent-designer",
        key="design_system",
        value={"colors": {"primary": "#6366f1", "accent": "#a78bfa"}},
        tags=["design", "system"],
        project=DEMO_PROJECT,
    ),
    MemoryEntry(
        session_id="design-v2",
        agent_id="agent-designer",
        key="user_feedback",
        value={"rating": 4.5, "comment": "Love the new dashboard"},
        tags=["research"],
        project=DEMO_PROJECT,
    ),
    # ── Agent: writer (own session) ─────────────────────
    MemoryEntry(
        session_id="docs-update",
        agent_id="agent-writer",
        key="docs_pages",
        value={"published": ["quickstart", "api-reference", "guides"]},
        tags=["docs", "done"],
        project=DEMO_PROJECT,
    ),
    MemoryEntry(
        session_id="docs-update",
        agent_id="agent-writer",
        key="changelog_draft",
        value={"version": "0.3.0", "changes": ["Memory graph", "Auth0 SSO"]},
        tags=["docs", "draft"],
        project=DEMO_PROJECT,
    ),
]


async def seed():
    storage = await get_storage()
    await storage.initialize()

    existing = await storage.query_memories(limit=1, project=DEMO_PROJECT)
    if existing:
        print(f"Demo data already exists ({len(existing)} memories found). Skipping seed.")
        return

    count = 0
    for mem in DEMO_MEMORIES:
        await storage.store_memory(mem)
        count += 1

    print(f"Seeded {count} demo memories under project='{DEMO_PROJECT}'.")
    print()
    print("Agents: planner, coder, tester, designer, writer")
    print("Sessions: sprint-23 (shared), design-v2, docs-update")
    print()
    print("Visit /graph to see it — use the env API key or project=demo param.")


if __name__ == "__main__":
    asyncio.run(seed())
