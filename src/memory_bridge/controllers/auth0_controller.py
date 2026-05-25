"""Auth0 authentication controller.

Handles Auth0 Universal Login flow:
1. GET /auth/auth0/login — redirects user to Auth0's hosted login page
2. GET /auth/auth0/callback — Auth0 redirects here after login, exchanges code for tokens
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..config import get_settings
from ..dependencies import get_storage
from ..models import User
from ..repository import MemoryRepository
from ..services.auth0_service import get_auth0_service
from ..services.user_service import UserService

logger = logging.getLogger(__name__)


def _get_base_url(request: Request) -> str:
    """Get the base URL of the application, respecting reverse proxy headers.

    Order of precedence:
    1. APP_URL environment variable (best for Railway/deploy)
    2. X-Forwarded-Proto + Host headers (reverse proxy)
    3. request.base_url (fallback, may be wrong behind proxy)
    """
    import os
    env_url = os.environ.get("APP_URL", "").rstrip("/")
    if env_url:
        return env_url

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    host = request.headers.get("X-Forwarded-Host", "") or request.headers.get("Host", "")
    if forwarded_proto and host:
        return f"{forwarded_proto}://{host}"

    return str(request.base_url).rstrip("/")


router = APIRouter(prefix="/auth/auth0", tags=["auth0"])


def _get_user_service(storage: MemoryRepository = Depends(get_storage)) -> UserService:
    return UserService(repo=storage)


@router.get("/login")
async def auth0_login(
    request: Request,
    connection: str = Query("", description="Specific social connection (e.g. google-oauth2, apple, windowslive)"),
):
    """Redirect user to Auth0's Universal Login page.

    If a connection is specified, Auth0 skips the login page and
    goes directly to that provider's authentication flow.
    """
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    # Build the redirect URI — the URL Auth0 sends the user back to
    base = _get_base_url(request)
    redirect_uri = f"{base}/auth/auth0/callback"
    state = str(uuid.uuid4())[:8]  # Simple CSRF token

    authorize_url = svc._authorize_url(redirect_uri, state=state, connection=connection)
    logger.info("Auth0 login redirect: %s (connection=%s)", authorize_url, connection or "default")
    return RedirectResponse(url=authorize_url)


@router.get("/callback")
async def auth0_callback(
    request: Request,
    code: str = Query("", description="Authorization code from Auth0"),
    state: str = Query("", description="State parameter (CSRF)"),
    error: str = Query("", description="Error from Auth0 if login failed"),
    storage: MemoryRepository = Depends(get_storage),
    user_svc: UserService = Depends(_get_user_service),
):
    """Handle Auth0 login callback.

    Auth0 redirects here after successful authentication.
    Exchanges the authorization code for tokens, validates the ID token,
    and creates/updates a local user record.
    """
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    # Handle Auth0 errors (user cancelled, etc.)
    if error:
        # Redirect to home with error
        return RedirectResponse(url=f"/?auth_error={error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    base = _get_base_url(request)
    redirect_uri = f"{base}/auth/auth0/callback"

    # Exchange code for tokens
    tokens = await svc.exchange_code(code, redirect_uri)
    if not tokens:
        raise HTTPException(status_code=400, detail="Failed to exchange authorization code")

    id_token = tokens.get("id_token", "")
    access_token = tokens.get("access_token", "")

    # Validate the ID token (contains user profile)
    claims = await svc.validate_token(id_token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid ID token from Auth0")

    # Extract user info from claims
    auth0_sub = claims.get("sub", "")
    email = claims.get("email", "") or claims.get("name", "")
    name = claims.get("name", "") or claims.get("nickname", "") or email.split("@")[0]

    # Fallback for phone-only users (shouldn't happen in social login, but be safe)
    if not email and auth0_sub:
        import hashlib
        sub_slug = hashlib.md5(auth0_sub.encode()).hexdigest()[:12]
        email = f"auth0_{sub_slug}@social.auth0.local"
        name = name or "Auth0 User"

    if not auth0_sub:
        raise HTTPException(status_code=400, detail="Auth0 response missing user identifier")

    # Find or create user by email (no schema migration needed)
    existing_user = None
    if email:
        try:
            existing_user = await storage.get_user_by_email(email)
        except Exception:
            pass

    if existing_user:
        user_id = existing_user.get("id")
        org_id = existing_user.get("organization_id", "")
        logger.info("Auth0 login: existing user %s (org=%s)", email, org_id)
    else:
        # Create new user
        org_id = str(uuid.uuid4())
        try:
            user = User(
                email=email,
                password_hash="",  # Auth0 manages passwords
                name=name,
                organization_id=org_id,
                auth0_sub=auth0_sub,
            )
            result = await storage.create_user(user)
            user_id = result.get("id", "")
            logger.info("Auth0 signup: new user %s (org=%s)", email, org_id)
        except Exception as e:
            logger.error("Auth0 user creation failed: %s", e)
            raise HTTPException(status_code=500, detail="Could not create user account")

        # Create free subscription + API key for new user so dashboard can load
        try:
            from ..models import Subscription
            sub = Subscription(id=f"free-{org_id[:8]}", organization_id=org_id, tier="free", status="active")
            await storage.store_subscription(sub)
        except Exception as e:
            logger.warning("Could not create subscription for new user: %s", e)
        try:
            from datetime import datetime, timezone
            key_label = f"auth0-key-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
            await storage.create_api_key(label=key_label, project_id=org_id)
        except Exception as e:
            logger.warning("Could not create API key for new user: %s", e)

    # Generate our own JWT for the app
    user_data = {
        "id": user_id,
        "email": email,
        "name": name,
        "organization_id": org_id,
        "role": "member",
    }
    try:
        jwt_token = await user_svc.generate_token(user_data)
    except Exception as e:
        logger.error("JWT generation failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not generate session token")

    # Redirect to dashboard with token as query param (frontend stores it)
    redirect_url = f"/dashboard?jwt={jwt_token}&org_id={org_id}"
    return RedirectResponse(url=redirect_url)


# ── Passwordless (Email OTP) ──────────────────────────────────────


class PasswordlessStartRequest(BaseModel):
    email: str


class PasswordlessVerifyRequest(BaseModel):
    email: str = ""
    phone: str = ""  # for SMS OTP
    code: str


@router.post("/passwordless/start")
async def passwordless_start(req: PasswordlessStartRequest):
    """Send a 6-digit verification code to the user's email via Auth0 Passwordless.

    This is the first step of the passwordless flow (like ChatGPT's email login).
    Auth0 sends an email with a 6-digit code.
    """
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    success = await svc.start_passwordless(req.email)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to send verification code. Make sure Auth0 Passwordless (Email) is enabled in Auth0 Dashboard.")

    return {"sent": True, "email": mask_email(req.email)}


def mask_email(email: str) -> str:
    """Show just the first character and domain: 'j***@example.com'"""
    at_idx = email.find("@")
    if at_idx < 1:
        return email
    return email[0] + "***" + email[at_idx:]


@router.post("/passwordless/verify")
async def passwordless_verify(
    req: PasswordlessVerifyRequest,
    storage: MemoryRepository = Depends(get_storage),
    user_svc: UserService = Depends(_get_user_service),
):
    """Verify a 6-digit OTP code and return a session JWT.

    Exchanges the code for Auth0 tokens, validates the ID token,
    creates or finds the local user, and returns our JWT.
    """
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    if not req.code or len(req.code) < 4:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Exchange code for Auth0 tokens
    is_phone = bool(req.phone)
    realm = "sms" if is_phone else "email"
    username = req.phone if is_phone else req.email
    tokens = await svc.verify_passwordless(username, req.code, realm)
    if not tokens:
        raise HTTPException(status_code=401, detail="Invalid or expired verification code")
    
    # Check if Auth0 returned an error (non-standard dict with _http_status)
    if tokens.get("_http_status"):
        error_desc = tokens.get("error_description", str(tokens))
        logger.warning("Auth0 OTP verify failed: %s", error_desc)
        raise HTTPException(
            status_code=401,
            detail=f"Auth0 error: {error_desc[:300]}"
        )

    id_token = tokens.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="No ID token received from Auth0")

    # Validate the ID token
    claims = await svc.validate_token(id_token)
    if not claims:
        raise HTTPException(status_code=401, detail="Failed to validate Auth0 token")

    # Extract user info
    auth0_sub = claims.get("sub", "")
    email = claims.get("email", "") or req.email
    name = claims.get("name", "") or claims.get("nickname", "") or email.split("@")[0]

    # For phone-only users (SMS login), Auth0 doesn't return an email.
    # Use a placeholder so the database constraint doesn't fail.
    if not email and auth0_sub:
        # Generate a deterministic placeholder: sms_<sub_hash>@phone.auth0.local
        import hashlib
        sub_slug = hashlib.md5(auth0_sub.encode()).hexdigest()[:12]
        email = f"sms_{sub_slug}@phone.auth0.local"
        name = name or f"User {req.phone[-8:]}" if req.phone else "SMS User"

    if not auth0_sub:
        raise HTTPException(status_code=400, detail="Auth0 response missing user identifier")

    # Find or create user
    existing_user = None
    if email:
        try:
            existing_user = await storage.get_user_by_email(email)
        except Exception:
            pass

    if existing_user:
        user_id = existing_user.get("id")
        org_id = existing_user.get("organization_id", "")
        logger.info("Passwordless login: existing user %s (org=%s)", email, org_id)
    else:
        org_id = str(uuid.uuid4())
        try:
            user = User(
                email=email,
                password_hash="",  # Auth0 manages auth
                name=name,
                organization_id=org_id,
                auth0_sub=auth0_sub,
            )
            result = await storage.create_user(user)
            user_id = result.get("id", "")
            logger.info("Passwordless signup: new user %s (org=%s)", email, org_id)
        except Exception as e:
            logger.error("Passwordless user creation failed: %s", e)
            raise HTTPException(status_code=500, detail="Could not create user account")

        # Create free subscription + API key for new user so dashboard can load
        try:
            from ..models import Subscription
            sub = Subscription(id=f"free-{org_id[:8]}", organization_id=org_id, tier="free", status="active")
            await storage.store_subscription(sub)
        except Exception as e:
            logger.warning("Could not create subscription for new user: %s", e)
        try:
            from datetime import datetime, timezone
            key_label = f"auth0-key-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
            await storage.create_api_key(label=key_label, project_id=org_id)
        except Exception as e:
            logger.warning("Could not create API key for new user: %s", e)

    # Generate our JWT
    user_data = {
        "id": user_id,
        "email": email,
        "name": name,
        "organization_id": org_id,
        "role": "member",
    }

    try:
        jwt_token = await user_svc.generate_token(user_data)
    except Exception as e:
        logger.error("JWT generation failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not generate session token")

    # Fetch API key for the user
    api_key = ""
    try:
        keys = await storage.list_api_keys(org_id)
        if keys:
            api_key = keys[0].get("key", "")
    except Exception:
        pass

    return {
        "token": jwt_token,
        "api_key": api_key,
        "user": {
            "id": user_data["id"],
            "email": email,
            "name": name,
            "organization_id": org_id,
        },
    }


# ── Phone (SMS OTP) ──────────────────────────────────────────────


class PhoneStartRequest(BaseModel):
    phone: str


@router.post("/passwordless/start-sms")
async def passwordless_start_sms(req: PhoneStartRequest):
    """Send a 6-digit verification code to the user's phone via Auth0 Passwordless SMS.

    Requires Auth0 Passwordless (SMS) connection with Twilio configured.
    """
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    # Normalize phone number
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    success = await svc.start_passwordless_sms(phone)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Failed to send SMS code. Make sure Auth0 Passwordless (SMS) is enabled and Twilio is configured in Auth0 Dashboard.",
        )

    return {"sent": True, "phone": mask_phone(phone)}


def mask_phone(phone: str) -> str:
    """Show last 4 digits only: '****-***-1234'"""
    clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if len(clean) <= 4:
        return phone
    return "*******" + clean[-4:]
