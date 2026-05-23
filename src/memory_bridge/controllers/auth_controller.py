"""Auth endpoints for user registration and login.

Provides /auth/register and /auth/login endpoints that issue JWTs.
In Phase 4 these will integrate with UserService for proper user management.
For now, they return stub responses noting that full functionality is coming.
"""

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(req: RegisterRequest):
    """Register a new user account.

    In Phase 4 this will use UserService to create and store user accounts.
    For now, returns a stub response.
    """
    return {"message": "Registration coming in Phase 4"}


@router.post("/login")
async def login(req: LoginRequest):
    """Authenticate and receive a JWT token.

    In Phase 4 this will verify credentials against UserService and issue a JWT.
    For now, returns a stub response.
    """
    return {"message": "Login coming in Phase 4"}
