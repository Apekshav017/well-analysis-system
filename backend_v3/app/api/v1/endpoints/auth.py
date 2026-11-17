"""
api/v1/endpoints/auth.py

POST /auth/login, GET /auth/me, POST /auth/register.
Responses say whether the account is an administrator or a user, and list the rights of the account.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.jwt_handler import create_access_token
from app.auth.password import hash_password, verify_password
from app.auth.permissions import permissions_for, role_of
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, RegisterRequest, Token, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


def _to_response(user: User) -> UserResponse:
    out = UserResponse.model_validate(user)
    out.role = role_of(user)
    out.permissions = sorted(permissions_for(user))
    return out


@router.post("/login", response_model=Token)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    try:
        email = request.email.lower()
        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(request.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        access_token = create_access_token(data={"sub": str(user.id)})
        logger.info("User logged in: %s", user.email)
        return {"access_token": access_token, "token_type": "bearer", "expires_in": 86400}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Login error")
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Login failed")


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(user: User = Depends(get_current_user)):
    return _to_response(user)


@router.post("/register", response_model=UserResponse)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    try:
        email = request.email.lower()
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
        user = User(
            email=email,
            hashed_password=hash_password(request.password),
            full_name=request.full_name,
            mobile=request.mobile,
            organization=request.organization,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("New user registered: %s", user.email)
        return _to_response(user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Registration error: %s", e)
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed")
