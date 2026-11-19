"""Kullanici ve oturum islemleri (CLI ve API ortak kullanir)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from tradecrm.api.security import (
    hash_password,
    new_token,
    token_hash,
    validate_password,
    verify_password_or_dummy,
)
from tradecrm.db.models import AdminAction, AdminSession, AdminUser
from tradecrm.db.repository import _as_utc

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


class UserError(ValueError):
    pass


def normalize_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise UserError("kullanici adi 3-32 karakter: harf, rakam, _ . -")
    return username.lower()


def get_user(session: Session, username: str) -> AdminUser | None:
    return session.exec(select(AdminUser).where(AdminUser.username == username.strip().lower())).first()


def create_user(session: Session, username: str, password: str) -> AdminUser:
    name = normalize_username(username)
    validate_password(password)
    if get_user(session, name) is not None:
        raise UserError(f"{name} zaten var")
    user = AdminUser(username=name, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_password(session: Session, user: AdminUser, password: str) -> None:
    validate_password(password)
    user.password_hash = hash_password(password)
    session.add(user)
    revoke_user_sessions(session, user.id)  # parola degisince eski oturumlar gecersiz
    session.commit()


def set_active(session: Session, user: AdminUser, active: bool) -> None:
    if not active:
        others = session.exec(
            select(AdminUser).where(AdminUser.is_active == True, AdminUser.id != user.id)
        ).first()
        if others is None:
            raise UserError("son aktif kullanici devre disi birakilamaz")
        revoke_user_sessions(session, user.id)
    user.is_active = active
    session.add(user)
    session.commit()


def revoke_user_sessions(session: Session, user_id: int) -> None:
    for s in session.exec(select(AdminSession).where(AdminSession.user_id == user_id, AdminSession.revoked == False)):
        s.revoked = True
        session.add(s)
    session.commit()


def authenticate(session: Session, username: str, password: str) -> AdminUser | None:
    user = get_user(session, username)
    ok = verify_password_or_dummy(password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        return None
    return user


@dataclass
class IssuedSession:
    token: str  # cereze yazilir; DB'de yalnizca hash'i var
    csrf_token: str
    expires_at: datetime


def open_session(session: Session, user: AdminUser, hours: int, ip: str | None, user_agent: str | None) -> IssuedSession:
    token = new_token()
    csrf = new_token()
    expires = datetime.now(UTC) + timedelta(hours=hours)
    session.add(AdminSession(
        token_hash=token_hash(token), user_id=user.id, csrf_token=csrf, expires_at=expires,
        ip=ip, user_agent=(user_agent or "")[:200],
    ))
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    session.commit()
    return IssuedSession(token, csrf, expires)


def resolve_session(session: Session, token: str, hours: int) -> tuple[AdminUser, AdminSession] | None:
    """Gecerli oturumu dondurur ve suresini kaydirir; degilse None."""
    row = session.exec(select(AdminSession).where(AdminSession.token_hash == token_hash(token))).first()
    now = datetime.now(UTC)
    if row is None or row.revoked or _as_utc(row.expires_at) <= now:
        return None
    user = session.get(AdminUser, row.user_id)
    if user is None or not user.is_active:
        return None
    new_expiry = now + timedelta(hours=hours)
    if new_expiry - _as_utc(row.expires_at) > timedelta(minutes=5):  # her istekte yazma
        row.expires_at = new_expiry
        session.add(row)
        session.commit()
    return user, row


def close_session(session: Session, row: AdminSession) -> None:
    row.revoked = True
    session.add(row)
    session.commit()


def record_action(session: Session, username: str, action: str, account: str | None = None,
                  detail: dict | None = None, ok: bool = True) -> None:
    session.add(AdminAction(
        username=username, action=action, account=account,
        detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None, ok=ok,
    ))
    session.commit()
