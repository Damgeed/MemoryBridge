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


class CheckEmailRequest(BaseModel):
    email: str


async def get_user_service():
    repo = await get_storage()
    return UserService(repo=repo)


@router.post("/check-email")
async def check_email(
    req: CheckEmailRequest,
    storage: MemoryRepository = Depends(get_storage),
):
    """Check if an email already has a user account."""
    email_clean = req.email.strip().lower()
    user = await storage.get_user_by_email(email_clean)
    return {"exists": user is not None}


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
        # No keys yet — auto-create one for the user
        try:
            new_key = await storage.create_api_key(
                label="auto",
                project_id=org_id,
            )
            logger.info("Auto-created API key for org=%s", org_id)
            return {
                "key": new_key.get("key", ""),
                "key_id": new_key.get("id", ""),
                "label": new_key.get("label", "auto"),
                "key_count": 1,
                "new": True,
                "hint": "This is your new API key. Keep it safe!",
            }
        except Exception as e:
            logger.warning("Auto-create API key failed for org=%s: %s", org_id, e)
            return {"key": None, "key_id": "", "key_count": 0, "new": False, "hint": "Could not create API key. Try again from the dashboard."}

    # Return the most recent active key — never create a duplicate
    # list_api_keys returns hashes, not plaintext, which is by design.
    # Return the most recent active key — never create a duplicate
    latest = user_keys[-1]
    return {
        "key": None,
        "key_id": latest.get("id", ""),
        "label": latest.get("label", ""),
        "key_count": len(user_keys),
        "has_keys": True,
        "hint": "You already have active API keys. Manage them from the dashboard.",
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


class SetPasswordRequest(BaseModel):
    password: str


@router.post("/set-password")
async def set_password(
    req: SetPasswordRequest,
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Set a password on an account that was created without one (e.g. free sign-up).

    Requires a valid JWT. The authenticated user's email is used to
    find the user record, then the password is bcrypt-hashed and saved.
    After this, the user can sign in via /auth/login normally.
    """
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    email = auth.get("user_email", "")
    if not email:
        raise HTTPException(status_code=401, detail="Could not identify your account. Please sign in again.")

    import bcrypt
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    try:
        import aiosqlite
        db_path = getattr(storage, 'db_path', None)
        if db_path:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("UPDATE users SET password_hash = ? WHERE email = ?", (password_hash, email))
                await db.commit()
        else:
            conn = getattr(storage, 'pool', None)
            if conn:
                async with conn.acquire() as c:
                    await c.execute("UPDATE users SET password_hash = $1 WHERE email = $2", password_hash, email)
            else:
                raise RuntimeError("No database backend available")
    except Exception as e:
        logger.error("Failed to set password for %s: %s", email, e)
        raise HTTPException(status_code=500, detail="Could not save password. Try again later.")

    return {"status": "ok", "message": "Password set successfully. You can now sign in with email + password."}