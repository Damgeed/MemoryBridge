"""Health and metrics endpoints for Memory Bridge.

Exposes service health status and Prometheus metrics.
Both endpoints bypass the service layer and talk directly
to the repository for operational data.

Also provides Kubernetes-style readiness and liveness probes.
"""

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..dependencies import get_storage
from ..metrics import (
    memory_gauge,
    session_gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health(storage: MemoryRepository = Depends(get_storage)):
    """Return service health status with operational metrics."""
    metrics = await storage.get_all_metrics()

    start_time_str = metrics.get("start_time")
    if start_time_str:
        start_dt = datetime.fromisoformat(start_time_str)
        uptime = int((datetime.now(timezone.utc) - start_dt).total_seconds())
    else:
        uptime = 0

    req_count = metrics.get("request_count", 0)
    total_lat = metrics.get("total_latency_ms", 0.0)
    avg_latency = (total_lat / req_count) if req_count > 0 else 0.0

    last_cleanup = metrics.get("last_cleanup_at")
    if last_cleanup:
        last_cleanup_dt = datetime.fromisoformat(last_cleanup)
        last_cleanup_seconds_ago = int(
            (datetime.now(timezone.utc) - last_cleanup_dt).total_seconds()
        )
    else:
        last_cleanup_seconds_ago = None

    sessions_total = await storage.count_sessions()
    memories_total = await storage.count_memories()

    return {
        "status": "ok",
        "service": "memory-bridge",
        "version": "0.3.0",
        "uptime_seconds": uptime,
        "sessions_total": sessions_total,
        "memories_total": memories_total,
        "avg_latency_ms": round(avg_latency, 3),
        "requests_served": req_count,
        "last_cleanup_seconds_ago": last_cleanup_seconds_ago,
    }


@router.get("/health/ready")
async def readiness(storage: MemoryRepository = Depends(get_storage)):
    """Kubernetes readiness probe.

    Returns 200 when the service is ready to accept traffic,
    meaning the database connection is functional.
    Returns 503 if the database is unreachable.
    """
    try:
        # Verify database connectivity by checking metrics
        await storage.get_all_metrics()
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        logger.warning("Readiness check failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "database": "disconnected"},
        )


@router.get("/health/live")
async def liveness():
    """Kubernetes liveness probe.

    Always returns 200 as long as the process is alive.
    The process being alive means the service is running.
    """
    return {"status": "alive"}


@router.get("/metrics")
async def metrics(
    storage: MemoryRepository = Depends(get_storage),
    request: Request = None,
):
    """Expose Prometheus metrics.

    Requires auth by default. Set MEMORY_BRIDGE_PUBLIC_METRICS=true
    to allow unauthenticated access.
    """
    public_metrics = (
        os.environ.get("MEMORY_BRIDGE_PUBLIC_METRICS", "").lower() == "true"
    )
    if not public_metrics:
        auth = getattr(request.state, "auth", None) if request else None
        if not auth:
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Set MEMORY_BRIDGE_PUBLIC_METRICS=true for public access.",
            )

    memory_gauge.set(await storage.count_memories())
    session_gauge.set(await storage.count_sessions())

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
