from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.deps import (
    SESSION_COOKIE,
    Auth,
    current_user,
    get_ctx,
    get_db,
    require_csrf,
)
from tradecrm.api.services.users import (
    authenticate,
    close_session,
    open_session,
    record_action,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response,
          ctx: AppContext = Depends(get_ctx), db: Session = Depends(get_db)) -> dict:
    ip = _client_ip(request)
    wait = ctx.throttle.retry_after(body.username, ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"cok fazla hatali deneme; {wait} sn sonra tekrar deneyin",
                            headers={"Retry-After": str(wait)})
    user = authenticate(db, body.username, body.password)
    if user is None:
        ctx.throttle.record_failure(body.username, ip)
        record_action(db, body.username.strip().lower()[:64], "login_failed", detail={"ip": ip}, ok=False)
        raise HTTPException(status_code=401, detail="kullanici adi ya da parola hatali")
    ctx.throttle.record_success(body.username, ip)
    issued = open_session(db, user, ctx.session_hours, ip, request.headers.get("user-agent"))
    response.set_cookie(
        SESSION_COOKIE, issued.token, httponly=True, samesite="strict", secure=ctx.cookie_secure,
        max_age=ctx.session_hours * 3600, path="/",
    )
    record_action(db, user.username, "login", detail={"ip": ip})
    return {"username": user.username, "csrf_token": issued.csrf_token}


@router.get("/me")
def me(auth: Auth = Depends(current_user)) -> dict:
    return {"username": auth.username, "csrf_token": auth.session_row.csrf_token,
            "expires_at": auth.session_row.expires_at.isoformat()}


@router.post("/logout")
def logout(response: Response, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    close_session(db, auth.session_row)
    response.delete_cookie(SESSION_COOKIE, path="/")
    record_action(db, auth.username, "logout")
    return {"ok": True}
