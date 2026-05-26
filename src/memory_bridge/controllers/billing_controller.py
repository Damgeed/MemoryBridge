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
    """Create a Stripe checkout session, resolving org_id from auth.

    Requires authentication (JWT or API key). Returns the user's
    organization_id in the response so the frontend can track it.
    """
    # Require authentication — users must be logged in to subscribe
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(
            status_code=401,
            detail="You must be signed in to subscribe. Sign up or log in first.",
        )

    org_id = auth.get("project_id") or auth.get("key_id", "")
    if not org_id:
        raise HTTPException(status_code=401, detail="Could not resolve your account. Please sign in again.")

    # Check if user already has this tier — no point buying the same thing
    try:
        from ..dependencies import get_storage
        storage = await get_storage()
        existing_sub = await storage.get_subscription_by_org(org_id)
        if existing_sub and existing_sub.tier == tier and existing_sub.status == "active":
            raise HTTPException(
                status_code=409,
                detail=f"You're already on the {tier.title()} plan. Visit your dashboard to manage your subscription.",
            )
    except HTTPException:
        raise
    except Exception:
        pass

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
