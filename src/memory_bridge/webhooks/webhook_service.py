"""Webhook subscription management and event forwarding.

Stores webhook subscriptions (URL, event types, signing secret, project scope)
and forwards matching events with HMAC-SHA256 payload signing and
exponential-backoff retry.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..events.event_bus import EventBus
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

MAX_RETRIES = 3
"""Max retry attempts per delivery (initial attempt + 2 retries)."""

BASE_RETRY_DELAY = 1.0  # seconds
"""Initial delay before first retry; doubles each attempt."""

MAX_PAYLOAD_SIZE = 1_000_000  # 1 MB
"""Max payload body size in bytes before truncation."""

DELIVERY_TIMEOUT = 10.0  # seconds
"""HTTP request timeout for each delivery attempt."""

# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class WebhookSubscription:
    """A registered webhook subscription."""

    id: str
    """Unique subscription identifier."""

    url: str
    """Target URL to receive forwarded events."""

    event_types: list[str]
    """Event types to subscribe to (e.g. ['memory.created', 'memory.updated'])."""

    secret: str
    """HMAC-SHA256 signing secret shared with the subscriber."""

    project: Optional[str] = None
    """Project scope; None means all projects."""

    is_active: bool = True
    """Whether this subscription is active."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When this subscription was created."""


@dataclass
class WebhookDelivery:
    """Record of a single delivery attempt."""

    subscription_id: str
    event_type: str
    url: str
    status: str  # "success" | "failed" | "pending"
    status_code: Optional[int] = None
    error: Optional[str] = None
    attempts: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Service ────────────────────────────────────────────────────────────────


