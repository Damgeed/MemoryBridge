"""Tests for the webhook forwarding module."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, Response

from memory_bridge.webhooks import WebhookService, WebhookSubscription, WebhookDelivery
from memory_bridge.webhooks.webhook_controller import (
    WebhookCreate,
    WebhookUpdate,
    WebhookResponse,
    WebhookDeliveryResponse,
)
from memory_bridge.webhooks.webhook_service import (
    MAX_RETRIES,
    BASE_RETRY_DELAY,
    DELIVERY_TIMEOUT,
    MAX_PAYLOAD_SIZE,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def sample_subscription():
    return WebhookSubscription(
        id=str(uuid.uuid4()),
        url="https://example.com/webhooks/memory-bridge",
        event_types=["memory.created", "memory.updated"],
        secret="test-secret-key-12345",
        project="project-alpha",
    )


@pytest.fixture
def webhook_service():
    return WebhookService()


# ── WebhookSubscription Tests ──────────────────────────────────────────────


def test_subscription_defaults():
    """Test that a subscription gets sensible defaults."""
    sub = WebhookSubscription(
        id="test-1",
        url="https://example.com/hook",
        event_types=["memory.created"],
        secret="secret123",
    )
    assert sub.is_active is True
    assert sub.project is None
    assert isinstance(sub.created_at, datetime)


def test_subscription_project_scoped():
    """Test subscription with project scope."""
    sub = WebhookSubscription(
        id="test-2",
        url="https://example.com/hook",
        event_types=["memory.created"],
        secret="secret123",
        project="acme-corp",
    )
    assert sub.project == "acme-corp"


# ── WebhookService: Subscription Management ────────────────────────────────


def test_register_and_get_subscription(webhook_service, sample_subscription):
    """Test registering and retrieving a subscription."""
    webhook_service.register_subscription(sample_subscription)
    retrieved = webhook_service.get_subscription(sample_subscription.id)
    assert retrieved is not None
    assert retrieved.id == sample_subscription.id
    assert retrieved.url == sample_subscription.url


def test_remove_subscription(webhook_service, sample_subscription):
    """Test removing a subscription."""
    webhook_service.register_subscription(sample_subscription)
    assert webhook_service.remove_subscription(sample_subscription.id) is True
    assert webhook_service.get_subscription(sample_subscription.id) is None


def test_remove_nonexistent_subscription(webhook_service):
    """Test removing a subscription that doesn't exist."""
    assert webhook_service.remove_subscription("nonexistent") is False


def test_list_subscriptions(webhook_service):
    """Test listing subscriptions with optional project filter."""
    sub1 = WebhookSubscription(
        id="s1", url="https://a.com/hook", event_types=["memory.created"],
        secret="secret-1", project="proj-a",
    )
    sub2 = WebhookSubscription(
        id="s2", url="https://b.com/hook", event_types=["memory.updated"],
        secret="secret-2", project="proj-b",
    )
    sub3 = WebhookSubscription(
        id="s3", url="https://c.com/hook", event_types=["memory.created"],
        secret="secret-3", project=None,
    )

    for s in [sub1, sub2, sub3]:
        webhook_service.register_subscription(s)

    all_subs = webhook_service.list_subscriptions()
    assert len(all_subs) == 3

    proj_a_subs = webhook_service.list_subscriptions(project="proj-a")
    assert len(proj_a_subs) == 2  # sub1 (exact) + sub3 (global)

    proj_b_subs = webhook_service.list_subscriptions(project="proj-b")
    assert len(proj_b_subs) == 2  # sub2 (exact) + sub3 (global)


