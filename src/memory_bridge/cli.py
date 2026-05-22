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
