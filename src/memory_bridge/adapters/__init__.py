"""Framework adapters for Memory Bridge.

Each adapter can be used as a standalone package or copied into your project.

Usage:
    from memory_bridge.adapters import MemoryBridgeSaver, MemoryBridgeAgent, MemoryBridgeTool
"""

from memory_bridge.adapters.langgraph_adapter import MemoryBridgeSaver
from memory_bridge.adapters.autogen_adapter import MemoryBridgeAgent
from memory_bridge.adapters.crewai_adapter import MemoryBridgeTool

__all__ = [
    "MemoryBridgeSaver",
    "MemoryBridgeAgent",
    "MemoryBridgeTool",
]
