"""Authentication endpoints for user registration and login."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr

from ..services.user_service import UserService
from ..dependencies import get_storage

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
    """Register a new user account."""
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user = await service.register(
        email=req.email,
        password=req.password,
        name=req.name,
    )

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


@router.post("/refresh")
async def refresh_token(token_data: dict):
    """Refresh an authentication token."""
    service = UserService()
    new_token = await service.refresh_token(token_data.get("token", ""))
    if new_token is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"token": new_token}
