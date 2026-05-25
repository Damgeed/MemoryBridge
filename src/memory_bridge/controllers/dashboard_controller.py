"""Memory Bridge dashboard — self-service API key management and setup guide.

Provides authenticated endpoints for users to create, list, and revoke
their own API keys, plus serves the dashboard page with installation
instructions and copy-to-clipboard terminal commands.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

from ..dependencies import get_storage
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=True)
async def get_dashboard_page():
    """Serve the Memory Bridge dashboard page."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "dashboard.html")
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>Dashboard page not found</h1>",
            status_code=200,
        )
    with open(html_path) as f:
        content = f.read()
    return Response(
        content=content,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.post("/keys")
async def create_api_key(
    request: Request,
    label: str = Query("default", description="Human-readable label for the key"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a new API key for the authenticated user/organization.

    Returns the full key — this is the only time the plaintext key is shown.
    Each tier has a maximum number of API keys (defined in TIER_LIMITS).
    """
    org_id = _resolve_org(request)

    # Check tier limit for API keys
    from ..services.metering_service import TIER_LIMITS

    # Determine the user's tier
    try:
        from ..models import Subscription
        subs = await storage.list_subscriptions()
        sub = next((s for s in subs if s.organization_id == org_id), None)
        tier = sub.tier if sub else "free"
    except Exception:
        tier = "free"

    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    max_keys = limits.get("max_api_keys", 5)

    # Count ALL keys ever created for this org (including revoked)
    all_keys = await storage.list_api_keys()
    org_keys_total = [k for k in all_keys if k.get("project_id") == org_id]
    if len(org_keys_total) >= max_keys:
        if max_keys == 5:
            detail = f"Free tier: max {max_keys} API keys total. This account has already generated {max_keys} keys. Upgrade to a paid plan for unlimited keys."
        else:
            detail = f"{tier.title()} tier: max {max_keys} API keys. You have reached the limit. Upgrade your plan for more keys."
        raise HTTPException(
            status_code=429,
            detail=detail,
        )

    result = await storage.create_api_key(label=label, project_id=org_id)
    # Tag the key metadata with the org for lookup
    return {
        "id": result["id"],
        "key": result["key"],
        "label": result["label"],
        "created_at": result["created_at"],
    }


@router.get("/welcome")
async def welcome_setup(
    session_id: str = Query("", description="Stripe checkout session ID"),
    organization_id: str = Query("", description="Organization ID passed from checkout response"),
    tier: str = Query("", description="Tier passed from checkout (starter/pro/enterprise)"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Legacy welcome endpoint — kept for backward compatibility.

    New flow: user registers first, then purchases. Stripe webhook
    confirms payment and stores the subscription. This endpoint is
    no longer the primary flow but remains to handle old redirects.
    """
    # Use org_id from frontend if provided
    org_id = organization_id.strip() if organization_id else ""
    actual_tier = tier.strip().lower() if tier.strip() else "free"

    if not org_id and session_id:
        try:
            import stripe
            sess = stripe.checkout.Session.retrieve(session_id)
            if hasattr(sess, "client_reference_id") and sess.client_reference_id:
                org_id = sess.client_reference_id
            elif sess.metadata:
                org_id = sess.metadata.get("organization_id", "")
            if not actual_tier and sess.metadata:
                actual_tier = sess.metadata.get("tier", "free")
        except Exception:
            pass

    if not org_id:
        import uuid
        org_id = str(uuid.uuid4())

    # Best-effort subscription storage (webhook will finalize)
    try:
        from ..models.subscription import Subscription
        from datetime import datetime, timezone

        # Check if a subscription already exists for this org (UNIQUE constraint on org_id)
        existing = await storage.get_subscription_by_org(org_id)
        if existing:
            # Update existing subscription tier
            existing.tier = actual_tier
            existing.status = "active"
            existing.updated_at = datetime.now(timezone.utc)
            await storage.store_subscription(existing)
            logger.info("Welcome: updated existing subscription for org=%s to tier=%s", org_id, actual_tier)
        else:
            # Create new subscription
            sub = Subscription(
                id=f"welcome-{org_id[:8]}",
                organization_id=org_id,
                stripe_customer_id="",
                tier=actual_tier,
                status="active",
                current_period_start=datetime.now(timezone.utc),
                current_period_end=datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1),
            )
            await storage.store_subscription(sub)
            logger.info("Welcome: stored new subscription for org=%s tier=%s", org_id, actual_tier)
    except Exception as e:
        logger.error("Welcome: subscription storage failed for org=%s: %s", org_id, e)

    # Create API key for this org
    try:
        result = await storage.create_api_key(label=f"welcome-{actual_tier}", project_id=org_id)
        return {
            "welcome": True,
            "has_keys": False,
            "key": result["key"],
            "id": result["id"],
            "label": result["label"],
            "tier": actual_tier,
            "organization_id": org_id,
            "created_at": result.get("created_at", ""),
        }
    except Exception as e:
        return {
            "welcome": True,
            "has_keys": True,
            "tier": actual_tier,
            "organization_id": org_id,
            "hint": "Already has keys. Sign in to access them.",
        }


@router.get("/keys")
async def list_api_keys(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """List all active API keys for the authenticated user/organization.

    Only returns key hashes and metadata — never the plaintext key.
    """
    org_id = _resolve_org(request)
    all_keys = await storage.list_api_keys()
    # Filter keys matching this org (project_id)
    user_keys = [k for k in all_keys if k.get("project_id") == org_id or not k.get("project_id")]
    return {"keys": user_keys}


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Revoke an API key. It can no longer be used for authentication."""
    revoked = await storage.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True, "key_id": key_id}


@router.post("/restore-subscription")
async def restore_subscription(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Restore a subscription record from Stripe for the current org.

    For users who paid before the welcome endpoint reliably stored
    their subscription locally. Searches Stripe for a checkout session
    matching this org_id and recreates the local subscription record.
    """
    org_id = _resolve_org(request)
    if not org_id or org_id in ("default", "demo", ""):
        return {"restored": False, "tier": "free", "reason": "No org_id"}

    # Check if already have a subscription
    try:
        existing = await storage.get_subscription_by_org(org_id)
        if existing and existing.status not in ("canceled",):
            return {"restored": False, "tier": existing.tier, "reason": "Already exists"}
    except Exception:
        pass

    # Try to find Stripe checkout session by client_reference_id
    try:
        import stripe
        import asyncio
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            return {"restored": False, "tier": "free", "reason": "Stripe not configured"}

        # Run Stripe call in thread pool with timeout (sync SDK in async handler)
        sessions = await asyncio.wait_for(
            asyncio.to_thread(
                stripe.checkout.Session.list,
                client_reference_id=org_id,
                limit=5,
                expand=["data.subscription"],
            ),
            timeout=10.0,
        )
        for sess in sessions.data:
            if sess.status == "complete" and sess.subscription:
                sub_data = sess.subscription
                from ..models.subscription import Subscription
                from datetime import datetime, timezone
                from ..services.billing_service import PRICE_IDS

                # Resolve tier from price
                tier = sess.metadata.get("tier", "free") if sess.metadata else "free"
                items = sub_data.get("items", {}).get("data", [])
                if items:
                    price_id = items[0].get("price", {}).get("id", "")
                    for t, pid in PRICE_IDS.items():
                        if pid == price_id:
                            tier = t
                            break

                sub = Subscription(
                    id=sub_data.get("id", f"restored-{org_id[:8]}"),
                    organization_id=org_id,
                    stripe_customer_id=sess.get("customer", "") or "",
                    tier=tier,
                    status=sub_data.get("status", "active"),
                    current_period_start=datetime.fromtimestamp(
                        sub_data.get("current_period_start", 0), tz=timezone.utc
                    ) if sub_data.get("current_period_start") else datetime.now(timezone.utc),
                    current_period_end=datetime.fromtimestamp(
                        sub_data.get("current_period_end", 0), tz=timezone.utc
                    ) if sub_data.get("current_period_end") else None,
                )
                await storage.store_subscription(sub)
                logger.info("Subscription restored: org=%s tier=%s sub=%s", org_id, tier, sub.id)
                return {"restored": True, "tier": tier}

        return {"restored": False, "tier": "free", "reason": "No matching Stripe session"}
    except ImportError:
        return {"restored": False, "tier": "free", "reason": "Stripe not installed"}
    except asyncio.TimeoutError:
        logger.warning("Restore subscription timed out for org=%s", org_id)
        return {"restored": False, "tier": "free", "reason": "Stripe API timed out"}
    except Exception as e:
        logger.warning("Restore subscription failed for org=%s: %s", org_id, e)
        return {"restored": False, "tier": "free", "reason": str(e)}


@router.get("/data")
async def get_dashboard_data(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Get dashboard data: subscription info, key count, memory count."""
    org_id = _resolve_org(request)

    # Get subscription
    sub = None
    try:
        sub = await storage.get_subscription_by_org(org_id)
    except Exception:
        pass

    # Get key count
    keys = await storage.list_api_keys()
    user_keys = [k for k in keys if k.get("project_id") == org_id or not k.get("project_id")]
    active_keys = [k for k in user_keys if k.get("is_active", True)]

    # Get memory count
    mem_count = 0
    try:
        memories = await storage.query_memories(limit=1, offset=0)
        # Try to get total count - may not be available in all backends
        mem_count = len(memories)
    except Exception:
        pass

    tier = sub.tier if sub else "free"
    if sub and sub.status == "canceled":
        tier = "free"

    return {
        "organization_id": org_id,
        "tier": tier,
        "status": sub.status if sub else "active",
        "active_keys": len(active_keys),
        "total_keys": len(user_keys),
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
    }


def _resolve_org(request: Request) -> str:
    """Resolve the organization ID from the authenticated request.

    Falls back to a session-based ID for demo/open-mode users.
    """
    auth = getattr(request.state, "auth", None)
    if auth:
        key_id = auth.get("key_id", "")
        # Nicer display for demo key
        if key_id == "demo:public":
            return "demo"
        return auth.get("project_id") or key_id
    return "default"


@router.post("/free-signup")
async def free_signup(
    email: str = Query("", description="Email address for abuse prevention"),
    request: Request = None,
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a free-tier API key with abuse prevention.

    Each email can only sign up once. Each IP has a 30-day cooldown.
    Returns the plaintext API key for dashboard login.
    """
    import hashlib
    import uuid
    from datetime import datetime, timezone, timedelta

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid email address.")

    email_clean = email.strip().lower()
    email_hash = hashlib.sha256(email_clean.encode()).hexdigest()[:16]

    # Get client IP
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    # Check email already used
    existing = await storage.get_metric(f"free_signup:email:{email_hash}")
    if existing is not None:
        raise HTTPException(status_code=429, detail="This email has already claimed a free API key. Use the recovery form if you lost it.")

    # Check IP cooldown (30 days)
    existing_ip = await storage.get_metric(f"free_signup:ip:{ip_hash}")
    if existing_ip is not None:
        try:
            cooldown = datetime.fromisoformat(existing_ip)
            if datetime.now(timezone.utc) - cooldown < timedelta(days=30):
                remaining = (cooldown + timedelta(days=30) - datetime.now(timezone.utc)).days
                raise HTTPException(
                    status_code=429,
                    detail=f"Free tier already used from this network. Please try again in {remaining} day(s) or subscribe to a paid plan.",
                )
        except (ValueError, TypeError):
            pass  # Stale data, allow through

    # Create organization
    org_id = str(uuid.uuid4())

    # Store subscription record for free tier tracking
    from ..models import Subscription
    sub = Subscription(
        id=f"free-{org_id[:8]}",
        organization_id=org_id,
        stripe_customer_id="",
        tier="free",
        status="active",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=365 * 100),  # effectively never expires
    )
    try:
        await storage.store_subscription(sub)
    except Exception as e:
        logger.warning("Could not store free subscription: %s", e)

    # Create API key
    result = await storage.create_api_key(label="free-key", project_id=org_id)

    # Record abuse prevention data
    now = datetime.now(timezone.utc).isoformat()
    try:
        await storage.initialize_metric(f"free_signup:email:{email_hash}", now)
        await storage.initialize_metric(f"free_signup:ip:{ip_hash}", now)
    except Exception as e:
        logger.warning("Could not record abuse prevention metric: %s", e)

    logger.info("Free signup: org=%s, email=%s, ip=%s", org_id, email_clean, client_ip)
    # Create a user account for this free signup
    try:
        import bcrypt
        from ..models import User as UserModel
        password = email_clean.split("@")[0] + str(uuid.uuid4())[:4]
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = UserModel(
            email=email_clean,
            password_hash=password_hash,
            name=email_clean.split("@")[0],
            organization_id=org_id,
        )
        await storage.create_user(user)
    except Exception as e:
        logger.warning("Could not create free user account: %s", e)

    return {
        "key": result["key"],
        "id": result["id"],
        "label": result["label"],
        "tier": "free",
        "organization_id": org_id,
    }

@router.post("/recover")
async def recover_api_key(
    email: str = Query("", description="Email used during Stripe checkout"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Recover a lost API key by verifying the purchaser's email via Stripe.

    Looks up the Stripe customer by email, finds their active subscription,
    and issues a new API key — no charge. Returns the plaintext key once.
    """
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid email address.")

    import stripe
    try:
        customers = stripe.Customer.list(email=email, limit=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not verify purchase: {e}")

    if not customers.data:
        raise HTTPException(
            status_code=404,
            detail="No purchase found for that email. Make sure you use the email you paid with.",
        )

    # Find the most recent customer with an active subscription
    org_id = None
    found_tier = "free"
    for customer in customers.data:
        try:
            subs = stripe.Subscription.list(customer=customer.id, limit=5, status="all")
        except Exception:
            continue
        for sub in subs.data:
            active_statuses = {"active", "trialing", "past_due"}
            if sub.get("status") not in active_statuses:
                continue
            metadata = sub.get("metadata", {})
            if metadata.get("organization_id"):
                org_id = metadata["organization_id"]
                items = sub.get("items", {}).get("data", [])
                if items:
                    from ..services.billing_service import PRICE_IDS
                    price_id = items[0].get("price", {}).get("id", "")
                    for t, pid in PRICE_IDS.items():
                        if pid == price_id:
                            found_tier = t
                            break
                break
        if org_id:
            break

    if not org_id:
        # Last resort: check stored subscriptions by stripe_customer_id
        for customer in customers.data:
            try:
                sub = await storage.get_subscription_by_stripe_customer(customer.id)
            except Exception:
                continue
            if sub:
                org_id = sub.organization_id
                found_tier = sub.tier
                break

    if not org_id:
        raise HTTPException(
            status_code=404,
            detail="Found your account but no active subscription. Your plan may have expired.",
        )

    # Create a new API key for this org
    result = await storage.create_api_key(label=f"recovered-{found_tier}", project_id=org_id)
    logger.info("Recovered API key for org=%s, tier=%s", org_id, found_tier)
    return {
        "recovered": True,
        "key": result["key"],
        "id": result["id"],
        "label": result["label"],
        "tier": found_tier,
        "organization_id": org_id,
    }
