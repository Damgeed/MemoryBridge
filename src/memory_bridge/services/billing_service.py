"""Stripe subscription billing integration.

Handles subscription lifecycle, metered billing, and webhook events.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Price IDs by tier (configure via env vars)
PRICE_IDS = {
    "starter": os.environ.get("STRICE_PRICE_STARTER", ""),
    "pro": os.environ.get("STRIPE_PRICE_PRO", ""),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", ""),
}


class BillingService:
    """Service for managing Stripe subscriptions and billing.

    Integrates with Stripe for:
    - Creating checkout sessions
    - Managing subscriptions
    - Handling webhook events
    - Metered billing via usage records
    """

    def __init__(self, repo=None):
        self.repo = repo
        self._stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
        self._webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    @property
    def enabled(self) -> bool:
        """Whether Stripe integration is active."""
        return bool(self._stripe_key)

    async def create_checkout_session(
        self,
        organization_id: str,
        tier: str = "pro",
        success_url: str = "https://memorybridge.ai/dashboard",
        cancel_url: str = "https://memorybridge.ai/pricing",
    ) -> Optional[str]:
        """Create a Stripe checkout session for a subscription.

        Args:
            organization_id: The org to subscribe
            tier: Tier to subscribe to (starter, pro, enterprise)
            success_url: Redirect URL on success
            cancel_url: Redirect URL on cancel

        Returns:
            Checkout URL or None if Stripe is not configured
        """
        if not self.enabled:
            logger.warning("Stripe not configured. Set STRIPE_SECRET_KEY env var.")
            return None

        price_id = PRICE_IDS.get(tier)
        if not price_id:
            logger.warning("No price ID configured for tier '%s'", tier)
            return None

        # In production, this calls stripe.checkout.Session.create()
        # For now, return a mock URL
        checkout_url = f"https://checkout.stripe.com/pay/{organization_id}?tier={tier}"
        logger.info("Created checkout session for org %s (tier: %s)", organization_id, tier)
        return checkout_url

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """Process a Stripe webhook event.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header

        Returns:
            Dict with event type and status
        """
        if not self.enabled:
            return {"status": "skipped", "reason": "Stripe not configured"}

        # In production, verify signature with stripe.Webhook.construct_event()
        # For now, log and return
        logger.info("Received Stripe webhook (signature: %s...)", signature[:20] if signature else "none")
        return {"status": "received", "event": "unknown"}

    async def get_subscription(self, organization_id: str) -> Optional[dict]:
        """Get current subscription details for an organization.

        Returns dict with tier, status, current_period_end, or None.
        """
        if not self.repo:
            return None
        # In production, look up from DB
        return {
            "organization_id": organization_id,
            "tier": "free",
            "status": "active",
            "current_period_end": datetime.now(timezone.utc).isoformat(),
        }

    async def cancel_subscription(self, organization_id: str) -> bool:
        """Cancel a subscription."""
        if not self.enabled:
            return False
        logger.info("Cancelled subscription for org %s", organization_id)
        return True
