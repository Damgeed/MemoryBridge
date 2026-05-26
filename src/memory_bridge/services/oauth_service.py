"""OAuth authentication service for Google, Apple, and Microsoft.

Validates third-party identity tokens and links them to Memory Bridge user accounts.
Each provider requires a corresponding CLIENT_ID env var to be set.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from jose import jwk, jwt as jose_jwt
from jose.utils import base64url_decode

from ..config import get_settings
from ..models import User

logger = logging.getLogger(__name__)


class OAuthService:
    """Validate OAuth tokens from Google, Apple, and Microsoft."""

    def __init__(self, repo=None):
        self.repo = repo
        self.settings = get_settings()

    async def authenticate(self, provider: str, token: str, email: str = "", name: str = "") -> dict:
        """Authenticate a user via OAuth token.

        Args:
            provider: 'google', 'apple', or 'microsoft'
            token: The OAuth ID token from the provider
            email: User email (from client-side provider if server verification also returns it)
            name: User display name (from client-side provider)

        Returns:
            Dict with user info

        Raises:
            ValueError: If token is invalid or provider not supported
        """
        provider = provider.lower().strip()

        if provider == "google":
            payload = await self._verify_google(token)
        elif provider == "apple":
            payload = await self._verify_apple(token)
        elif provider == "microsoft":
            payload = await self._verify_microsoft(token)
        else:
            raise ValueError(f"Unsupported OAuth provider: {provider}")

        # Extract user info from verified payload
        provider_user_id = payload.get("sub", "")
        verified_email = payload.get("email", "") or email
        verified_name = payload.get("name", "") or name
        if not verified_name:
            verified_name = payload.get("given_name", "") + " " + payload.get("family_name", "")
            verified_name = verified_name.strip()

        if not provider_user_id:
            raise ValueError("Invalid token: missing user identifier (sub)")

        # Check if this OAuth account is already linked
        user = await self.repo.get_user_by_oauth(provider, provider_user_id) if hasattr(self.repo, 'get_user_by_oauth') else None

        if user:
            # Existing user — return it
            logger.info("OAuth login: provider=%s, sub=%s, email=%s", provider, provider_user_id, verified_email)
            return {
                **user,
                "role": "member",
                "is_active": True,
                "created_at": user.get("created_at", datetime.now(timezone.utc).isoformat()),
            }

        # Try to find user by email and link the OAuth account
        if verified_email:
            user = await self.repo.get_user_by_email(verified_email)
            if user:
                # Link OAuth to existing account
                await self.repo.link_oauth_account(user["id"], provider, provider_user_id)
                logger.info("OAuth linked: provider=%s, email=%s", provider, verified_email)
                return {
                    **user,
                    "role": "member",
                    "is_active": True,
                    "created_at": user.get("created_at", datetime.now(timezone.utc).isoformat()),
                }

        # Create new user with OAuth
        organization_id = str(uuid.uuid4())
        from ..models import User as UserModel
        from datetime import datetime

        new_user = UserModel(
            email=verified_email or f"{provider}_{provider_user_id[:8]}@oauth.local",
            password_hash="",  # No password for OAuth users
            name=verified_name or f"{provider.title()} User",
            organization_id=organization_id,
        )
        result = await self.repo.create_user(new_user)
        await self.repo.link_oauth_account(result["id"], provider, provider_user_id)

        # Create free subscription for the new user
        try:
            from ..models import Subscription
            sub = Subscription(
                id=f"free-{organization_id[:8]}",
                organization_id=organization_id,
                stripe_customer_id="",
                tier="free",
                status="active",
                current_period_start=datetime.now(timezone.utc),
                current_period_end=datetime.now(timezone.utc),
            )
            await self.repo.store_subscription(sub)
        except Exception as e:
            logger.warning("Could not create free subscription for OAuth user: %s", e)

        logger.info("OAuth new user: provider=%s, email=%s, org=%s", provider, verified_email, organization_id)
        return {
            **result,
            "role": "member",
            "is_active": True,
            "created_at": new_user.created_at.isoformat(),
        }

    async def _fetch_jwks(self, url: str) -> list:
        """Fetch JSON Web Key Set from a URL."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("keys", [])

    async def _verify_jwt_with_jwks(self, token: str, jwks_url: str, audience: str, issuer: str) -> dict:
        """Verify a JWT using a JWKS endpoint."""
        try:
            unverified_header = jose_jwt.get_unverified_header(token)
        except Exception as e:
            raise ValueError(f"Invalid token header: {e}")

        jwks = await self._fetch_jwks(jwks_url)

        # Find the signing key
        rsa_key = None
        for key in jwks:
            if key.get("kid") == unverified_header.get("kid"):
                rsa_key = key
                break

        if not rsa_key:
            raise ValueError("Unable to find appropriate signing key")

        # Verify the token
        try:
            payload = jose_jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
                options={"verify_exp": True},
            )
            return payload
        except jose_jwt.JWTError as e:
            raise ValueError(f"Token verification failed: {e}")

    async def _verify_google(self, token: str) -> dict:
        """Verify a Google ID token."""
        client_id = self.settings.google_client_id
        if not client_id:
            raise ValueError("Google Sign-In not configured (MEMORY_BRIDGE_GOOGLE_CLIENT_ID not set)")

        try:
            from google.oauth2 import id_token
            from google.auth.transport import requests

            req = requests.Request()
            info = id_token.verify_oauth2_token(token, req, client_id)
            if info.get("iss") not in ["accounts.google.com", "https://accounts.google.com"]:
                raise ValueError("Wrong issuer")
            return info
        except ImportError:
            # Fallback to manual JWT verification
            return await self._verify_jwt_with_jwks(
                token,
                "https://www.googleapis.com/oauth2/v3/certs",
                client_id,
                "accounts.google.com",
            )
        except ValueError as e:
            raise ValueError(f"Google token verification failed: {e}")

    async def _verify_apple(self, token: str) -> dict:
        """Verify an Apple Sign-In identity token."""
        client_id = self.settings.apple_client_id
        if not client_id:
            raise ValueError("Apple Sign-In not configured (MEMORY_BRIDGE_APPLE_CLIENT_ID not set)")

        return await self._verify_jwt_with_jwks(
            token,
            "https://appleid.apple.com/auth/keys",
            client_id,
            "https://appleid.apple.com",
        )

    async def _verify_microsoft(self, token: str) -> dict:
        """Verify a Microsoft (Azure AD) identity token."""
        client_id = self.settings.microsoft_client_id
        if not client_id:
            raise ValueError("Microsoft Sign-In not configured (MEMORY_BRIDGE_MICROSOFT_CLIENT_ID not set)")

        return await self._verify_jwt_with_jwks(
            token,
            f"https://login.microsoftonline.com/common/discovery/v2.0/keys",
            client_id,
            "https://login.microsoftonline.com/common/v2.0",
        )
