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
    """Create a Stripe checkout session for upgrading."""
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
