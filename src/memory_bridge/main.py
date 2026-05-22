from fastapi import FastAPI

app = FastAPI(
    title="Memory Bridge",
    version="0.1.0",
    description="Cross-session memory persistence for multi-agent teams",
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory-bridge"}
