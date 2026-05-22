"""CLI entry point for Memory Bridge."""
import uvicorn


def main():
    """Start the Memory Bridge server."""
    import os
    reload_enabled = os.environ.get("MEMORY_BRIDGE_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "memory_bridge.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", os.environ.get("MEMORY_BRIDGE_PORT", "8000"))),
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
