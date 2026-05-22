"""Prometheus metrics for Memory Bridge."""
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ── Prometheus Metric Definitions ─────────────────────────────────────────

request_counter = Counter(
    "memory_bridge_http_requests_total",
    "Total HTTP requests served",
)

memory_gauge = Gauge(
    "memory_bridge_memories",
    "Current number of memories in storage",
)

session_gauge = Gauge(
    "memory_bridge_sessions",
    "Current number of sessions in storage",
)

uptime_gauge = Gauge(
    "memory_bridge_uptime_seconds",
    "Server uptime in seconds",
)

request_latency = Histogram(
    "memory_bridge_request_latency_seconds",
    "HTTP request latency in seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

__all__ = [
    "request_counter",
    "memory_gauge",
    "session_gauge",
    "uptime_gauge",
    "request_latency",
    "generate_latest",
    "CONTENT_TYPE_LATEST",
]
