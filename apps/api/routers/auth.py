"""Auth endpoints. Sessions are httpOnly SameSite=Lax cookies; the token is never put in a response body."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from ..auth import COOKIE, hash_password, make_token, verify_password
from ..container import Container
from ..db import User
from ..deps import current_user, get_container
from ..errors import ApiError
from ..schemas import Credentials, RegisterIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY = hash_password("not-a-real-password")  # verified against when the email is unknown, so timing does not reveal which emails exist


def _set_cookie(response: Response, c: Container, user: User) -> None:
    ttl = c.settings.session_ttl_minutes
    response.set_cookie(COOKIE, make_token(user.id, user.role, c.settings.app_secret_key, ttl), max_age=ttl * 60, httponly=True,
                        samesite="lax", secure=c.settings.cookie_secure, path="/")


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, response: Response, c: Container = Depends(get_container)) -> User:
    """The first account can always register (bootstrap). Later ones only when ALLOW_REGISTRATION=true."""
    email = body.email.lower()
    async with c.sessions() as db:
        count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
        if count > 0 and not c.settings.allow_registration:
            raise ApiError(403, "registration_closed", "Registration is closed")
        if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none() is not None:
            raise ApiError(409, "email_taken", "An account with this email already exists")
        user = User(email=email, password_hash=hash_password(body.password), role="owner")
        db.add(user)
        await db.commit()
    _set_cookie(response, c, user)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: Credentials, request: Request, response: Response, c: Container = Depends(get_container)) -> User:
    email = body.email.lower()
    key = f"{request.client.host if request.client else '-'}|{email}"
    if c.throttle.blocked(key):
        raise ApiError(429, "too_many_attempts", "Too many failed sign-ins; wait a minute", retryable=True, headers={"Retry-After": "60"})
    async with c.sessions() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else _DUMMY)
    if user is None or not ok:
        c.throttle.fail(key)
        raise ApiError(401, "invalid_credentials", "Invalid email or password")
    c.throttle.success(key)
    _set_cookie(response, c, user)
    return user


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user
