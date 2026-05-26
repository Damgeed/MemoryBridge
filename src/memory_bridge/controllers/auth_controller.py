"""Authentication endpoints for user registration and login."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, EmailStr

from ..services.user_service import UserService
from ..dependencies import get_storage
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


async def get_user_service():
    repo = await get_storage()
    return UserService(repo=repo)


@router.post("/register", response_model=AuthResponse)
async def register(
    req: RegisterRequest,
    service: UserService = Depends(get_user_service),
):
    """Register a new user account and generate a JWT."""
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    try:
        user = await service.register(
            email=req.email,
            password=req.password,
            name=req.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Create free subscription so the user's dashboard can load
    try:
        from ..models import Subscription
        storage = await get_storage()
        org_id = user.get("organization_id", "")
        if org_id:
            sub = Subscription(id=f"free-{org_id[:8]}", organization_id=org_id, tier="free", status="active")
            await storage.store_subscription(sub)
    except Exception as e:
        logger.warning("Could not create subscription for new user: %s", e)

    token = await service.generate_token(user)

    return AuthResponse(
        token=token,
        user={k: v for k, v in user.items() if k != "password_hash"},
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    req: LoginRequest,
    service: UserService = Depends(get_user_service),
):
    """Authenticate and return a JWT token."""
    user = await service.authenticate(email=req.email, password=req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = await service.generate_token(user)

    return AuthResponse(
        token=token,
        user={k: v for k, v in user.items() if k != "password_hash"},
    )


@router.get("/my-key")
async def get_my_api_key(request: Request):
    """Return the first active API key for the authenticated user's org.

    Requires a valid JWT in the Authorization header.
    The returned key is the same one used to authenticate Memory Bridge API calls.
    """
    from ..dependencies import get_storage
    from ..repository import MemoryRepository

    # Reuse existing JWT auth from auth.py middleware
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Resolve org_id: prefer project_id, fall back to key_id
    org_id = auth.get("project_id") or auth.get("key_id", "")
    if not org_id or org_id in ("default", "demo", ""):
        raise HTTPException(status_code=401, detail="Could not resolve your account. Please sign in again.")

    storage = await get_storage()
    all_keys = await storage.list_api_keys()
    user_keys = [k for k in all_keys if k.get("project_id") == org_id and k.get("is_active") is not False]

    if not user_keys:
        raise HTTPException(status_code=404, detail="No API keys found for your account. Use the dashboard to generate one.")

    return {"key_id": user_keys[0]["id"], "key_count": len(user_keys)}


@router.get("/my-key-value")
async def get_my_api_key_value(request: Request):
    """Return the raw API key value for the authenticated user's org.

    Requires a valid JWT in the Authorization header.
    This returns the actual key string used to authenticate Memory Bridge API calls.
    """
    from ..dependencies import get_storage

    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    storage = await get_storage()

    # Resolve org_id: prefer project_id from JWT/auth, fall back to looking up by email
    org_id = auth.get("project_id", "")
    if not org_id or org_id in ("default", "demo", ""):
        # Try to get user's real org_id from the database using auth info
        user_email = auth.get("user_email", "")
        if not user_email:
            # Try decoding the JWT from the Authorization header
            try:
                import jwt as pyjwt
                from ..config import get_settings
                settings = get_settings()
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    claims = pyjwt.decode(
                        token, settings.jwt_secret,
                        algorithms=[settings.jwt_algorithm or "HS256"],
                        options={"verify_exp": False},
                    )
                    user_email = claims.get("email", "") or claims.get("sub", "")
            except Exception:
                pass
        if user_email:
            try:
                user_record = await storage.get_user_by_email(user_email)
                if user_record:
                    org_id = user_record.get("organization_id", "") or org_id
            except Exception:
                pass

    if not org_id or org_id in ("default", "demo", ""):
        # Last resort: use key_id (may not match DB org_id, but better than nothing)
        org_id = auth.get("key_id", "")
        if not org_id or org_id in ("default", "demo", ""):
            raise HTTPException(status_code=401, detail="Could not resolve your account. Please sign in again.")

    all_keys = await storage.list_api_keys()
    user_keys = [k for k in all_keys if k.get("project_id") == org_id and k.get("is_active") is not False]

    if not user_keys:
        # No keys yet — user needs to generate one from the dashboard
        return {"key": None, "key_id": "", "key_count": 0, "new": False, "hint": "Generate your first API key from the dashboard."}

    # Return the most recent active key — never create a duplicate
    # list_api_keys returns hashes, not plaintext, which is by design.
    # The user can see/manage full keys from the dashboard.
    latest = user_keys[-1]
    return {
        "key": None,
        "key_id": latest.get("id", ""),
        "label": latest.get("label", ""),
        "key_count": len(user_keys),
        "hint": "View and manage your API keys in the dashboard.",
    }


@router.post("/oauth")
async def oauth_login(
    req: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Authenticate via OAuth provider token.

    Accepts a JSON body with:
    - provider: "google" | "apple" | "microsoft"
    - token: The ID token from the OAuth provider
    - email: (optional) email from client
    - name: (optional) display name from client

    Returns JWT + user info (same response as /auth/login).
    """
    from pydantic import BaseModel

    class OAuthRequest(BaseModel):
        provider: str
        token: str
        email: str = ""
        name: str = ""

    body = await req.json()
    oauth_req = OAuthRequest(**body)

    from ..services.oauth_service import OAuthService
    from ..dependencies import get_storage

    repo = await get_storage()
    oauth_service = OAuthService(repo=repo)

    try:
        user = await oauth_service.authenticate(
            provider=oauth_req.provider,
            token=oauth_req.token,
            email=oauth_req.email,
            name=oauth_req.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    from ..services.user_service import UserService
    user_service = UserService(repo=repo)
    token = await user_service.generate_token(user)

    return {
        "token": token,
        "user": {k: v for k, v in user.items() if k != "password_hash"},
    }


class RefreshRequest(BaseModel):
    token: str


@router.post("/refresh")
async def refresh_token(
    req: RefreshRequest,
    service: UserService = Depends(get_user_service),
):
    """Exchange an expiring JWT for a fresh one.

    The current token must still be valid (within its expiry window).
    Returns a new JWT with a fresh expiry or 401 if the token is
    too old or invalid.
    """
    from ..services.user_service import UserService as US
    new_token = await service.refresh_token(req.token)
    if not new_token:
        raise HTTPException(status_code=401, detail="Token expired. Please sign in again.")
    return {"token": new_token}


@router.get("/me")
async def get_me(request: Request):
    """Return basic info about the currently authenticated user.

    Requires a valid JWT in the Authorization header. Lightweight
    endpoint — no DB queries, just decodes the existing JWT.
    """
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "email": auth.get("user_email", ""),
        "name": auth.get("user_name", ""),
        "organization_id": auth.get("project_id", ""),
        "role": auth.get("role", "member"),
    }


