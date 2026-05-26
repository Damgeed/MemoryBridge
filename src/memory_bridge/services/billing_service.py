"""Stripe subscription billing integration.

Handles subscription lifecycle, metered billing, and webhook events
using real Stripe API calls.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import stripe

from ..models import Subscription

logger = logging.getLogger(__name__)

# Price IDs by tier (configure via env vars)
PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
    "pro": os.environ.get("STRIPE_PRICE_PRO", ""),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", ""),
}

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")


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
        if self._stripe_key:
            stripe.api_key = self._stripe_key

    @property
    def enabled(self) -> bool:
        """Whether Stripe integration is active."""
        return bool(self._stripe_key)

    @property
    def app_url(self) -> str:
        """The base application URL for redirects."""
        return APP_URL

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
            Checkout URL or None if Stripe is not configured or price not found
        """
        if not self.enabled:
            logger.warning("Stripe not configured. Set STRIPE_SECRET_KEY env var.")
            return None

        price_id = PRICE_IDS.get(tier)
        if not price_id:
            logger.warning("No price ID configured for tier '%s'", tier)
            return None

        success = success_url or f"{APP_URL}/dashboard?session_id={{CHECKOUT_SESSION_ID}}"
        cancel = cancel_url or f"{APP_URL}/pricing"

        try:
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                client_reference_id=organization_id,
                success_url=success,
                cancel_url=cancel,
                subscription_data={"metadata": {"organization_id": organization_id, "tier": tier}},
                metadata={"organization_id": organization_id, "tier": tier},
            )
            logger.info(
                "Created checkout session %s for org %s (tier: %s)",
                session.id,
                organization_id,
                tier,
            )
            return session.url
        except stripe.error.StripeError as e:
            logger.error("Stripe checkout session creation failed: %s", e)
            return None

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """Process a Stripe webhook event with signature verification.

        Uses the stripe library's Webhook.construct_event for robust
        signature verification, then routes known event types to their
        handlers.

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

        # --- Signature verification via stripe library ---
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            logger.warning("Stripe webhook signature verification failed: %s", e)
            return {"status": "rejected", "reason": str(e)}

        event_type = event.type
        logger.info("Stripe webhook received: %s", event_type)

        # Route event to handler
        handler = self._get_event_handler(event_type)
        if handler:
            try:
                return await handler(event)
            except Exception as e:
                logger.error("Webhook handler %s failed: %s", event_type, e)
                return {"status": "error", "event": event_type, "reason": str(e)}

        logger.info("Unhandled webhook event type: %s", event_type)
        return {"status": "received", "event": event_type}

    def _get_event_handler(self, event_type: str):
        """Map event types to async handler methods."""
        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "invoice.paid": self._handle_invoice_paid,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "customer.subscription.created": self._handle_subscription_created,
        }
        return handlers.get(event_type)

    async def _handle_checkout_completed(self, event) -> dict:
        """Handle checkout.session.completed.

        Retrieves full subscription details from Stripe and persists
        the subscription record to the local database.
        """
        session = event.data.object
        org_id = session.get("client_reference_id")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")

        if not org_id or not subscription_id:
            logger.warning(
                "checkout.session.completed missing org_id=%s or subscription_id=%s",
                org_id,
                subscription_id,
            )
            return {
                "status": "received",
                "event": "checkout.session.completed",
                "reason": "Missing org_id or subscription_id",
            }

        # Fetch full subscription details from Stripe
        try:
            sub_data = stripe.Subscription.retrieve(subscription_id)
        except stripe.error.StripeError as e:
            logger.error("Failed to retrieve subscription %s: %s", subscription_id, e)
            return {"status": "error", "event": "checkout.session.completed", "reason": str(e)}

        # Resolve tier from the price ID on the first item
        items = sub_data.get("items", {}).get("data", [])
        price_id = items[0].get("price", {}).get("id", "") if items else ""
        tier = self._resolve_tier_from_price(price_id)

        sub = Subscription(
            id=subscription_id,
            organization_id=org_id,
            stripe_customer_id=customer_id or "",
            tier=tier,
            status=sub_data.get("status", "active"),
            current_period_start=datetime.fromtimestamp(sub_data.get("current_period_start", 0), tz=timezone.utc) if sub_data.get("current_period_start") else None,
            current_period_end=datetime.fromtimestamp(sub_data.get("current_period_end", 0), tz=timezone.utc) if sub_data.get("current_period_end") else None,
        )

        if self.repo:
            await self.repo.store_subscription(sub)

        # Link the Stripe customer ID to the user record for bidirectional recovery
        if customer_id and org_id and self.repo:
            try:
                user = await self.repo.get_user_by_organization_id(org_id)
                if user:
                    user_id = user.get("id")
                    import aiosqlite
                    db_path = getattr(self.repo, 'db_path', None)
                    if db_path:
                        async with aiosqlite.connect(db_path) as db:
                            await db.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ?", (customer_id, user_id))
                            await db.commit()
                    else:
                        conn = getattr(self.repo, 'pool', None)
                        if conn:
                            async with conn.acquire() as c:
                                await c.execute("UPDATE users SET stripe_customer_id = $1 WHERE id = $2", customer_id, user_id)
                    logger.info("Linked stripe_customer_id=%s to user org=%s", customer_id, org_id)
            except Exception as e:
                logger.warning("Could not link stripe_customer_id to user org=%s: %s", org_id, e)

        logger.info(
            "Subscription stored: org=%s, sub=%s, tier=%s, customer=%s",
            org_id,
            subscription_id,
            tier,
            customer_id,
        )
        return {
            "status": "processed",
            "event": "checkout.session.completed",
            "subscription_id": subscription_id,
            "tier": tier,
        }

    async def _handle_subscription_created(self, event) -> dict:
        """Handle customer.subscription.created.

        Store or update the subscription record in the local database.
        """
        sub_data = event.data.object
        return await self._upsert_subscription_from_event(sub_data, "customer.subscription.created")

    async def _handle_subscription_updated(self, event) -> dict:
        """Handle customer.subscription.updated.

        Update subscription tier and status in the local database.
        """
        sub_data = event.data.object
        return await self._upsert_subscription_from_event(sub_data, "customer.subscription.updated")

    async def _upsert_subscription_from_event(self, sub_data, event_type: str) -> dict:
        """Upsert a subscription record from Stripe subscription data."""
        sub_id = sub_data.get("id")
        if not sub_id:
            logger.warning("%s missing subscription id", event_type)
            return {"status": "received", "event": event_type, "reason": "Missing subscription id"}

        # Try to find the org from the metadata or from an existing record
        metadata = sub_data.get("metadata", {})
        org_id = metadata.get("organization_id")

        if not org_id and self.repo:
            # Fall back: look up by subscription ID in our DB
            existing = await self._find_sub_by_id(sub_id)
            org_id = existing.organization_id if existing else None

        if not org_id:
            logger.warning(
                "%s: could not resolve organization_id for sub %s",
                event_type,
                sub_id,
            )
            return {"status": "received", "event": event_type, "reason": "Could not resolve organization_id"}

        customer_id = sub_data.get("customer", "")
        items = sub_data.get("items", {}).get("data", [])
        price_id = items[0].get("price", {}).get("id", "") if items else ""
        tier = self._resolve_tier_from_price(price_id)
        status = sub_data.get("status", "active")

        sub = Subscription(
            id=sub_id,
            organization_id=org_id,
            stripe_customer_id=customer_id or "",
            tier=tier,
            status=status,
            current_period_start=datetime.fromtimestamp(sub_data.get("current_period_start", 0), tz=timezone.utc) if sub_data.get("current_period_start") else None,
            current_period_end=datetime.fromtimestamp(sub_data.get("current_period_end", 0), tz=timezone.utc) if sub_data.get("current_period_end") else None,
        )

        if self.repo:
            await self.repo.store_subscription(sub)

        logger.info(
            "Subscription upserted from %s: sub=%s, org=%s, tier=%s, status=%s",
            event_type,
            sub_id,
            org_id,
            tier,
            status,
        )
        return {"status": "processed", "event": event_type, "tier": tier}

    async def _handle_invoice_paid(self, event) -> dict:
        """Handle invoice.paid.

        Updates the subscription period on successful payment.
        """
        invoice = event.data.object
        subscription_id = invoice.get("subscription")
        amount = invoice.get("amount_paid", 0)

        if not subscription_id:
            logger.warning("invoice.paid missing subscription")
            return {"status": "received", "event": "invoice.paid", "reason": "Missing subscription"}

        # Fetch the subscription from Stripe to get updated period dates
        try:
            sub_data = stripe.Subscription.retrieve(subscription_id)
        except stripe.error.StripeError as e:
            logger.error("Failed to retrieve subscription %s: %s", subscription_id, e)
            return {"status": "error", "event": "invoice.paid", "reason": str(e)}

        if self.repo:
            items = sub_data.get("items", {}).get("data", [])
            price_id = items[0].get("price", {}).get("id", "") if items else ""
            tier = self._resolve_tier_from_price(price_id)
            status = sub_data.get("status", "active")

            # Try to find existing subscription to get org_id
            existing = await self._find_sub_by_id(subscription_id)
            if existing:
                existing.tier = tier
                existing.status = status
                existing.current_period_start = datetime.fromtimestamp(sub_data.get("current_period_start", 0), tz=timezone.utc) if sub_data.get("current_period_start") else None
                existing.current_period_end = datetime.fromtimestamp(sub_data.get("current_period_end", 0), tz=timezone.utc) if sub_data.get("current_period_end") else None
                existing.updated_at = datetime.now(timezone.utc)
                await self.repo.store_subscription(existing)

        logger.info(
            "Invoice paid: sub=%s, amount=%d, period updated",
            subscription_id,
            amount,
        )
        return {"status": "processed", "event": "invoice.paid"}

    async def _handle_subscription_deleted(self, event) -> dict:
        """Handle customer.subscription.deleted.

        Downgrades the subscription to free tier in the local database.
        """
        sub_data = event.data.object
        sub_id = sub_data.get("id")

        if not sub_id:
            logger.warning("customer.subscription.deleted missing subscription id")
            return {"status": "received", "event": "customer.subscription.deleted", "reason": "Missing subscription id"}

        if self.repo:
            # Downgrade to free tier
            updated = await self.repo.update_subscription_tier(sub_id, "free")
            if updated:
                # Also update status to canceled
                updated.status = "canceled"
                updated.updated_at = datetime.now(timezone.utc)
                await self.repo.store_subscription(updated)
            else:
                # Subscription not in local DB yet — try to find by org metadata
                metadata = sub_data.get("metadata", {})
                org_id = metadata.get("organization_id")
                if org_id:
                    existing = await self.repo.get_subscription_by_org(org_id)
                    if existing:
                        existing.tier = "free"
                        existing.status = "canceled"
                        existing.updated_at = datetime.now(timezone.utc)
                        await self.repo.store_subscription(existing)

        logger.info("Subscription cancelled: sub=%s — downgraded to free", sub_id)
        return {"status": "processed", "event": "customer.subscription.deleted", "tier": "free"}

    async def _find_sub_by_id(self, sub_id: str) -> Optional[Subscription]:
        """Find a subscription by its Stripe ID across all orgs."""
        if not self.repo:
            return None
        return await self.repo.get_subscription_by_id(sub_id)

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

        Returns dict with id, organization_id, stripe_customer_id, tier,
        status, current_period_start, current_period_end, created_at,
        updated_at, or None if not found.
        """
        if not self.repo:
            return {
                "organization_id": organization_id,
                "tier": "free",
                "status": "active",
                "current_period_end": datetime.now(timezone.utc).isoformat(),
            }

        try:
            sub = await self.repo.get_subscription_by_org(organization_id)
        except Exception as e:
            logger.error("Failed to get subscription for org %s: %s", organization_id, e)
            return None

        if sub is None:
            return None

        return {
            "id": sub.id,
            "organization_id": sub.organization_id,
            "stripe_customer_id": sub.stripe_customer_id,
            "tier": sub.tier,
            "status": sub.status,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "created_at": sub.created_at.isoformat(),
            "updated_at": sub.updated_at.isoformat(),
        }

    async def cancel_subscription(self, organization_id: str) -> bool:
        """Cancel a subscription at period end via Stripe API.

        Args:
            organization_id: The organization whose subscription to cancel.

        Returns:
            True if the subscription was successfully canceled, False otherwise.
        """
        if not self.enabled:
            logger.warning("Stripe not configured. Cannot cancel subscription.")
            return False

        if not self.repo:
            logger.warning("No repo configured. Cannot cancel subscription.")
            return False

        try:
            sub = await self.repo.get_subscription_by_org(organization_id)
        except Exception as e:
            logger.error("Failed to look up subscription for org %s: %s", organization_id, e)
            return False

        if sub is None or not sub.id:
            logger.warning("No subscription found for org %s", organization_id)
            return False

        try:
            stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
            logger.info(
                "Subscription %s for org %s set to cancel at period end",
                sub.id,
                organization_id,
            )

            # Update local status
            sub.status = "canceled"
            sub.updated_at = datetime.now(timezone.utc)
            await self.repo.store_subscription(sub)

            return True
        except stripe.error.StripeError as e:
            logger.error(
                "Failed to cancel subscription %s for org %s: %s",
                sub.id,
                organization_id,
                e,
            )
            return False
