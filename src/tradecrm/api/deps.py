from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.services.users import resolve_session
from tradecrm.db.models import AdminSession, AdminUser

SESSION_COOKIE = "tc_session"


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_db(ctx: AppContext = Depends(get_ctx)) -> Iterator[Session]:
    try:
        cm = ctx.session_factory()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"veritabani kullanilamiyor: {type(exc).__name__}") from None
    with cm as session:
        yield session


@dataclass
class Auth:
    user: AdminUser
    session_row: AdminSession

    @property
    def username(self) -> str:
        return self.user.username


def current_user(request: Request, ctx: AppContext = Depends(get_ctx), db: Session = Depends(get_db)) -> Auth:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="giris gerekli")
    resolved = resolve_session(db, token, ctx.session_hours)
    if resolved is None:
        raise HTTPException(status_code=401, detail="oturum gecersiz ya da suresi doldu")
    return Auth(*resolved)


def require_csrf(auth: Auth = Depends(current_user), x_csrf_token: str | None = Header(default=None)) -> Auth:
    """Durum degistiren her istek: gecerli oturum + oturuma bagli CSRF token'i."""
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, auth.session_row.csrf_token):
        raise HTTPException(status_code=403, detail="gecersiz CSRF token")
    return auth
