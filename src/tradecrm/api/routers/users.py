"""Panel kullanicilari. Rol yok: her aktif kullanici ayni yetkide."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from tradecrm.api.deps import Auth, current_user, get_db, require_csrf
from tradecrm.api.security import WeakPasswordError
from tradecrm.api.services import users as svc
from tradecrm.db.models import AdminUser
from tradecrm.db.repository import _as_utc

router = APIRouter(prefix="/users", tags=["users"])


def _row(u: AdminUser) -> dict:
    return {"id": u.id, "username": u.username, "is_active": u.is_active,
            "created_at": _as_utc(u.created_at).isoformat(),
            "last_login_at": _as_utc(u.last_login_at).isoformat() if u.last_login_at else None}


@router.get("")
def list_users(_: Auth = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_row(u) for u in db.exec(select(AdminUser).order_by(AdminUser.username))]


class UserIn(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


@router.post("")
def create_user(body: UserIn, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    try:
        user = svc.create_user(db, body.username, body.password)
    except (svc.UserError, WeakPasswordError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    svc.record_action(db, auth.username, "user_created", detail={"username": user.username})
    return _row(user)


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=256)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserPatch, auth: Auth = Depends(require_csrf),
                db: Session = Depends(get_db)) -> dict:
    user = db.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="kullanici bulunamadi")
    try:
        if body.password is not None:
            svc.set_password(db, user, body.password)
            svc.record_action(db, auth.username, "user_password_changed", detail={"username": user.username})
        if body.is_active is not None and body.is_active != user.is_active:
            svc.set_active(db, user, body.is_active)
            svc.record_action(db, auth.username, "user_activated" if body.is_active else "user_deactivated",
                              detail={"username": user.username})
    except (svc.UserError, WeakPasswordError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.refresh(user)
    return _row(user)
