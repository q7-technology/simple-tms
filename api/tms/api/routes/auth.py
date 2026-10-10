"""Sign in: desktop users with a username and password, drivers with a code and PIN."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Who
from tms.api.errors import Unauthorised
from tms.models import Driver, User
from tms.services import access, sessions

router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_TRIES = 5
PASSWORD_LOCK_MINUTES = 15
PIN_TRIES = 5


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class SessionOut(BaseModel):
    token: str
    expires_in: int
    refresh_token: str
    user: UserOut


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(max_length=200)


class MeOut(BaseModel):
    kind: str
    id: int
    name: str
    display_name: str
    role: str
    scopes: list[str]


class DriverLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(max_length=32)
    pin: str = Field(max_length=8)


class DriverSessionOut(BaseModel):
    token: str
    expires_in: int
    driver: MeOut


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role)


def _refused(detail: str, code: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail, "code": code, **extra},
                        headers={"WWW-Authenticate": "Bearer"})


def _is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.now(UTC))


@router.post("/login", response_model=SessionOut)
def login(body: LoginIn, db: DB):
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.active:
        # an unknown name and a wrong password look alike
        return _refused("wrong username or password", "wrong_password")
    if _is_locked(user):
        return _refused("this account is locked; try again later or ask an admin", "locked")
    if not access.verify_password(body.password, user.password_hash):
        user.failed_attempts = (user.failed_attempts or 0) + 1
        left = PASSWORD_TRIES - user.failed_attempts
        if left <= 0:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=PASSWORD_LOCK_MINUTES)
            user.failed_attempts = 0
            db.commit()
            return _refused(f"too many wrong passwords; locked for {PASSWORD_LOCK_MINUTES} minutes",
                            "locked")
        db.commit()
        return _refused("wrong username or password", "wrong_password", tries_left=left)

    row, refresh = sessions.start_session(db, user)
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return SessionOut(token=sessions.issue_access_token(user),
                      expires_in=sessions.ACCESS_TTL_SECONDS, refresh_token=refresh,
                      user=user_out(user))


@router.post("/refresh", response_model=SessionOut)
def refresh(body: RefreshIn, db: DB):
    row = sessions.find_session(db, body.refresh_token)
    user = db.get(User, row.user_id) if row else None
    if row is None or user is None or not user.active:
        raise Unauthorised("session expired or invalid")
    new_refresh = sessions.rotate_session(db, row)
    db.commit()
    return SessionOut(token=sessions.issue_access_token(user),
                      expires_in=sessions.ACCESS_TTL_SECONDS, refresh_token=new_refresh,
                      user=user_out(user))


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, db: DB):
    row = sessions.find_session(db, body.refresh_token)
    if row is not None:
        sessions.revoke_session(db, row)
        db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(who: Who):
    return MeOut(kind=who.kind, id=who.id, name=who.name, display_name=who.display_name,
                 role=who.role, scopes=who.scopes)


@router.post("/driver-login", response_model=DriverSessionOut)
def driver_login(body: DriverLoginIn, db: DB):
    """Five wrong PINs lock the driver until a manager unlocks them."""
    driver = db.execute(select(Driver).where(Driver.code == body.code)).scalar_one_or_none()
    if driver is None or not driver.active:
        return _refused("no active driver with that code", "unknown_driver")
    if driver.locked:
        return _refused(f"{driver.code} is locked after too many wrong PINs; a manager can unlock it",
                        "locked")
    if not access.verify_password(body.pin, driver.pin_hash):
        driver.failed_attempts += 1
        if driver.failed_attempts >= PIN_TRIES:
            driver.locked = True
            db.commit()
            return _refused(f"wrong PIN {PIN_TRIES} times; {driver.code} is locked until a "
                            f"manager unlocks it", "locked")
        db.commit()
        return _refused("wrong PIN", "wrong_pin", tries_left=PIN_TRIES - driver.failed_attempts)
    driver.failed_attempts = 0
    db.commit()
    return DriverSessionOut(
        token=sessions.issue_driver_token(driver.id), expires_in=sessions.DRIVER_TTL_SECONDS,
        driver=MeOut(kind="driver", id=driver.id, name=driver.code, display_name=driver.name,
                     role="driver", scopes=list(sessions.DRIVER_SCOPES)),
    )
