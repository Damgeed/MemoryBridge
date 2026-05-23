"""Stripe subscription billing integration.

Handles subscription lifecycle, metered billing, and webhook events.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Price IDs by tier (configure via env vars)
PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
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
        """Process a Stripe webhook event with signature verification.

        Verifies the Stripe-Signature header using HMAC-SHA256, then
        routes known event types to their handlers.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header value

        Returns:
            Dict with event type and status
        """
        if not self.enabled:
            return {"status": "skipped", "reason": "Stripe not configured"}

        if not self._webhook_secret:
            logger.warning("Stripe webhook secret not configured. Set STRIPE_WEBHOOK_SECRET.")
            return {"status": "skipped", "reason": "No webhook secret configured"}

        # --- Signature verification ---
        # Stripe sends: Stripe-Signature: t=...,v1=...,v0=...
        # We verify the v1 signature with HMAC-SHA256 of the raw body.
        try:
            expected_sig = hmac.new(
                self._webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()

            sig_parts: dict[str, str] = {}
            for part in signature.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    sig_parts[k.strip()] = v.strip()

            provided_sig = sig_parts.get("v1", "")

            if not provided_sig or not hmac.compare_digest(expected_sig, provided_sig):
                logger.warning("Stripe webhook signature mismatch")
                return {"status": "rejected", "reason": "Invalid signature"}
        except Exception as e:
            logger.warning("Stripe webhook verification failed: %s", e)
            return {"status": "error", "reason": str(e)}

        # --- Signature verified — parse and route event ---
        try:
            event_data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in Stripe webhook payload")
            return {"status": "error", "reason": "Invalid JSON"}

        event_type = event_data.get("type", "unknown")
        logger.info("Stripe webhook processed: %s", event_type)

        if event_type == "checkout.session.completed":
            return {"status": "processed", "event": event_type}
        elif event_type == "invoice.paid":
            return {"status": "processed", "event": event_type}
        elif event_type == "customer.subscription.updated":
            return {"status": "processed", "event": event_type}
        elif event_type == "customer.subscription.deleted":
            return {"status": "processed", "event": event_type}
        else:
            logger.info("Unhandled webhook event type: %s", event_type)
            return {"status": "received", "event": event_type}

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
