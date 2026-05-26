"""Business logic for user registration, authentication, and session management."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from ..config import get_settings
from ..models import User

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
        # Check if email already taken
        existing = await self.repo.get_user_by_email(email)
        if existing:
            raise ValueError("Email already registered")

        # Hash password
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        if not organization_id:
            organization_id = str(uuid.uuid4())

        user = User(
            email=email,
            password_hash=password_hash,
            name=name,
            organization_id=organization_id,
        )

        result = await self.repo.create_user(user)
        logger.info("Registered user: %s (org=%s)", email, organization_id)
        return {
            **result,
            "role": "member",
            "is_active": True,
            "created_at": user.created_at.isoformat(),
        }

    async def authenticate(self, email: str, password: str) -> Optional[dict]:
        """Authenticate a user by email and password.

        Returns user dict (without password_hash) on success, None on failure.
        """
        user_data = await self.repo.get_user_by_email(email)
        if user_data is None:
            return None

        stored_hash = user_data.get("password_hash") or user_data.get("password_hash", "")
        if not stored_hash:
            return None

        try:
            if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
                return None
        except Exception:
            return None

        return {
            "id": user_data["id"],
            "email": user_data["email"],
            "name": user_data.get("name", ""),
            "organization_id": user_data.get("organization_id", ""),
            "role": "member",
            "is_active": True,
        }

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
            "project_id": user.get("organization_id"),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes or 60),
        }
        return jwt.encode(
            payload,
            jwt_secret,
            algorithm=settings.jwt_algorithm or "HS256",
        )

    async def refresh_token(self, token: str) -> Optional[str]:
        """Refresh an expiring token before it expires.

        Decodes the current token (must still be valid), remaps JWT
        claims to the user dict format expected by generate_token,
        and issues a fresh token with a new expiry window.

        Returns a new JWT string or None if the token is expired/invalid.
        """
        settings = self.settings
        jwt_secret = self._get_jwt_secret()
        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=[settings.jwt_algorithm or "HS256"],
                options={"verify_exp": True},
            )
            if payload.get("sub"):
                # Remap JWT claims to user dict keys for generate_token
                user_data = {
                    "id": payload.get("sub"),
                    "email": payload.get("email", ""),
                    "name": payload.get("name", ""),
                    "organization_id": payload.get("project_id", ""),
                    "role": payload.get("role", "member"),
                }
                return await self.generate_token(user_data)
        except jwt.ExpiredSignatureError:
            pass
        except jwt.InvalidTokenError:
            pass
        return None
