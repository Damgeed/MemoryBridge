"""Auth0 integration service.

Handles token validation via JWKS, authorization code exchange,
and user creation on first Auth0 login.
"""
import json
import logging
from typing import Optional

import httpx
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

logger = logging.getLogger(__name__)


class Auth0Service:
    """Service for Auth0 authentication integration.

    Validates Auth0-issued JWTs (RS256), exchanges authorization codes
    for tokens, and syncs Auth0 users to the local database.
    """

    def __init__(self):
        self.domain = None
        self.client_id = None
        self.client_secret = None
        self.audience = None
        self._jwks = None

    def configure(self, domain: str, client_id: str, client_secret: str, audience: str):
        """Configure Auth0 credentials. Call once at startup from env vars."""
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience
        self._jwks = None
        logger.info("Auth0 configured: domain=%s audience=%s", domain, audience)

    @property
    def enabled(self) -> bool:
        """Whether Auth0 integration is configured."""
        return bool(self.domain and self.client_id)

    def _jwks_url(self) -> str:
        return f"https://{self.domain}/.well-known/jwks.json"

    def _authorize_url(self, redirect_uri: str, state: str = "", connection: str = "", screen_hint: str = "") -> str:
        """Build the Auth0 Universal Login URL.

        Args:
            redirect_uri: Where Auth0 redirects after login
            state: CSRF token
            connection: Optional — if set, Auth0 goes directly to that social provider.
                        Examples: 'google-oauth2', 'apple', 'windowslive'
            screen_hint: Optional — 'signup' to pre-select sign-up screen, 'login' for login.
        """
        from urllib.parse import urlencode

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
        }
        # Only include audience if it's actually a registered Auth0 API
        # (omitting for social/passwordless to avoid invalid_request errors)
        if state:
            params["state"] = state
        if connection:
            params["connection"] = connection
        if screen_hint and screen_hint in ("login", "signup"):
            params["screen_hint"] = screen_hint
        return f"https://{self.domain}/authorize?{urlencode(params)}"

    async def _fetch_jwks(self) -> dict:
        """Fetch Auth0's JWKS (cached after first call)."""
        if self._jwks:
            return self._jwks
        url = self._jwks_url()
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            self._jwks = resp.json()
            logger.info("Fetched Auth0 JWKS from %s", url)
            return self._jwks

    async def validate_token(self, token: str) -> Optional[dict]:
        """Validate an Auth0-issued JWT (RS256) and return its claims.

        Returns the decoded payload dict on success, None on failure.
        """
        if not self.enabled:
            return None
        try:
            jwks = await self._fetch_jwks()
            # Find the signing key from the JWKS
            header = jose_jwt.get_unverified_header(token)
            key = None
            for k in jwks.get("keys", []):
                if k.get("kid") == header.get("kid"):
                    key = k
                    break
            if not key:
                logger.warning("No matching JWK found for kid=%s", header.get("kid"))
                return None

            # Try primary audience first (custom API), fall back to client_id
            # (passwordless OTP ID tokens often have aud=client_id)
            audiences = [self.audience, self.client_id]
            last_error = None
            for aud in audiences:
                if not aud:
                    continue
                try:
                    payload = jose_jwt.decode(
                        token,
                        key,
                        algorithms=["RS256"],
                        audience=aud,
                        issuer=f"https://{self.domain}/",
                    )
                    return payload
                except JWTError as e:
                    last_error = e
                    continue
            logger.warning("Auth0 token validation failed for all audiences: %s", last_error)
            return None
        except JWTError as e:
            logger.warning("Auth0 token validation failed: %s", e)
            return None
        except Exception as e:
            logger.warning("Auth0 JWKS fetch failed: %s", e)
            return None

    async def exchange_code(self, code: str, redirect_uri: str) -> Optional[dict]:
        """Exchange an authorization code for tokens.

        Returns dict with 'access_token', 'id_token', 'refresh_token' etc.
        """
        if not self.enabled or not self.client_secret:
            logger.warning("Auth0 code exchange: client_secret not configured")
            return None
        url = f"https://{self.domain}/oauth/token"
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=data, timeout=15)
            if resp.status_code != 200:
                logger.warning("Auth0 code exchange failed: %s", resp.text)
                return None
            return resp.json()

    async def start_passwordless(self, email: str) -> bool:
        """Send a 6-digit verification code to the user's email via Auth0 Passwordless.

        Requires Auth0 Passwordless (Email) connection to be enabled in Auth0 Dashboard.
        Returns True if the code was sent successfully.
        """
        if not self.enabled or not self.client_secret:
            logger.warning("Auth0 passwordless: client_secret not configured")
            return False
        url = f"https://{self.domain}/passwordless/start"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "connection": "email",
            "email": email,
            "send": "code",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.warning("Auth0 passwordless start failed (%s): %s", resp.status_code, resp.text)
                return False
            return True

    async def verify_passwordless(self, username: str, code: str, realm: str = "email") -> Optional[dict]:
        """Verify a 6-digit OTP code and exchange it for Auth0 tokens.

        Args:
            username: email (for email OTP) or phone number (for SMS OTP)
            code: 6-digit verification code
            realm: 'email' or 'sms'
        """
        if not self.enabled or not self.client_secret:
            logger.warning("Auth0 passwordless verify: client_secret not configured")
            return None
        url = f"https://{self.domain}/oauth/token"
        payload = {
            "grant_type": "http://auth0.com/oauth/grant-type/passwordless/otp",
            "realm": realm,
            "username": username,
            "otp": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "openid profile email",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.warning("Auth0 passwordless verify failed (%s): realm=%s — %s",
                               resp.status_code, realm, resp.text[:500])
                # Return error detail so the controller can show it
                try:
                    err_detail = resp.json()
                except Exception:
                    err_detail = {"error_description": resp.text[:200]}
                err_detail["_http_status"] = resp.status_code
                return err_detail
            result = resp.json()
            logger.info("Auth0 passwordless verify OK: got id_token with sub=%s",
                        result.get("id_token", "")[:50])
            return result

    async def start_passwordless_sms(self, phone: str) -> bool:
        """Send a 6-digit verification code to the user's phone via Auth0 Passwordless SMS.

        Requires Auth0 Passwordless (SMS) connection with Twilio configured in Auth0 Dashboard.
        Returns True if the code was sent successfully.
        """
        if not self.enabled or not self.client_secret:
            logger.warning("Auth0 SMS passwordless: client_secret not configured")
            return False
        url = f"https://{self.domain}/passwordless/start"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "connection": "sms",
            "phone_number": phone,
            "send": "code",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.warning("Auth0 SMS passwordless start failed (%s): %s", resp.status_code, resp.text)
                return False
            return True

    async def get_userinfo(self, access_token: str) -> Optional[dict]:
        """Get user info from Auth0's /userinfo endpoint."""
        url = f"https://{self.domain}/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning("Auth0 userinfo failed: %s", resp.text)
                return None
            return resp.json()


# Singleton
_auth0_service: Optional[Auth0Service] = None


def get_auth0_service() -> Auth0Service:
    """Get the Auth0 service singleton."""
    global _auth0_service
    if _auth0_service is None:
        _auth0_service = Auth0Service()
    return _auth0_service
