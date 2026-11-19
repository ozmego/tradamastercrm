"""Panelden risk durumu degistirme -- guvenli yon kurali burada.

- Paper hesap: alim izni ac/kapat, kill-switch tetikle/sifirla.
- Canli hesap: yalnizca GUVENLI yon (izni kapat, kill-switch tetikle).
  Izni acmak, kill-switch'i sifirlamak ve ilk emir onayi yalnizca CLI'dan
  (bilincli, makinenin basinda yapilan islem).
Her islem hesap adinin aynen yazilmasini (confirm) ister ve kullanici
adiyla kaydedilir.
"""

from __future__ import annotations

from sqlmodel import Session

from tradecrm.api.services.accounts import all_account_names, is_live_account
from tradecrm.api.services.users import record_action
from tradecrm.risk.state import RiskStateStore


class RiskActionError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _check(db: Session, account: str, confirm: str) -> None:
    known = set(all_account_names(db)) | {"paper"}
    if account not in known:
        raise RiskActionError(404, f"bilinmeyen hesap: {account}")
    if confirm != account:
        raise RiskActionError(400, "onay icin hesap adini aynen yazin")


def set_trading(db: Session, username: str, account: str, enabled: bool, confirm: str, note: str | None = None) -> None:
    _check(db, account, confirm)
    if enabled and is_live_account(account):
        record_action(db, username, "trading_enable_refused", account, {"reason": "live"}, ok=False)
        raise RiskActionError(403, "canli hesapta alim izni panelden acilamaz; CLI: tradecrm risk enable-trading")
    RiskStateStore(db, broker=account).set_trading_enabled(enabled, reason=f"panel:{username}" + (f" -- {note}" if note else ""))
    record_action(db, username, "trading_enabled" if enabled else "trading_disabled", account, {"note": note})


def trip_kill_switch(db: Session, username: str, account: str, confirm: str, reason: str) -> None:
    _check(db, account, confirm)
    reason = (reason or "").strip()[:200] or "manuel"
    RiskStateStore(db, broker=account).trip_kill_switch(reason=f"panel:{username} -- {reason}")
    record_action(db, username, "kill_switch_trip", account, {"reason": reason})


def reset_kill_switch(db: Session, username: str, account: str, confirm: str) -> None:
    _check(db, account, confirm)
    if is_live_account(account):
        record_action(db, username, "kill_switch_reset_refused", account, {"reason": "live"}, ok=False)
        raise RiskActionError(403, "canli hesapta kill-switch panelden sifirlanamaz; CLI: tradecrm risk reset-kill-switch")
    RiskStateStore(db, broker=account).reset_kill_switch()
    record_action(db, username, "kill_switch_reset", account)
