"""Stripe webhook and billing endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_storage
from ..repository import MemoryRepository
from ..services.billing_service import BillingService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


def _get_billing_service(repo: MemoryRepository = Depends(get_storage)) -> BillingService:
    """Build a BillingService with the current repository injected."""
    return BillingService(repo=repo)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    billing: BillingService = Depends(_get_billing_service),
):
    """Handle incoming Stripe webhook events.

    Processes subscription lifecycle events:
    - checkout.session.completed
    - invoice.paid
    - customer.subscription.updated
    - customer.subscription.deleted
    - customer.subscription.created
    """
    payload = await request.body()
    # Get the signature — Stripe sends case-insensitive header
    # Try lowercase first (FastAPI normalizes to lowercase), fallback to original case
    signature = request.headers.get("stripe-signature") or request.headers.get("Stripe-Signature", "")

    result = await billing.handle_webhook(payload, signature)

    logger.info("Stripe webhook processed: %s", result)
    return result


@router.get("/subscription/{org_id}")
async def get_subscription(
    org_id: str,
    billing: BillingService = Depends(_get_billing_service),
):
    """Get current subscription details for an organization."""
    sub = await billing.get_subscription(org_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.post("/checkout/{org_id}")
async def create_checkout(
    org_id: str,
    tier: str = "pro",
    billing: BillingService = Depends(_get_billing_service),
):
    """Create a Stripe checkout session for upgrading.

    The org_id comes from the URL path. For authenticated users, a
    separate /checkout endpoint (without org_id) resolves from auth.
    """
    url = await billing.create_checkout_session(
        organization_id=org_id,
        tier=tier,
    )
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Could not create checkout session. Is Stripe configured?",
        )
    return {"checkout_url": url}


@router.post("/checkout")
async def create_checkout_auth(
    request: Request,
    tier: str = "pro",
    billing: BillingService = Depends(_get_billing_service),
):
    """Create a Stripe checkout session, resolving org_id from auth or generating one.

    - Authenticated users: org_id = project_id from API key
    - Unauthenticated users (first-time checkout): generates a UUID org_id,
      stored in Stripe session metadata for the welcome endpoint to pick up
    """
    import uuid
    auth = getattr(request.state, "auth", None)
    if auth and auth.get("project_id"):
        org_id = auth["project_id"]
    elif auth and auth.get("key_id"):
        org_id = auth["key_id"]
    else:
        # First-time user — generate a unique org ID for the Stripe session
        org_id = str(uuid.uuid4())
    url = await billing.create_checkout_session(
        organization_id=org_id,
        tier=tier,
    )
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Could not create checkout session. Is Stripe configured?",
        )
    return {"checkout_url": url, "organization_id": org_id}


@router.post("/cancel/{org_id}")
async def cancel_subscription(
    org_id: str,
    billing: BillingService = Depends(_get_billing_service),
):
    """Cancel a subscription (at period end)."""
    success = await billing.cancel_subscription(org_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Could not cancel subscription. Is Stripe configured?",
        )
    return {"status": "canceled", "organization_id": org_id}
