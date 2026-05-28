"""Memory Bridge MCP Server.

Model Context Protocol (MCP) server that exposes Memory Bridge as a set of
tools that Claude, Cursor, AutoGen, and other MCP-compatible agents can use
with a single line of configuration.

Usage:
    memory-bridge-mcp [--api-url URL] [--api-key KEY]

Environment:
    MEMORY_BRIDGE_API_URL   Base URL of Memory Bridge HTTP API (default: http://localhost:8000)
    MEMORY_BRIDGE_API_KEY   API key for authentication (default: from env MEMORY_BRIDGE_API_KEY)
"""