# ── WebhookService: Event Dispatch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_event_matches_subscription(webhook_service, sample_subscription):
    """Test that events are dispatched only to matching subscriptions."""
    webhook_service.register_subscription(sample_subscription)

    # Mock the HTTP client
    mock_client = AsyncMock(spec=AsyncClient)
    mock_response = Response(200, json={"ok": True})
    mock_client.post = AsyncMock(return_value=mock_response)
    webhook_service._client = mock_client

    await webhook_service.dispatch_event(
        "memory.created",
        {"session_id": "sess-1", "key": "test-key", "project": "project-alpha"},
    )

    # Should have attempted delivery — allow the async task to run
    await asyncio.sleep(0.05)
    assert mock_client.post.called
    call_args = mock_client.post.call_args
    # url is passed as positional arg; content/headers as keyword args
    assert call_args[0][0] == sample_subscription.url
    headers = call_args[1].get("headers", {})
    # Verify HMAC header present
    assert "X-MemoryBridge-Signature" in headers
    assert headers["X-MemoryBridge-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_dispatch_event_non_matching_type(webhook_service, sample_subscription):
    """Test that events with non-matching types are not dispatched."""
    webhook_service.register_subscription(sample_subscription)
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_client_post = AsyncMock()
    webhook_service._client.post = mock_client_post

    await webhook_service.dispatch_event(
        "session.created",
        {"session_id": "sess-1"},
    )

    mock_client_post.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_event_non_matching_project(webhook_service, sample_subscription):
    """Test that events from a different project are not dispatched."""
    webhook_service.register_subscription(sample_subscription)
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_client_post = AsyncMock()
    webhook_service._client.post = mock_client_post

    await webhook_service.dispatch_event(
        "memory.created",
        {"session_id": "sess-1", "key": "test", "project": "other-project"},
    )

    mock_client_post.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_event_inactive_subscription(webhook_service, sample_subscription):
    """Test that inactive subscriptions don't receive events."""
    sample_subscription.is_active = False
    webhook_service.register_subscription(sample_subscription)
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_client_post = AsyncMock()
    webhook_service._client.post = mock_client_post

    await webhook_service.dispatch_event(
        "memory.created",
        {"session_id": "sess-1", "project": "project-alpha"},
    )

    mock_client_post.assert_not_called()


# ── WebhookService: Delivery Logic ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_delivery(webhook_service, sample_subscription):
    """Test a successful webhook delivery."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_response = Response(200, json={"ok": True})
    webhook_service._client.post = AsyncMock(return_value=mock_response)

    payload = {"type": "memory.created", "data": {"key": "val"}}
    await webhook_service._deliver(sample_subscription, payload)

    delivery = webhook_service.get_last_delivery(sample_subscription.id)
    assert delivery is not None
    assert delivery.status == "success"
    assert delivery.status_code == 200
    assert delivery.attempts == 1


@pytest.mark.asyncio
async def test_failed_delivery_with_retry(webhook_service, sample_subscription):
    """Test that delivery is retried on failure."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    # Simulate consecutive failures
    mock_response = Response(500, json={"error": "server error"})
    webhook_service._client.post = AsyncMock(return_value=mock_response)

    payload = {"type": "memory.created", "data": {"key": "val"}}
    await webhook_service._deliver(sample_subscription, payload)

    delivery = webhook_service.get_last_delivery(sample_subscription.id)
    assert delivery is not None
    assert delivery.status == "failed"
    assert delivery.attempts == 1 + MAX_RETRIES  # initial + retries


@pytest.mark.asyncio
async def test_delivery_timeout_retry(webhook_service, sample_subscription):
    """Test that timeouts trigger retries."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    from httpx import TimeoutException
    webhook_service._client.post = AsyncMock(side_effect=TimeoutException("timeout"))

    payload = {"type": "memory.created", "data": {"key": "val"}}
    await webhook_service._deliver(sample_subscription, payload)

    delivery = webhook_service.get_last_delivery(sample_subscription.id)
    assert delivery is not None
    assert delivery.status == "failed"
    assert "timeout" in delivery.error.lower() or "failed" in delivery.status


@pytest.mark.asyncio
async def test_delivery_hmac_signing(webhook_service, sample_subscription):
    """Test that the payload is properly HMAC-SHA256 signed."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_response = Response(200, json={"ok": True})
    webhook_service._client.post = AsyncMock(return_value=mock_response)

    payload = {"type": "memory.created", "data": {"key": "val"}}
    await webhook_service._deliver(sample_subscription, payload)

    call_kwargs = webhook_service._client.post.call_args[1]
    headers = call_kwargs["headers"]

    assert "X-MemoryBridge-Signature" in headers
    assert headers["X-MemoryBridge-Signature"].startswith("sha256=")

    # Verify the signature is valid
    import hashlib
    import hmac
    expected_sig = hmac.new(
        sample_subscription.secret.encode("utf-8"),
        call_kwargs["content"],
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-MemoryBridge-Signature"] == f"sha256={expected_sig}"


@pytest.mark.asyncio
async def test_delivery_headers(webhook_service, sample_subscription):
    """Test that delivery includes all required headers."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_response = Response(200, json={"ok": True})
    webhook_service._client.post = AsyncMock(return_value=mock_response)

    payload = {"type": "memory.created", "data": {"key": "val"}}
    await webhook_service._deliver(sample_subscription, payload)

    call_kwargs = webhook_service._client.post.call_args[1]
    headers = call_kwargs["headers"]

    assert headers["Content-Type"] == "application/json"
    assert headers["X-MemoryBridge-Event"] == "memory.created"
    assert headers["User-Agent"] == "MemoryBridge-Webhook/1.0"
    assert "X-MemoryBridge-Delivery" in headers
    assert "X-MemoryBridge-Signature" in headers


@pytest.mark.asyncio
async def test_delivery_payload_truncation(webhook_service, sample_subscription):
    """Test that oversized payloads are truncated."""
    webhook_service._client = AsyncMock(spec=AsyncClient)
    mock_response = Response(200, json={"ok": True})
    webhook_service._client.post = AsyncMock(return_value=mock_response)

    # Create a payload that exceeds MAX_PAYLOAD_SIZE
    large_data = {"data": "x" * (MAX_PAYLOAD_SIZE + 100_000)}
    payload = {"type": "memory.created", "data": large_data}
    await webhook_service._deliver(sample_subscription, payload)

    call_kwargs = webhook_service._client.post.call_args[1]
    assert len(call_kwargs["content"]) <= MAX_PAYLOAD_SIZE


# ── WebhookDelivery Data ───────────────────────────────────────────────────


def test_webhook_delivery_defaults():
    """Test delivery has sensible defaults."""
    delivery = WebhookDelivery(
        subscription_id="sub-1",
        event_type="memory.created",
        url="https://example.com/hook",
        status="pending",
    )
    assert delivery.attempts == 0
    assert delivery.status_code is None
    assert delivery.error is None
    assert isinstance(delivery.timestamp, datetime)


# ── WebhookService Lifecycle ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_start_stop(webhook_service):
    """Test that start/stop properly manages the HTTP client."""
    assert webhook_service._client is None

    await webhook_service.start()
    assert webhook_service._client is not None
    assert webhook_service._retry_worker_task is not None

    await webhook_service.stop()
    assert webhook_service._client is None
    assert webhook_service._retry_worker_task is None


@pytest.mark.asyncio
async def test_delivery_without_start_warns(webhook_service, sample_subscription, caplog):
    """Test that dispatch warns if service not started."""
    webhook_service.register_subscription(sample_subscription)
    webhook_service._client = None  # Ensure no client

    await webhook_service.dispatch_event(
        "memory.created",
        {"session_id": "sess-1", "project": "project-alpha"},
    )

    assert "WebhookService not started" in caplog.text


# ── Pydantic Schema Validation ────────────────────────────────────────────


def test_webhook_create_valid():
    """Test valid WebhookCreate schema."""
    data = {
        "url": "https://example.com/hook",
        "event_types": ["memory.created"],
        "secret": "my-secret-key-12345",
    }
    create = WebhookCreate(**data)
    assert str(create.url) == "https://example.com/hook"
    assert create.event_types == ["memory.created"]


def test_webhook_create_invalid_url():
    """Test that invalid URLs are rejected."""
    with pytest.raises(Exception):
        WebhookCreate(
            url="not-a-url",
            event_types=["memory.created"],
            secret="my-secret-key",
        )


def test_webhook_create_empty_event_types():
    """Test that empty event_types list is rejected."""
    with pytest.raises(Exception):
        WebhookCreate(
            url="https://example.com/hook",
            event_types=[],
            secret="my-secret-key",
        )


def test_webhook_create_short_secret():
    """Test that short secret is rejected."""
    with pytest.raises(Exception):
        WebhookCreate(
            url="https://example.com/hook",
            event_types=["memory.created"],
            secret="short",
        )


def test_webhook_update_partial():
    """Test partial update via WebhookUpdate."""
    update = WebhookUpdate(is_active=False)
    assert update.is_active is False
    assert update.url is None
    assert update.event_types is None


def test_webhook_response_serialization(sample_subscription):
    """Test WebhookResponse serialization."""
    resp = WebhookResponse(
        id=sample_subscription.id,
        url=sample_subscription.url,
        event_types=sample_subscription.event_types,
        project=sample_subscription.project,
        is_active=sample_subscription.is_active,
        created_at=sample_subscription.created_at.isoformat(),
    )
    assert resp.id == sample_subscription.id
    assert resp.url == sample_subscription.url
    assert resp.project == "project-alpha"


# ── Edge Cases ─────────────────────────────────────────────────────────────


def test_global_subscription_matches_any_project(webhook_service):
    """Test that a subscription without project scope matches any project."""
    global_sub = WebhookSubscription(
        id="global",
        url="https://example.com/hook",
        event_types=["memory.created"],
        secret="secret",
        project=None,
    )
    webhook_service.register_subscription(global_sub)

    subs = webhook_service.list_subscriptions(project="any-project")
    assert len(subs) == 1


def test_duplicate_subscription_update(webhook_service, sample_subscription):
    """Test that registering with the same ID updates the subscription."""
    webhook_service.register_subscription(sample_subscription)
    updated = WebhookSubscription(
        id=sample_subscription.id,
        url="https://new-url.com/hook",
        event_types=["memory.deleted"],
        secret="new-secret",
    )
    webhook_service.register_subscription(updated)
    retrieved = webhook_service.get_subscription(sample_subscription.id)
    assert retrieved.url == "https://new-url.com/hook"


@pytest.mark.asyncio
async def test_enqueue_retry(webhook_service, sample_subscription):
    """Test that retry queue accepts items."""
    webhook_service.enqueue_retry(sample_subscription, "memory.created", {"key": "val"})
    assert webhook_service._retry_queue.qsize() == 1
