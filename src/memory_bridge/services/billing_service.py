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
from ..services.metering_service import TIER_LIMITS

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
        # StripeObject → dict so all .get() calls work
        session = session.to_dict()
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
            sub_data_raw = stripe.Subscription.retrieve(subscription_id)
            sub_data = sub_data_raw.to_dict()  # StripeObject → dict
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
                if user and user.get("id"):
                    await self.repo.update_user_stripe_customer(user["id"], customer_id)
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
        sub_data = event.data.object.to_dict()  # StripeObject → dict
        return await self._upsert_subscription_from_event(sub_data, "customer.subscription.created")

    async def _handle_subscription_updated(self, event) -> dict:
        """Handle customer.subscription.updated.

        Update subscription tier and status in the local database.
        """
        sub_data = event.data.object.to_dict()  # StripeObject → dict
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

        # If subscription is being set to free/canceled, trim excess API keys
        if self.repo and (tier == "free" or status == "canceled" or status == "incomplete_expired"):
            from ..services.metering_service import TIER_LIMITS
            free_limit = TIER_LIMITS.get("free", {}).get("max_api_keys", 5)
            try:
                deactivated = await self.repo.deactivate_excess_keys(org_id, free_limit)
                if deactivated:
                    logger.info("Webhook: deactivated %d excess keys for org %s (tier=%s, status=%s)", deactivated, org_id, tier, status)
            except Exception as e:
                logger.warning("Webhook: failed to deactivate keys for org %s: %s", org_id, e)

        # Also trim keys to the new tier's limit for any tier change (e.g. Pro→Starter)
        if self.repo and tier and tier != "free":
            from ..services.metering_service import TIER_LIMITS
            tier_limit = TIER_LIMITS.get(tier, {}).get("max_api_keys", 25)
            try:
                deactivated = await self.repo.deactivate_excess_keys(org_id, tier_limit)
                if deactivated:
                    logger.info("Webhook: deactivated %d excess keys for org %s (tier=%s, limit=%d)", deactivated, org_id, tier, tier_limit)
            except Exception as e:
                logger.warning("Webhook: failed to deactivate keys for org %s: %s", org_id, e)

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
        invoice = event.data.object.to_dict()  # StripeObject → dict
        subscription_id = invoice.get("subscription")
        amount = invoice.get("amount_paid", 0)

        if not subscription_id:
            logger.warning("invoice.paid missing subscription")
            return {"status": "received", "event": "invoice.paid", "reason": "Missing subscription"}

        # Fetch the subscription from Stripe to get updated period dates
        try:
            sub_data_raw = stripe.Subscription.retrieve(subscription_id)
            sub_data = sub_data_raw.to_dict()  # StripeObject → dict
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
            else:
                # No local record yet (webhook ordering) — create one
                metadata = sub_data.get("metadata", {})
                org_id = metadata.get("organization_id", "")
                customer_id = sub_data.get("customer", "")
                sub = Subscription(
                    id=subscription_id,
                    organization_id=org_id or f"stripe-{subscription_id[:8]}",
                    stripe_customer_id=customer_id or "",
                    tier=tier,
                    status=status,
                    current_period_start=datetime.fromtimestamp(sub_data.get("current_period_start", 0), tz=timezone.utc) if sub_data.get("current_period_start") else None,
                    current_period_end=datetime.fromtimestamp(sub_data.get("current_period_end", 0), tz=timezone.utc) if sub_data.get("current_period_end") else None,
                )
                await self.repo.store_subscription(sub)
                logger.info("Invoice paid: created new subscription record for sub=%s tier=%s", subscription_id, tier)

        logger.info(
            "Invoice paid: sub=%s, amount=%d, period updated",
            subscription_id,
            amount,
        )
        return {"status": "processed", "event": "invoice.paid"}

    async def _handle_subscription_deleted(self, event) -> dict:
        """Handle customer.subscription.deleted.

        If the subscription had a pending_tier set, create a new subscription
        at that tier (Netflix-style downgrade at period end).
        Otherwise, downgrade to free tier.
        """
        sub_data = event.data.object.to_dict()  # StripeObject → dict
        sub_id = sub_data.get("id")

        if not sub_id:
            logger.warning("customer.subscription.deleted missing subscription id")
            return {"status": "received", "event": "customer.subscription.deleted", "reason": "Missing subscription id"}

        if self.repo:
            # Check for pending tier on the local record
            existing = await self.repo.get_subscription_by_id(sub_id)
            pending_tier = existing.pending_tier if existing else ""
            org_id = existing.organization_id if existing else sub_data.get("metadata", {}).get("organization_id", "")
            customer_id = existing.stripe_customer_id if existing else sub_data.get("customer", "")

            if pending_tier and pending_tier != "free":
                # Auto-create a new subscription at the downgraded tier
                try:
                    target_price_id = PRICE_IDS.get(pending_tier)
                    if target_price_id and customer_id:
                        new_sub = stripe.Subscription.create(
                            customer=customer_id,
                            items=[{"price": target_price_id}],
                            metadata={"organization_id": org_id, "tier": pending_tier},
                        )
                        logger.info(
                            "Auto-created subscription %s for org %s at tier %s (downgrade from period end)",
                            new_sub.id,
                            org_id,
                            pending_tier,
                        )
                        # Update local record with new sub
                        if existing:
                            new_sub_data = new_sub.to_dict() if hasattr(new_sub, 'to_dict') else new_sub
                            existing.id = new_sub.id
                            existing.tier = pending_tier
                            existing.status = new_sub_data.get('status', 'active')
                            existing.pending_tier = ""
                            existing.current_period_start = datetime.fromtimestamp(
                                new_sub_data['current_period_start'], tz=timezone.utc
                            ) if new_sub_data.get('current_period_start') else None
                            existing.current_period_end = datetime.fromtimestamp(
                                new_sub_data['current_period_end'], tz=timezone.utc
                            ) if new_sub_data.get('current_period_end') else None
                            existing.updated_at = datetime.now(timezone.utc)
                            await self.repo.store_subscription(existing)
                        return {"status": "processed", "event": "customer.subscription.deleted", "tier": pending_tier}
                except Exception as e:
                    logger.error("Failed to auto-create subscription for org %s at tier %s: %s", org_id, pending_tier, e)
                    # Fall through to free tier downgrade

            # Downgrade to free tier
            updated = await self.repo.update_subscription_tier(sub_id, "free") if sub_id else None
            if updated:
                updated.status = "canceled"
                updated.tier = "free"
                updated.pending_tier = ""
                updated.updated_at = datetime.now(timezone.utc)
                await self.repo.store_subscription(updated)
            elif org_id:
                alt = await self.repo.get_subscription_by_org(org_id)
                if alt:
                    alt.tier = "free"
                    alt.status = "canceled"
                    alt.pending_tier = ""
                    alt.updated_at = datetime.now(timezone.utc)
                    await self.repo.store_subscription(alt)

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

    async def cancel_subscription(self, organization_id: str, target_tier: str = "free") -> bool:
        """Cancel a subscription at period end via Stripe API.

        Args:
            organization_id: The organization whose subscription to cancel.
            target_tier: The tier to switch to at period end (default: "free").

        Returns:
            True if the subscription was successfully scheduled for cancellation.
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
                "Subscription %s for org %s set to cancel at period end; pending tier = %s",
                sub.id,
                organization_id,
                target_tier,
            )

            # Store the pending tier so the webhook can create the new subscription
            sub.pending_tier = target_tier
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


    async def preview_upgrade_cost(self, organization_id: str, target_tier: str) -> dict:
        """Preview the prorated cost of upgrading without making changes."""
        target_price_id = PRICE_IDS.get(target_tier)
        if not target_price_id:
            return {"can_upgrade": False, "error": f"No price configured for tier '{target_tier}'."}

        try:
            sub = await self.repo.get_subscription_by_org(organization_id)
        except Exception as e:
            return {"can_upgrade": False, "error": f"Failed to look up subscription: {e}"}

        if not sub or not sub.id:
            return {"can_upgrade": False, "error": "No active subscription found."}

        try:
            stripe_sub = stripe.Subscription.retrieve(sub.id)
            items = stripe_sub.items.data if hasattr(stripe_sub, 'items') else []
            if not items:
                return {"can_upgrade": False, "error": "No items on existing subscription."}

            item_id = items[0].id

            # Preview via upcoming invoice
            upcoming = stripe.Invoice.upcoming(
                subscription=sub.id,
                subscription_items=[{
                    'id': item_id,
                    'price': target_price_id,
                }],
                subscription_proration_behavior='create_prorations',
            )

            upcoming_data = upcoming.to_dict() if hasattr(upcoming, 'to_dict') else upcoming
            amount_due = upcoming_data.get('amount_due', 0) / 100.0
            amount_remaining = upcoming_data.get('amount_remaining', 0) / 100.0
            credit_note = upcoming_data.get('starting_balance', 0) / 100.0

            tier_names = {'free': 'Free', 'starter': 'Starter', 'pro': 'Pro', 'enterprise': 'Enterprise'}
            old_name = tier_names.get(sub.tier, sub.tier.title())
            new_name = tier_names.get(target_tier, target_tier.title())

            # Calculate proration breakdown
            lines = upcoming_data.get('lines', {}).get('data', [])
            proration_details = []
            for line in lines:
                if line.get('proration'):
                    desc = line.get('description', '')
                    amt = line.get('amount', 0) / 100.0
                    proration_details.append({"description": desc, "amount": amt})

            return {
                "can_upgrade": True,
                "current_plan": old_name,
                "new_plan": new_name,
                "amount_due": round(amount_due, 2),
                "proration_details": proration_details,
                "message": f"Upgrade from {old_name} to {new_name}: prorated charge of ${amount_due:.2f} today. Your next billing date stays the same."
            }
        except Exception as e:
            return {"can_upgrade": False, "error": f"Could not preview: {e}"}

    async def upgrade_subscription(self, organization_id: str, target_tier: str) -> tuple[bool, str]:
        """Upgrade an existing subscription to a higher tier with proration.

        Returns (success, detail_message).
        """
        if not self.enabled:
            return False, "Stripe not configured."

        if not self.repo:
            return False, "No database configured."

        target_price_id = PRICE_IDS.get(target_tier)
        if not target_price_id:
            return False, f"No price configured for tier '{target_tier}'."

        try:
            sub = await self.repo.get_subscription_by_org(organization_id)
        except Exception as e:
            return False, f"Failed to look up subscription: {e}"

        if not sub or not sub.id:
            return False, "No active subscription found. Use checkout to subscribe."

        try:
            # Get the subscription from Stripe to get the subscription item ID
            stripe_sub = stripe.Subscription.retrieve(sub.id)
            sub_data = stripe_sub.to_dict() if hasattr(stripe_sub, 'to_dict') else stripe_sub
            items = sub_data.get('items', {}).get('data', [])
            if not items:
                return False, "No items on existing subscription."

            item_id = items[0].get('id')

            # Modify subscription to switch price with proration
            updated = stripe.Subscription.modify(
                sub.id,
                items=[{
                    'id': item_id,
                    'price': target_price_id,
                }],
                proration_behavior='create_prorations',
                metadata={'organization_id': organization_id, 'tier': target_tier},
            )

            # Calculate prorated amount for message
            latest_invoice = stripe.Invoice.list(subscription=sub.id, limit=1)
            prorated_amount = 0
            if latest_invoice.data:
                prorated_amount = latest_invoice.data[0].amount_due / 100.0

            tier_names = {'free': 'Free', 'starter': 'Starter', 'pro': 'Pro', 'enterprise': 'Enterprise'}
            old_name = tier_names.get(sub.tier, sub.tier.title())
            new_name = tier_names.get(target_tier, target_tier.title())

            # Now update local record (after capturing old tier name)
            updated_data = updated.to_dict() if hasattr(updated, 'to_dict') else updated
            sub.tier = target_tier
            sub.pending_tier = ""
            sub.status = updated_data.get('status', 'active')
            sub.updated_at = datetime.now(timezone.utc)
            if updated_data.get('current_period_start'):
                sub.current_period_start = datetime.fromtimestamp(updated_data.get('current_period_start'), tz=timezone.utc)
            if updated_data.get('current_period_end'):
                sub.current_period_end = datetime.fromtimestamp(updated_data.get('current_period_end'), tz=timezone.utc)
            await self.repo.store_subscription(sub)

            logger.info(
                "Upgraded subscription %s from %s to %s — prorated charge ~$%.2f",
                sub.id, old_name, new_name, prorated_amount,
            )

            return True, f"Upgraded from {old_name} to {new_name}. Prorated charge: ${prorated_amount:.2f}"

        except stripe.error.StripeError as e:
            logger.error("Stripe upgrade failed for org %s: %s", organization_id, e)
            return False, f"Stripe error: {e}"
