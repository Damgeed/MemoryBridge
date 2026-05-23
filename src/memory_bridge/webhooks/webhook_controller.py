"""Webhook subscription management endpoints.

Provides CRUD operations for webhook subscriptions so that users
can register, list, view, and delete webhook endpoints that receive
forwarded Memory Bridge events.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, HttpUrl

from ..dependencies import get_storage
from .webhook_service import WebhookService, WebhookSubscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")

# ── In-memory registry (will be shared across requests) ────────────────────
# In production, this would be persisted in the database.
_service: Optional[WebhookService] = None


def get_webhook_service() -> WebhookService:
    """Dependency: return the shared WebhookService instance."""
    global _service
    if _service is None:
        _service = WebhookService()
    return _service


# ── Pydantic Schemas ──────────────────────────────────────────────────────


class WebhookCreate(BaseModel):
    """Request body to create a webhook subscription."""

    url: HttpUrl
    """Target URL that will receive forwarded events."""

    event_types: list[str] = Field(
        ..., min_length=1, description="Event types to subscribe to"
    )
    """List of event type strings (e.g. ['memory.created', 'memory.updated'])."""

    secret: str = Field(..., min_length=8, max_length=256)
    """HMAC signing secret shared with the subscriber."""

    project: Optional[str] = None
    """Project scope; None means all projects."""


class WebhookUpdate(BaseModel):
    """Request body to update a webhook subscription."""

    url: Optional[HttpUrl] = None
    event_types: Optional[list[str]] = None
    secret: Optional[str] = Field(None, min_length=8, max_length=256)
    is_active: Optional[bool] = None


class WebhookResponse(BaseModel):
    """Webhook subscription returned to the client."""

    id: str
    url: str
    event_types: list[str]
    project: Optional[str] = None
    is_active: bool
    created_at: str


class WebhookDeliveryResponse(BaseModel):
    """Delivery status for a subscription."""

    subscription_id: str
    event_type: str
    url: str
    status: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    attempts: int
    timestamp: str


# ── Helper ─────────────────────────────────────────────────────────────────


def _to_response(sub: WebhookSubscription) -> WebhookResponse:
    return WebhookResponse(
        id=sub.id,
        url=sub.url,
        event_types=sub.event_types,
        project=sub.project,
        is_active=sub.is_active,
        created_at=sub.created_at.isoformat(),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    payload: WebhookCreate,
    request: Request,
    service: WebhookService = Depends(get_webhook_service),
):
    """Register a new webhook subscription.

    The webhook will receive forwarded events matching its ``event_types``.
    Payloads are HMAC-SHA256 signed using the provided ``secret``.
    """
    project = getattr(request.state, "project_id", None) or payload.project

    sub = WebhookSubscription(
        id=str(uuid.uuid4()),
        url=str(payload.url),
        event_types=payload.event_types,
        secret=payload.secret,
        project=project or payload.project,
    )
    service.register_subscription(sub)
    logger.info("Created webhook subscription %s → %s", sub.id, sub.url)
    return _to_response(sub)


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    request: Request,
    service: WebhookService = Depends(get_webhook_service),
):
    """List all webhook subscriptions, scoped to the current project."""
    project = getattr(request.state, "project_id", None)
    subs = service.list_subscriptions(project=project)
    return [_to_response(s) for s in subs]


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(
    webhook_id: str,
    service: WebhookService = Depends(get_webhook_service),
):
    """Get details of a specific webhook subscription."""
    sub = service.get_subscription(webhook_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return _to_response(sub)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: str,
    payload: WebhookUpdate,
    service: WebhookService = Depends(get_webhook_service),
):
    """Update an existing webhook subscription."""
    sub = service.get_subscription(webhook_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if payload.url is not None:
        sub.url = str(payload.url)
    if payload.event_types is not None:
        sub.event_types = payload.event_types
    if payload.secret is not None:
        sub.secret = payload.secret
    if payload.is_active is not None:
        sub.is_active = payload.is_active

    service.register_subscription(sub)
    logger.info("Updated webhook subscription %s", webhook_id)
    return _to_response(sub)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    service: WebhookService = Depends(get_webhook_service),
):
    """Delete a webhook subscription."""
    removed = service.remove_subscription(webhook_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Webhook not found")
    logger.info("Deleted webhook subscription %s", webhook_id)
    return None


@router.get("/{webhook_id}/deliveries", response_model=Optional[WebhookDeliveryResponse])
async def get_webhook_deliveries(
    webhook_id: str,
    service: WebhookService = Depends(get_webhook_service),
):
    """Get the last delivery status for a webhook subscription."""
    sub = service.get_subscription(webhook_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    delivery = service.get_last_delivery(webhook_id)
    if delivery is None:
        return None

    return WebhookDeliveryResponse(
        subscription_id=delivery.subscription_id,
        event_type=delivery.event_type,
        url=delivery.url,
        status=delivery.status,
        status_code=delivery.status_code,
        error=delivery.error,
        attempts=delivery.attempts,
        timestamp=delivery.timestamp.isoformat(),
    )
