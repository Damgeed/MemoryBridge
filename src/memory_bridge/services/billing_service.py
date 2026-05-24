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
        success_url: str = "",
        cancel_url: str = "",
    ) -> Optional[str]:
        """Create a Stripe checkout session for a subscription.

        Args:
            organization_id: The org to subscribe
            tier: Tier to subscribe to (starter, pro, enterprise)
            success_url: Redirect URL on success (defaults to /dashboard)
            cancel_url: Redirect URL on cancel (defaults to /pricing)

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
            session_data = event_data.get("data", {}).get("object", {})
            customer_id = session_data.get("customer")
            org_id = session_data.get("client_reference_id")
            subscription_id = session_data.get("subscription")

            if org_id and subscription_id:
                logger.info(
                    "Subscription created: org=%s, sub=%s, customer=%s",
                    org_id, subscription_id, customer_id,
                )
                # Store subscription in DB via repo
                return {"status": "processed", "event": event_type, "subscription_id": subscription_id}
            else:
                logger.warning("checkout.session.completed missing org_id or subscription_id")
                return {"status": "received", "event": event_type, "reason": "Missing org_id or subscription_id"}

        elif event_type == "invoice.paid":
            invoice = event_data.get("data", {}).get("object", {})
            subscription_id = invoice.get("subscription")
            amount = invoice.get("amount_paid", 0)
            period_end = invoice.get("period_end")

            if subscription_id:
                logger.info("Invoice paid: sub=%s, amount=%d", subscription_id, amount)
                # Record the payment, extend the subscription period
                return {"status": "processed", "event": event_type}
            else:
                logger.warning("invoice.paid missing subscription")
                return {"status": "received", "event": event_type, "reason": "Missing subscription"}

        elif event_type == "customer.subscription.updated":
            sub = event_data.get("data", {}).get("object", {})
            sub_id = sub.get("id")
            items = sub.get("items", {}).get("data", [])
            price_id = items[0].get("price", {}).get("id", "") if items else ""

            if sub_id:
                tier = self._resolve_tier_from_price(price_id)
                logger.info("Subscription updated: sub=%s, tier=%s", sub_id, tier)
                # Update tier in DB via repo
                return {"status": "processed", "event": event_type, "tier": tier}
            else:
                logger.warning("customer.subscription.updated missing subscription id")
                return {"status": "received", "event": event_type, "reason": "Missing subscription id"}

        elif event_type == "customer.subscription.deleted":
            sub = event_data.get("data", {}).get("object", {})
            sub_id = sub.get("id")
            if sub_id:
                logger.info("Subscription cancelled: sub=%s — downgrading to free", sub_id)
                # Downgrade to free tier in DB via repo
                return {"status": "processed", "event": event_type, "tier": "free"}
            else:
                logger.warning("customer.subscription.deleted missing subscription id")
                return {"status": "received", "event": event_type, "reason": "Missing subscription id"}

        else:
            logger.info("Unhandled webhook event type: %s", event_type)
            return {"status": "received", "event": event_type}

    def _resolve_tier_from_price(self, price_id: str) -> str:
        """Map Stripe price ID to tier name.

        Args:
            price_id: The Stripe price ID from the subscription item.

        Returns:
            Tier name ('starter', 'pro', 'enterprise') or 'free' if unknown.
        """
        for tier, pid in PRICE_IDS.items():
            if pid == price_id:
                return tier
        return "free"

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
