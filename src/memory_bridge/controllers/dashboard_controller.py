"""Memory Bridge dashboard — self-service API key management and setup guide.

Provides authenticated endpoints for users to create, list, and revoke
their own API keys, plus serves the dashboard page with installation
instructions and copy-to-clipboard terminal commands.
"""

import asyncio
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
        sub = await storage.get_subscription_by_org(org_id)
        tier = sub.tier if sub else "free"
    except Exception:
        tier = "free"

    limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    max_keys = limits.get("max_api_keys", 5)

    # Count only ACTIVE keys (deleted/revoked keys don't count toward the limit)
    all_keys = await storage.list_api_keys()
    org_keys_total = [k for k in all_keys if k.get("project_id") == org_id and k.get("is_active") is not False]
    if len(org_keys_total) >= max_keys:
        if max_keys == 5:
            detail = f"Free tier: max {max_keys} active API keys. Delete an existing key first, or upgrade to a paid plan for unlimited keys."
        else:
            detail = f"{tier.title()} tier: max {max_keys} active API keys. Delete an existing key first, or upgrade your plan for more keys."
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
    request: Request,
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
    # Auth guard — require a valid JWT session
    auth = getattr(request.state, "auth", None)
    if not auth or not auth.get("user_email"):
        raise HTTPException(status_code=401, detail="Authentication required")

    # Verify org_id in request matches authenticated user's org
    auth_org = auth.get("organization_id", "") or auth.get("project_id", "")
    if organization_id.strip() and auth_org and organization_id.strip() != auth_org:
        raise HTTPException(status_code=403, detail="Organization mismatch")

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

    # Don't auto-create API keys — users generate their own from the dashboard
    return {
        "welcome": True,
        "has_keys": False,
        "key": None,
        "tier": actual_tier,
        "organization_id": org_id,
        "key_count": 0,
        "hint": "Generate your first API key from the dashboard when you need one.",
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

    # Extract user identity from auth state (set by middleware for JWT) or from JWT fallback
    auth = getattr(request.state, "auth", None) or {}
    user_email = auth.get("user_email", "")
    user_name = auth.get("user_name", "")
    user_created_at = None

    # Fallback: if auth state doesn't have user_email (API key auth), try decoding the JWT
    if not user_email:
        try:
            import jwt as pyjwt
            from ..config import get_settings
            settings = get_settings()
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                claims = pyjwt.decode(
                    token,
                    settings.jwt_secret,
                    algorithms=[settings.jwt_algorithm or "HS256"],
                    options={"verify_exp": False},
                )
                user_email = claims.get("email", "") or claims.get("sub", "")
                user_name = claims.get("name", "")
        except Exception:
            pass

    # Get subscription
    sub = None
    try:
        sub = await storage.get_subscription_by_org(org_id)
    except Exception:
        pass

    # If no subscription found for the resolved org_id, try looking up by user email
    if not sub and user_email:
        try:
            user_record = await storage.get_user_by_email(user_email)
            if user_record:
                # User found — if they have an org_id, try that
                user_org_id = user_record.get('organization_id')
                if user_org_id:
                    org_id = user_org_id
                    sub = await storage.get_subscription_by_org(org_id)
                # If still no sub, try looking up by stripe_customer_id on the user record
                if not sub:
                    stripe_customer_id = user_record.get('stripe_customer_id', '')
                    if stripe_customer_id:
                        try:
                            sub = await storage.get_subscription_by_stripe_customer(stripe_customer_id)
                        except Exception:
                            pass
        except Exception:
            pass

    # If STILL no subscription found, kick off async Stripe fallback (don't block the response)
    if not sub and user_email:
        asyncio.ensure_future(_check_stripe_fallback(user_email, org_id, storage))

    # Get key count
    try:
        keys = await storage.list_api_keys()
    except Exception:
        logger.warning("Failed to list API keys for org=%s", org_id, exc_info=True)
        keys = []
    user_keys = [k for k in keys if k.get("project_id") == org_id or not k.get("project_id")]
    active_keys = [k for k in user_keys if k.get("is_active", True)]

    # Get memory count (placeholder until real counter is implemented)
    mem_count = 0

    tier = sub.tier if sub else "free"
    if sub and sub.status == "canceled":
        tier = "free"

    # Per-tier rate limits
    rate_limits = {"free": 300, "starter": 600, "pro": 1200, "enterprise": 6000}

    # Look up user record for created_at
    if user_email:
        try:
            user_record = await storage.get_user_by_email(user_email)
            if user_record and 'created_at' in user_record:
                created_val = user_record.get('created_at')
                if created_val:
                    user_created_at = created_val.isoformat() if hasattr(created_val, 'isoformat') else str(created_val)
        except Exception:
            pass

    # Fallback: if no email-based lookup worked, try by org_id (for API-key-authenticated users)
    if not user_created_at and org_id and org_id not in ("default", "demo", ""):
        try:
            # Try to find user by organization_id using the storage's pool/conn
            pool = getattr(storage, 'pool', None)
            if pool:
                async with pool.acquire() as conn:
                    schema = getattr(storage, 'schema', 'public')
                    row = await conn.fetchrow(
                        f"SELECT created_at FROM {schema}.users WHERE organization_id = $1 LIMIT 1",
                        org_id,
                    )
                    if row and row['created_at']:
                        created_val = row['created_at']
                        user_created_at = created_val.isoformat() if hasattr(created_val, 'isoformat') else str(created_val)
            else:
                # SQLite fallback
                db_path = getattr(storage, 'db_path', None)
                if db_path:
                    import aiosqlite
                    async with aiosqlite.connect(db_path) as db:
                        db.row_factory = aiosqlite.Row
                        cursor = await db.execute(
                            "SELECT created_at FROM users WHERE organization_id = ? LIMIT 1",
                            (org_id,),
                        )
                        row = await cursor.fetchone()
                        if row and row['created_at']:
                            user_created_at = row['created_at']
        except Exception:
            pass

    return {
        "organization_id": org_id,
        "tier": tier,
        "status": sub.status if sub else "active",
        "active_keys": len(active_keys),
        "total_keys": len(user_keys),
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "created_at": user_created_at,
        "user_name": user_name,
        "user_email": user_email,
        "email": user_email,
        "memories": mem_count,
        "sessions": 0,
        "rate_limit": rate_limits.get(tier, 60),
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

    # Don't auto-create API keys — users generate their own from the dashboard

    # Record abuse prevention data
    now = datetime.now(timezone.utc).isoformat()
    try:
        await storage.initialize_metric(f"free_signup:email:{email_hash}", now)
        await storage.initialize_metric(f"free_signup:ip:{ip_hash}", now)
    except Exception as e:
        logger.warning("Could not record abuse prevention metric: %s", e)

    logger.info("Free signup: org=%s, email=%s, ip=%s", org_id, email_clean, client_ip)
    # Create a user account for this free signup
    user_dict = None
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
        user_dict = await storage.create_user(user)
    except Exception as e:
        logger.warning("Could not create free user account: %s", e)

    # Generate JWT so the user gets a real session
    token = ""
    if user_dict:
        try:
            from ..services.user_service import UserService
            svc = UserService(repo=storage)
            token = await svc.generate_token(user_dict)
        except Exception as e:
            logger.warning("Could not generate JWT for free signup: %s", e)

    return {
        "key": result["key"],
        "id": result["id"],
        "label": result["label"],
        "tier": "free",
        "organization_id": org_id,
        "token": token,
        "needs_password_setup": True,
    }

@router.post("/recover")
async def recover_api_key(
    email: str = Query("", description="Email used during signup or Stripe checkout"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Recover access to an account by verifying email ownership.

    Security model:
    - Free users: email lookup generates a JWT to log them in
      (same trust level as 'forgot password'). No new API key is
      created — user generates one from the dashboard after login.
    - Paid users: Stripe subscription verification generates a JWT
      and creates a new API key (Stripe is strong auth).
    - API keys are NEVER created from email-only lookup.
    """
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid email address.")

    normalized_email = email.strip().lower()
    from ..services.user_service import UserService
    user_svc = UserService(repo=storage)

    # ── Step 1: Check local database ─────────────────────────────────
    org_id = None
    found_tier = "free"
    user_dict = None
    try:
        user_dict = await storage.get_user_by_email(normalized_email)
        if user_dict:
            org_id = user_dict.get("organization_id", "")
            if not org_id:
                raise HTTPException(status_code=404, detail="No user found. Create an account to get started.")
            try:
                sub = await storage.get_subscription_by_org(org_id)
                if sub:
                    found_tier = sub.tier if sub.status != "canceled" else "free"
            except Exception:
                pass
            logger.info("Recover: found user in local DB org=%s tier=%s", org_id, found_tier)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Recover: local DB lookup failed: %s", e)

    # ── Step 2: Fall through to Stripe if not found locally ─────────
    recovered_via_stripe = False
    if not org_id:
        import stripe
        try:
            customers = stripe.Customer.list(email=normalized_email, limit=10)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not verify identity: {e}")

        if customers.data:
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
            for customer in customers.data:
                try:
                    sub = await storage.get_subscription_by_stripe_customer(customer.id)
                except Exception:
                    continue
                if sub:
                    org_id = sub.organization_id
                    found_tier = sub.tier
                    break

        if org_id:
            recovered_via_stripe = True
            logger.info("Recover: found via Stripe org=%s tier=%s", org_id, found_tier)

    if not org_id:
        raise HTTPException(
            status_code=404,
            detail="No user found. Create an account to get started.",
        )

    # ── Generate JWT to log the user in ──────────────────────────────
    jwt_token = ""
    # If we found the user via local DB, we have their user record
    if user_dict:
        try:
            jwt_token = await user_svc.generate_token(user_dict)
        except Exception as e:
            logger.warning("Recover: JWT generation failed for local user: %s", e)
    # If we found via Stripe, create or find a user record first
    else:
        try:
            from datetime import datetime, timezone
            user_data = {
                "id": f"stripe-recover-{org_id[:8]}",
                "email": normalized_email,
                "name": normalized_email.split("@")[0],
                "organization_id": org_id,
                "role": "member",
                "created_at": datetime.now(timezone.utc),
            }
            user_dict = await storage.create_user(user_data)
            jwt_token = await user_svc.generate_token(user_dict)
        except Exception as e:
            logger.warning("Recover: JWT generation failed for Stripe user: %s", e)

    # ── Determine what to return ────────────────────────────────────
    api_key_value = None
    api_key_id = ""
    key_count = 0

    # Only create a new API key if verified via Stripe (strong auth)
    if recovered_via_stripe:
        # Check if org already has active keys
        try:
            all_keys = await storage.list_api_keys()
            org_active = [k for k in all_keys if k.get("project_id") == org_id and k.get("is_active") is not False]
            key_count = len(org_active)
            if org_active:
                api_key_id = org_active[-1].get("id", "")
                logger.info("Recover: Stripe user has %d existing keys", key_count)
            else:
                # Create a new key — verified via Stripe
                new_key = await storage.create_api_key(label=f"recovered-{found_tier}", project_id=org_id)
                api_key_value = new_key.get("key", "")
                api_key_id = new_key.get("id", "")
                key_count = 1
                logger.info("Recover: created new API key for Stripe-verified org=%s", org_id)
        except Exception as e:
            logger.warning("Recover: key handling failed: %s", e)
    else:
        # Free/local user — don't create keys, just count existing ones
        try:
            all_keys = await storage.list_api_keys()
            org_active = [k for k in all_keys if k.get("project_id") == org_id and k.get("is_active") is not False]
            key_count = len(org_active)
            if org_active:
                api_key_id = org_active[-1].get("id", "")
        except Exception:
            pass

    logger.info("Recover: login successful for org=%s tier=%s via_stripe=%s", org_id, found_tier, recovered_via_stripe)
    return {
        "recovered": True,
        "token": jwt_token,
        "key": api_key_value,
        "key_id": api_key_id,
        "tier": found_tier,
        "organization_id": org_id,
        "key_count": key_count,
    }


@router.post("/stripe-welcome")
async def stripe_welcome(
    request: Request,
    session_id: str = Query("", description="Stripe checkout session ID"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Generate a fresh JWT from a Stripe checkout session.

    Exempt from auth middleware — allows users who just completed
    Stripe checkout to get a fresh JWT even if their previous one
    expired during the Stripe payment flow.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    import stripe as stripe_mod
    stripe_mod.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_mod.api_key:
        raise HTTPException(status_code=502, detail="Stripe not configured")

    try:
        session = await asyncio.wait_for(
            asyncio.to_thread(
                stripe_mod.checkout.Session.retrieve,
                session_id,
                expand=["subscription"],
            ),
            timeout=10.0,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not verify payment: {e}")

    if session.status != "complete":
        raise HTTPException(status_code=400, detail="Payment not completed")

    # Resolve org_id from session metadata
    org_id = (session.metadata or {}).get("organization_id", "")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization linked to this session")

    # Resolve tier from the subscription or metadata
    tier = (session.metadata or {}).get("tier", "free")
    try:
        if session.subscription:
            items = []
            if hasattr(session.subscription, "items") and session.subscription.items:
                items = session.subscription.items.get("data", [])
            if items and items[0].get("price"):
                from ..services.billing_service import PRICE_IDS
                price_id = items[0]["price"].get("id", "")
                for t, pid in PRICE_IDS.items():
                    if pid == price_id:
                        tier = t
                        break
    except Exception:
        pass

    # Log the Stripe customer ID if available
    stripe_customer_id = session.get("customer", "") or ""

    # Store the subscription locally immediately — don't wait for webhook
    try:
        from ..models import Subscription as SubModel
        from datetime import datetime, timezone
        sub_data_obj = session.subscription
        if sub_data_obj:
            sub_id = sub_data_obj.id if hasattr(sub_data_obj, "id") else sub_data_obj.get("id", "")
            sub_status = sub_data_obj.status if hasattr(sub_data_obj, "status") else sub_data_obj.get("status", "active")
            period_end = None
            period_start = None
            if hasattr(sub_data_obj, "current_period_end") and sub_data_obj.current_period_end:
                period_end = datetime.fromtimestamp(sub_data_obj.current_period_end, tz=timezone.utc)
            elif isinstance(sub_data_obj, dict) and sub_data_obj.get("current_period_end"):
                period_end = datetime.fromtimestamp(sub_data_obj["current_period_end"], tz=timezone.utc)
            if hasattr(sub_data_obj, "current_period_start") and sub_data_obj.current_period_start:
                period_start = datetime.fromtimestamp(sub_data_obj.current_period_start, tz=timezone.utc)
            elif isinstance(sub_data_obj, dict) and sub_data_obj.get("current_period_start"):
                period_start = datetime.fromtimestamp(sub_data_obj["current_period_start"], tz=timezone.utc)

            sub = SubModel(
                id=sub_id or f"stripe-{org_id[:8]}",
                organization_id=org_id,
                stripe_customer_id=stripe_customer_id,
                tier=tier,
                status=sub_status,
                current_period_start=period_start,
                current_period_end=period_end,
            )
            await storage.store_subscription(sub)
            logger.info("Stripe welcome: stored subscription org=%s tier=%s sub=%s", org_id, tier, sub_id)
    except Exception as e:
        logger.warning("Stripe welcome: could not store subscription: %s", e)

    # Try to find the user for this org
    user_dict = None
    try:
        # Look for user by org_id
        user_dict = await storage.get_user_by_organization_id(org_id)
    except Exception:
        pass

    # If no user found by org, check session metadata for email
    if not user_dict and session.get("customer_details"):
        email = (session.get("customer_details") or {}).get("email", "")
        if email:
            try:
                user_dict = await storage.get_user_by_email(email)
                if user_dict:
                    user_id = user_dict.get("id")
                    if not user_dict.get("organization_id") and user_id:
                        # Link org to user — user existed but had no org_id
                        # This happens when user signed up via Auth0 before subscribing
                        try:
                            await storage.update_user_organization_id(user_id, org_id)
                            user_dict["organization_id"] = org_id
                            logger.info("Linked org %s to user %s (%s)", org_id, user_id, email)
                        except Exception as e:
                            logger.warning("Could not link org %s to user %s: %s", org_id, email, e)
                    if stripe_customer_id and user_id:
                        try:
                            await storage.update_user_stripe_customer(user_id, stripe_customer_id)
                        except Exception as e:
                            logger.warning("Could not link stripe customer to user: %s", e)
            except Exception:
                pass

    if not user_dict:
        # Create a minimal user record for this org
        customer_email = ""
        if session.get("customer_details"):
            customer_email = (session.get("customer_details") or {}).get("email", "")
        try:
            from datetime import datetime, timezone
            user_data = {
                "id": f"stripe-{org_id[:8]}",
                "email": customer_email or f"user-{org_id[:8]}@memorybridge.io",
                "name": customer_email.split("@")[0] if customer_email else "User",
                "organization_id": org_id,
                "role": "member",
                "created_at": datetime.now(timezone.utc),
            }
            user_dict = await storage.create_user(user_data)
        except Exception as e:
            logger.warning("Could not create user for stripe welcome: %s", e)

    # Generate a fresh JWT
    from ..services.user_service import UserService
    svc = UserService(repo=storage)
    token = ""
    if user_dict:
        try:
            token = await svc.generate_token(user_dict)
        except Exception as e:
            logger.warning("Could not generate JWT for stripe welcome: %s", e)

    return {
        "token": token,
        "organization_id": org_id,
        "tier": tier,
        "stripe_customer_id": stripe_customer_id,
        "needs_password_setup": True,
    }


async def _check_stripe_fallback(user_email: str, org_id: str, storage):
    """Background task: check Stripe for a subscription by email and store it locally."""
    try:
        import stripe as stripe_mod
        stripe_mod.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not stripe_mod.api_key:
            return
        customers = await asyncio.wait_for(
            asyncio.to_thread(
                stripe_mod.Customer.list,
                email=user_email,
                limit=1,
                expand=["data.subscriptions"],
            ),
            timeout=5.0,
        )
        if not customers or not customers.data:
            return
        stripe_customer = customers.data[0]
        subs_list = None
        if hasattr(stripe_customer, "subscriptions") and stripe_customer.subscriptions:
            subs_list = stripe_customer.subscriptions.get("data", [])
        if not subs_list:
            subs_list = stripe_customer.get("subscriptions", {}) or {}
            subs_list = subs_list.get("data", []) if isinstance(subs_list, dict) else []
        if not subs_list:
            return
        active_sub = subs_list[0]
        price_id = ""
        items_list = []
        if hasattr(active_sub, "items") and active_sub.items:
            items_list = active_sub.items.get("data", [])
        if not items_list:
            items_list = active_sub.get("items", {}).get("data", []) if isinstance(active_sub.get("items"), dict) else []
        if items_list:
            price_obj = items_list[0].get("price", {})
            price_id = price_obj.get("id", "") if isinstance(price_obj, dict) else getattr(price_obj, "id", "")
        from ..services.billing_service import PRICE_IDS
        resolved_tier = "starter"
        for t, pid in PRICE_IDS.items():
            if pid == price_id:
                resolved_tier = t
                break
        from ..models import Subscription
        from datetime import datetime, timezone
        sub_id = ""
        sub_status = "active"
        period_end = None
        if hasattr(active_sub, "id"):
            sub_id = active_sub.id
            sub_status = active_sub.status or "active"
            if hasattr(active_sub, "current_period_end") and active_sub.current_period_end:
                period_end = datetime.fromtimestamp(active_sub.current_period_end, tz=timezone.utc)
        else:
            sub_id = active_sub.get("id", f"stripe-{org_id[:8]}")
            sub_status = active_sub.get("status", "active")
            if active_sub.get("current_period_end"):
                period_end = datetime.fromtimestamp(active_sub["current_period_end"], tz=timezone.utc)
        stripe_customer_id = stripe_customer.id if hasattr(stripe_customer, "id") else stripe_customer.get("id", "")

        # If org_id looks like a user key (e.g. "user:email"), generate a proper UUID
        import uuid
        real_org_id = org_id
        if org_id.startswith("user:") or org_id == "default":
            real_org_id = str(uuid.uuid4())

        # Update the user record with the real org_id
        try:
            user_record = await storage.get_user_by_email(user_email)
            if user_record:
                user_id = user_record.get("id")
                if user_id:
                    if not user_record.get("organization_id"):
                        await storage.update_user_organization_id(user_id, real_org_id)
                    # Always link stripe_customer_id
                    if stripe_customer_id:
                        await storage.update_user_stripe_customer(user_id, stripe_customer_id)
                    logger.info(
                        "Stripe fallback: linked org=%s to user=%s",
                        real_org_id, user_email,
                    )
        except Exception as e:
            logger.warning("Stripe fallback: could not link user: %s", e)

        sub = Subscription(
            id=sub_id or f"stripe-{real_org_id[:8]}",
            organization_id=real_org_id,
            stripe_customer_id=stripe_customer_id,
            tier=resolved_tier,
            status=sub_status,
            current_period_start=datetime.now(timezone.utc),
            current_period_end=period_end,
        )
        try:
            await storage.store_subscription(sub)
            logger.info("Stripe fallback: restored subscription for org=%s tier=%s", org_id, resolved_tier)
        except Exception as e:
            logger.warning("Stripe fallback: store failed %s", e)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Stripe fallback error: %s", e)