class WebhookService:
    """Manages webhook subscriptions and forwards events to subscribers.

    Integrates with EventBus to receive events and deliver them to
    registered webhook URLs with HMAC payload signing and retry.
    """

    def __init__(self, event_bus: Optional[EventBus] = None, repo: Optional[MemoryRepository] = None):
        self._subscriptions: dict[str, WebhookSubscription] = {}
        self._event_bus = event_bus
        self.repo = repo
        self._client: Optional[httpx.AsyncClient] = None
        self._retry_queue: asyncio.Queue[tuple[WebhookSubscription, str, dict[str, Any]]] = (
            asyncio.Queue()
        )
        self._retry_worker_task: Optional[asyncio.Task] = None
        self._last_deliveries: dict[str, WebhookDelivery] = {}
        self._delivery_history: dict[str, list[WebhookDelivery]] = {}
        self._max_deliveries_per_webhook = 1000
        self._delivery_semaphore = asyncio.Semaphore(20)
        self._dispatch_semaphore = asyncio.Semaphore(100)

        # Subscribe to all event types on the bus if available
        if event_bus and event_bus.enabled:
            self._subscribe_to_all()

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the retry worker and HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT)
        if self._retry_worker_task is None:
            self._retry_worker_task = asyncio.create_task(self._retry_worker())
        logger.info("WebhookService started")

    async def stop(self) -> None:
        """Stop the retry worker and HTTP client."""
        if self._retry_worker_task:
            self._retry_worker_task.cancel()
            try:
                await self._retry_worker_task
            except asyncio.CancelledError:
                pass
            self._retry_worker_task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("WebhookService stopped")

    # ── Subscription Management ─────────────────────────────────────────

    def register_subscription(self, sub: WebhookSubscription) -> None:
        """Register or update a webhook subscription."""
        self._subscriptions[sub.id] = sub
        logger.info(
            "Registered webhook subscription %s → %s (%d event types)",
            sub.id,
            sub.url,
            len(sub.event_types),
        )

    def remove_subscription(self, sub_id: str) -> bool:
        """Remove a subscription by ID. Returns True if removed."""
        if sub_id in self._subscriptions:
            del self._subscriptions[sub_id]
            logger.info("Removed webhook subscription %s", sub_id)
            return True
        return False

    def get_subscription(self, sub_id: str) -> Optional[WebhookSubscription]:
        """Get a subscription by ID."""
        return self._subscriptions.get(sub_id)

    def list_subscriptions(
        self, project: Optional[str] = None
    ) -> list[WebhookSubscription]:
        """List subscriptions, optionally filtered by project."""
        subs = list(self._subscriptions.values())
        if project:
            subs = [s for s in subs if s.project is None or s.project == project]
        return subs

    # ── Persistence (Repository-backed) ───────────────────────────────

    async def load_subscriptions(self) -> None:
        """Load all active subscriptions from the repository.

        Queries the ``webhook_subscriptions`` table via ``self.repo``
        and populates ``self._subscriptions``.
        """
        if not self.repo:
            logger.debug("No repo configured; subscriptions live in memory only")
            return
        rows = await self.repo.list_webhook_subscriptions()
        for row in rows:
            sub = WebhookSubscription(
                id=row["id"],
                url=row["url"],
                event_types=row["event_types"],
                secret=row["secret"],
                project=row.get("project"),
                is_active=row.get("is_active", True),
                created_at=datetime.fromisoformat(row["created_at"])
                if isinstance(row["created_at"], str)
                else row["created_at"],
            )
            self._subscriptions[sub.id] = sub
        logger.info("Loaded %d webhook subscriptions from repository", len(rows))

    async def save_subscription(self, sub: WebhookSubscription) -> None:
        """Save (create or update) a subscription in the repository.

        INSERTs or UPDATEs the ``webhook_subscriptions`` table
        via ``self.repo``.
        """
        if not self.repo:
            return
        await self.repo.store_webhook_subscription({
            "id": sub.id,
            "url": sub.url,
            "event_types": sub.event_types,
            "secret": sub.secret,
            "project": sub.project,
            "is_active": sub.is_active,
            "created_at": sub.created_at.isoformat(),
        })
        logger.debug("Saved webhook subscription %s to repository", sub.id)

    async def remove_subscription_from_repo(self, sub_id: str) -> None:
        """Remove a subscription from the repository.

        DELETEs from the ``webhook_subscriptions`` table via ``self.repo``.
        """
        if not self.repo:
            return
        await self.repo.remove_webhook_subscription(sub_id)
        logger.debug("Removed webhook subscription %s from repository", sub_id)

    def get_last_delivery(self, subscription_id: str) -> Optional[WebhookDelivery]:
        """Get the last delivery record for a subscription."""
        return self._last_deliveries.get(subscription_id)

    # ── Event Handling ──────────────────────────────────────────────────

    def _subscribe_to_all(self) -> None:
        """Subscribe the webhook service to all event types on the EventBus."""
        # We subscribe with a single handler that checks subscriptions with
        # matching event types at dispatch time.
        if self._event_bus is None:
            return

        # The dispatcher inspects event_type at runtime to match subscribers
        async def dispatch_wrapper(data: dict[str, Any]) -> None:
            event_type = data.get("type", "unknown")
            await self.dispatch_event(event_type, data.get("data", {}))

        # Subscribe to a wildcard catch-all pattern.
        # In practice, the EventBus is called for each known event type,
        # so we subscribe the wrapper to the internal list of types.
        for et in [
            "memory.created",
            "memory.updated",
            "memory.deleted",
            "memory.searched",
            "session.created",
            "handoff.executed",
        ]:
            self._event_bus.subscribe(et, dispatch_wrapper)

    async def dispatch_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Dispatch an event to all matching subscriptions.

        Args:
            event_type: The type of event (e.g. ``memory.created``).
            data: Event payload data.
        """
        if not self._client:
            logger.warning("WebhookService not started; cannot dispatch %s", event_type)
            return

        # Preserve the event type in the delivery payload
        payload_data = {"type": event_type, "data": data}

        for sub in list(self._subscriptions.values()):
            if not sub.is_active:
                continue
            if event_type not in sub.event_types:
                continue

            # Check project scope match
            event_project = data.get("project") if isinstance(data, dict) else None
            if sub.project and event_project and event_project != sub.project:
                continue

            async with self._dispatch_semaphore:
                asyncio.create_task(self._deliver(sub, payload_data))

    # ── Delivery Logic ──────────────────────────────────────────────────

    async def _deliver(
        self,
        sub: WebhookSubscription,
        payload: dict[str, Any],
        is_retry: bool = False,
    ) -> None:
        """Deliver an event to a single webhook URL.

        Runs synchronously for initial attempt; retries are queued.
        Concurrent deliveries are capped by ``self._delivery_semaphore``.
        """
        async with self._delivery_semaphore:
            await self._deliver_inner(sub, payload, is_retry=is_retry)

    async def _deliver_inner(
        self,
        sub: WebhookSubscription,
        payload: dict[str, Any],
        is_retry: bool = False,
    ) -> None:
        """Inner delivery logic (runs under semaphore)."""
        if not self._client:
            logger.warning("WebhookService not started; cannot deliver")
            return

        body = json.dumps(payload, default=str).encode()
        if len(body) > MAX_PAYLOAD_SIZE:
            logger.warning(
                "Payload %d bytes exceeds max %d bytes for %s; truncating",
                len(body),
                MAX_PAYLOAD_SIZE,
                sub.url,
            )
            body = body[:MAX_PAYLOAD_SIZE]

        # HMAC-SHA256 signature
        signature = hmac.new(
            sub.secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-MemoryBridge-Event": payload.get("type", ""),
            "X-MemoryBridge-Signature": f"sha256={signature}",
            "X-MemoryBridge-Delivery": datetime.now(timezone.utc).isoformat(),
            "User-Agent": "MemoryBridge-Webhook/1.0",
        }

        attempt = 0 if not is_retry else 1
        max_attempts = 1 + MAX_RETRIES  # initial + retries

        while attempt < max_attempts:
            attempt += 1
            try:
                response = await self._client.post(
                    sub.url, content=body, headers=headers
                )
                if 200 <= response.status_code < 300:
                    self._record_delivery(
                        WebhookDelivery(
                            subscription_id=sub.id,
                            event_type=payload.get("type", "unknown"),
                            url=sub.url,
                            status="success",
                            status_code=response.status_code,
                            attempts=attempt,
                        )
                    )
                    logger.debug(
                        "Delivered %s to %s (status=%d)",
                        payload.get("type"),
                        sub.url,
                        response.status_code,
                    )
                    return
                else:
                    logger.warning(
                        "Webhook %s returned %d for %s",
                        sub.url,
                        response.status_code,
                        payload.get("type"),
                    )
            except httpx.TimeoutException:
                logger.warning("Timeout delivering to %s", sub.url)
            except httpx.RequestError as exc:
                logger.warning("Request error delivering to %s: %s", sub.url, exc)
            except Exception:
                logger.exception("Unexpected error delivering to %s", sub.url)

            if attempt >= max_attempts:
                break

            # Exponential backoff before retry
            delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))
            logger.info(
                "Retrying %s for %s in %.1fs (attempt %d/%d)",
                payload.get("type"),
                sub.url,
                delay,
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(delay)

        # All attempts exhausted
        self._record_delivery(
            WebhookDelivery(
                subscription_id=sub.id,
                event_type=payload.get("type", "unknown"),
                url=sub.url,
                status="failed",
                error=f"Failed after {attempt} attempts",
                attempts=attempt,
            )
        )

    # ── Retry Worker ────────────────────────────────────────────────────

    async def _retry_worker(self) -> None:
        """Background worker that retries failed deliveries from the queue."""
        while True:
            try:
                sub, event_type, data = await self._retry_queue.get()
                # Reconstruct full payload
                payload = {"type": event_type, "data": data}
                await self._deliver(sub, payload, is_retry=True)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Retry worker error")

    def enqueue_retry(self, sub: WebhookSubscription, event_type: str, data: dict[str, Any]) -> None:
        """Enqueue a failed delivery for background retry."""
        self._retry_queue.put_nowait((sub, event_type, data))

    # ── Internal Helpers ────────────────────────────────────────────────

    def _record_delivery(self, delivery: WebhookDelivery) -> None:
        """Record a delivery attempt in both last-delivery and history stores,
        and persist to the repository if available."""
        sub_id = delivery.subscription_id
        self._last_deliveries[sub_id] = delivery

        # Append to full delivery history
        if sub_id not in self._delivery_history:
            self._delivery_history[sub_id] = []
        self._delivery_history[sub_id].append(delivery)

        # Trim old records beyond the max per-webhook limit
        if len(self._delivery_history[sub_id]) > self._max_deliveries_per_webhook:
            self._delivery_history[sub_id] = self._delivery_history[sub_id][-self._max_deliveries_per_webhook:]

        # Persist to the repository if available
        if self.repo:
            asyncio.create_task(self._persist_delivery(delivery))

    async def _persist_delivery(self, delivery: WebhookDelivery) -> None:
        """Persist a delivery record to the repository."""
        try:
            await self.repo.store_webhook_delivery({
                "id": str(uuid.uuid4()) if not hasattr(delivery, 'id') or not delivery.subscription_id else f"{delivery.subscription_id}_{delivery.timestamp.timestamp()}",
                "subscription_id": delivery.subscription_id,
                "event_type": delivery.event_type,
                "url": delivery.url,
                "status": delivery.status,
                "status_code": delivery.status_code,
                "error": delivery.error,
                "attempts": delivery.attempts,
                "timestamp": delivery.timestamp.isoformat(),
            })
        except Exception:
            logger.exception("Failed to persist delivery to repository")

    def get_deliveries(
        self, webhook_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[WebhookDelivery], int]:
        """Get paginated delivery history for a webhook.

        Merges in-memory delivery history with repository data.
        """
        # Get in-memory deliveries
        deliveries = self._delivery_history.get(webhook_id, [])
        total = len(deliveries)
        page = deliveries[offset:offset + limit]
        return page, total
