"""Paper runner baslatma parametrelerinin dogrulanmasi. Panel (POST
/processes/live/start) ve otomatik baslatma (`tradecrm live resume`) ayni
kurallardan gecer: profil/sembol/TF beyaz listesi, yalnizca paper, kayitli
hesap farkli sermayeyle baslatilamaz."""

from sqlmodel import Session

from tradecrm.api.services.processes import build_live_command
from tradecrm.backtest.service import (
    InvalidParamsError,
    validate_profile,
    validate_symbols,
    validate_timeframe,
)
from tradecrm.db.repository import AccountRepository

MIN_CAPITAL, MAX_CAPITAL = 100.0, 10_000_000.0


class LiveStartError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def prepare_live_start(db: Session | None, profile: str, symbols: list[str], timeframe: str,
                       initial_capital: float) -> tuple[list[str], dict]:
    """Dogrulanmis (komut, meta). Gecersizse LiveStartError(400|409)."""
    from tradecrm.strategy.profiles import Portfolio

    try:
        target = validate_profile(profile)
        symbols = validate_symbols(symbols)
        timeframe = validate_timeframe(timeframe)
    except InvalidParamsError as exc:
        raise LiveStartError(400, str(exc)) from None
    try:
        initial_capital = float(initial_capital)
    except (TypeError, ValueError):
        raise LiveStartError(400, "gecersiz sermaye") from None
    if not MIN_CAPITAL <= initial_capital <= MAX_CAPITAL:
        raise LiveStartError(400, f"sermaye {MIN_CAPITAL:,.0f} - {MAX_CAPITAL:,.0f} arasinda olmali")

    members = target.members if isinstance(target, Portfolio) else ((target, 1.0),)
    # db=None: veritabani henuz yok (oturum acilisi); butce kontrolunu runner baslarken kendisi yapar.
    repo = AccountRepository(db) if db is not None else None
    for prof, weight in (members if repo is not None else ()):
        name = f"paper:{prof.name}" if isinstance(target, Portfolio) else "paper"
        acc = repo.get(name)
        want = initial_capital * weight
        if acc is not None and abs(acc.initial_capital - want) > 1e-6:
            raise LiveStartError(409, f"{name} hesabi {acc.initial_capital:,.2f} sermayeyle kayitli; "
                                      f"{want:,.2f} ile baslatilamaz")

    meta = {"profile": target.name, "symbols": symbols, "timeframe": timeframe,
            "initial_capital": initial_capital, "mode": "paper"}
    return build_live_command(target.name, symbols, timeframe, initial_capital), meta


LIVE_MIN_CAPITAL = 10.0


def prepare_exchange_start(db: Session | None, exchange_id: str, profile: str, symbols: list[str], timeframe: str,
                           initial_capital: float) -> tuple[list[str], dict]:
    """Canli (gercek hesap) runner icin dogrulanmis (komut, meta). Portfoy yok; butce kayitli
    hesapla ayni olmali. Emir sartlari (.env salteri, DB alim izni, ilk emir onayi) AYRICA gerekir."""
    from tradecrm.broker.ccxt_spot_adapter import get_spec
    from tradecrm.strategy.profiles import Portfolio

    try:
        spec = get_spec(exchange_id)
        target = validate_profile(profile)
        symbols = validate_symbols(symbols)
        timeframe = validate_timeframe(timeframe)
    except (KeyError, InvalidParamsError) as exc:
        raise LiveStartError(400, str(exc).strip("'\"")) from None
    if isinstance(target, Portfolio):
        raise LiveStartError(400, "canli modda portfoy desteklenmiyor; tek profil verin")
    try:
        initial_capital = float(initial_capital)
    except (TypeError, ValueError):
        raise LiveStartError(400, "gecersiz butce") from None
    if not LIVE_MIN_CAPITAL <= initial_capital <= MAX_CAPITAL:
        raise LiveStartError(400, f"butce {LIVE_MIN_CAPITAL:,.0f} - {MAX_CAPITAL:,.0f} arasinda olmali")
    acc = AccountRepository(db).get(spec.id) if db is not None else None
    if acc is not None and abs(acc.initial_capital - initial_capital) > 1e-6:
        raise LiveStartError(409, f"{spec.id} hesabi {acc.initial_capital:,.2f} butceyle kayitli; "
                                  f"{initial_capital:,.2f} ile baslatilamaz")
    meta = {"exchange": spec.id, "profile": target.name, "symbols": symbols, "timeframe": timeframe,
            "initial_capital": initial_capital, "mode": spec.id}
    return build_live_command(target.name, symbols, timeframe, initial_capital, mode=spec.id), meta
