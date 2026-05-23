"""Business logic for user registration, authentication, and session management."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from ..config import get_settings

logger = logging.getLogger(__name__)


class UserService:
    """Service layer for user operations.

    Handles:
    - User registration with bcrypt password hashing
    - Login with password verification
    - JWT token generation
    - Token refresh
    """

    def __init__(self, repo=None):
        self.repo = repo
        self.settings = get_settings()

    async def register(
        self,
        email: str,
        password: str,
        name: str = "",
        organization_id: Optional[str] = None,
    ) -> dict:
        """Register a new user.

        Args:
            email: User's email address
            password: Plaintext password (hashed with bcrypt before storage)
            name: Optional display name
            organization_id: Optional org to assign user to

        Returns:
            Dict with user info (excluding password_hash)
        """
        # Hash password
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        # Store user (in production, this goes through the repository)
        user = {
            "id": None,  # Will be set by storage
            "email": email,
            "name": name,
            "organization_id": organization_id,
            "role": "member",
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("Registered user: %s", email)
        return user

    async def authenticate(self, email: str, password: str) -> Optional[dict]:
        """Authenticate a user by email and password.

        Returns user dict (without password_hash) on success, None on failure.
        """
        # In production, look up user by email from repository
        # For now, return None (will be implemented with DB in Phase 4 full)
        return None

    def _get_jwt_secret(self) -> str:
        """Return the validated JWT secret or raise a clear error."""
        secret = self.settings.jwt_secret
        if not secret:
            raise RuntimeError(
                "JWT secret not configured. Set MEMORY_BRIDGE_JWT_SECRET environment variable. "
                "This is required for authentication to work."
            )
        return secret

    async def generate_token(self, user: dict) -> str:
        """Generate a JWT token for an authenticated user."""
        settings = self.settings
        jwt_secret = self._get_jwt_secret()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user.get("id", user.get("email")),
            "email": user.get("email"),
            "name": user.get("name", ""),
            "role": user.get("role", "member"),
            "project_id": user.get("project_id"),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes or 60),
        }
        return jwt.encode(
            payload,
            jwt_secret,
            algorithm=settings.jwt_algorithm or "HS256",
        )

    async def refresh_token(self, token: str) -> Optional[str]:
        """Refresh an expired token if the refresh window is valid."""
        settings = self.settings
        jwt_secret = self._get_jwt_secret()
        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=[settings.jwt_algorithm or "HS256"],
                options={"verify_exp": True},
            )
            # Generate new token if not expired
            if payload.get("sub"):
                return await self.generate_token(payload)
        except jwt.ExpiredSignatureError:
            pass  # Can't refresh expired tokens
        except jwt.InvalidTokenError:
            pass
        return None
