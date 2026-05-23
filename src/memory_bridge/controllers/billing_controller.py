"""Stripe webhook and billing endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Request

from ..services.billing_service import BillingService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events.

    Processes subscription lifecycle events:
    - checkout.session.completed
    - invoice.paid
    - customer.subscription.updated
    - customer.subscription.deleted
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    service = BillingService()
    result = await service.handle_webhook(payload, signature)

    logger.info("Stripe webhook processed: %s", result)
    return {"received": True}


@router.get("/subscription/{org_id}")
async def get_subscription(org_id: str):
    """Get current subscription details for an organization."""
    service = BillingService()
    sub = await service.get_subscription(org_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.post("/checkout/{org_id}")
async def create_checkout(org_id: str, tier: str = "pro"):
    """Create a Stripe checkout session for upgrading."""
    service = BillingService()
    url = await service.create_checkout_session(
        organization_id=org_id,
        tier=tier,
    )
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Could not create checkout session. Is Stripe configured?",
        )
    return {"checkout_url": url}