class LinkSubscriptionRequest(BaseModel):
    pending_org_id: str


@router.post("/link-subscription")
async def link_subscription(
    req: LinkSubscriptionRequest,
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Link a pending (pre-registration) subscription to the user's account.

    When a user subscribes before registering, the Stripe webhook stores
    the subscription under a temporary 'pending-*' org_id. After the user
    registers, this endpoint transfers that subscription to their real
    org_id so they don't lose paid access.
    """
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    real_org_id = auth.get("project_id", "")
    if not real_org_id or real_org_id in ("default", "demo", ""):
        raise HTTPException(status_code=401, detail="Could not resolve your account. Please sign in again.")

    pending_org_id = req.pending_org_id
    if not pending_org_id.startswith("pending-"):
        raise HTTPException(status_code=400, detail="Invalid pending subscription token")

    # Look up the subscription stored under the pending org_id
    pending_sub = await storage.get_subscription_by_org(pending_org_id)
    if not pending_sub:
        return {"status": "not_found", "message": "No pending subscription found. Payment may still be processing."}

    # Transfer the subscription to the user's real org
    pending_sub.organization_id = real_org_id
    from datetime import timezone
    pending_sub.updated_at = datetime.now(timezone.utc)
    await storage.store_subscription(pending_sub)

    # Clean up the old record still referencing pending org_id
    # (store_subscription upserts by id, so the org_id is now updated)

    logger.info(
        "Transferred pending subscription %s from %s to %s",
        pending_sub.id, pending_org_id, real_org_id,
    )
    return {
        "status": "linked",
        "tier": pending_sub.tier,
        "subscription_id": pending_sub.id,
    }
