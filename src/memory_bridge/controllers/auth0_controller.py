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
from starlette.responses import JSONResponse

from ..config import get_settings
from ..dependencies import get_storage
from ..models import User
from ..repository import MemoryRepository
from ..services.auth0_service import get_auth0_service
from ..services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/auth0", tags=["auth0"])


def _get_user_service(storage: MemoryRepository = Depends(get_storage)) -> UserService:
    return UserService(repo=storage)


@router.get("/login")
async def auth0_login(request: Request):
    """Redirect user to Auth0's Universal Login page."""
    svc = get_auth0_service()
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Auth0 is not configured")

    # Build the redirect URI — the URL Auth0 sends the user back to
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/auth0/callback"
    state = str(uuid.uuid4())[:8]  # Simple CSRF token

    authorize_url = svc._authorize_url(redirect_uri, state=state)
    logger.info("Auth0 login redirect: %s", authorize_url)
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

    base = str(request.base_url).rstrip("/")
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

    # Generate our own JWT for the app
    user_data = {
        "id": user_id if existing_user else (result.get("id", "") if not existing_user else ""),
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
