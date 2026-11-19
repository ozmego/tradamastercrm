from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tz_column(*, index: bool = False):
    return Column(DateTime(timezone=True), index=index)


class Trade(SQLModel, table=True):
    """Gerceklesen bir fill/pozisyon kaydi (paper veya live).

    realized_pnl_pct, trade acilirken hesaba anlik bakiyeye (account_balance_at_entry)
    gore hesaplanir -- islem hacmine gore degil, hesap buyumesine gore. Gunluk
    hedef takibi (risk/target_sizing) bu yuzdelerin gun icinde toplanmasiyla
    yapilir; bu, tam bir compounded equity-curve degil, v1 icin yeterli bir
    yaklastirmadir.
    """

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    side: str = "BUY"  # spot/long-only v1: giris hep BUY, cikis SELL ile temsil edilir (status/exit alanlarinda)
    strategy_name: str
    broker: str  # "paper" | "binance"

    entry_time: datetime = Field(sa_column=_tz_column(index=True))
    entry_price: float
    qty: float
    account_balance_at_entry: float

    exit_time: datetime | None = Field(default=None, sa_column=_tz_column(index=True))
    exit_price: float | None = None

    status: str = Field(default="OPEN", index=True)  # "OPEN" | "CLOSED"
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None
    fees: float = 0.0  # giris + cikis komisyonu toplami (kapaninca)
    entry_fees: float = 0.0
    signal_reason: str | None = None
    exit_reason: str | None = None
    entry_client_order_id: str | None = Field(default=None, index=True)
    exit_client_order_id: str | None = Field(default=None, index=True)

    # Cikis katmani (risk/exits.py) durumu. None: o seviye kullanilmiyor.
    initial_stop_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    highest_price: float | None = None

    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())


class RiskEvent(SQLModel, table=True):
    """Kill-switch / TRADING_ENABLED / ilk-emir-onayi gibi risk durumu
    gecislerinin kaydi. Bir bayragin *anlik durumu*, o bayrak tipi icin
    en son event satiridir -- ayri bir dosya/tablo yok."""

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)  # KILL_SWITCH_TRIP | KILL_SWITCH_RESET | TRADING_ENABLED | TRADING_DISABLED | FIRST_ORDER_CONFIRMED
    # Hesap (= broker adi): "paper", "paper:<profil>", "binance". Bir alt
    # hesap ("paper:x") kendi olaylarina ek olarak kok hesabin ("paper")
    # olaylarindan da etkilenir (bkz. RiskEventRepository).
    account: str = Field(default="paper", index=True)
    timestamp: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    reason: str | None = None
    realized_pnl_pct_at_event: float | None = None


class Order(SQLModel, table=True):
    """Her emir denemesinin kalici kaydi.

    client_order_id deterministiktir: (hesap, sembol, yon, sinyal mumu)
    -> ayni kimlik. (account, symbol, side, bar_time) benzersizdir: ayni
    mum icin ikinci emir -- tekrar gelen sinyal, yeniden baslatma ya da
    eszamanli iki runner -- veritabani seviyesinde engellenir. Zaman
    asiminda ayni kimlikle borsaya sorulur, kor tekrar gonderilmez.

    Durumlar: NEW (rezerve, henuz gonderilmedi) -> FILLED | REJECTED |
    UNKNOWN (sonuc belirsiz; uzlastirilana kadar yeni alim yok) | ABANDONED
    (gonderilmeden yarim kalmis, borsada da yok)."""

    __tablename__ = "trade_order"  # "order" SQL'de ayrilmis kelime
    __table_args__ = (UniqueConstraint("account", "symbol", "side", "bar_time", name="uq_order_intent"),)

    id: int | None = Field(default=None, primary_key=True)
    client_order_id: str = Field(index=True, unique=True)
    account: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str  # "BUY" | "SELL"
    bar_time: datetime = Field(sa_column=_tz_column(index=True))  # sinyali ureten (kapanmis) mumun acilis zamani
    status: str = Field(default="NEW", index=True)
    reason: str | None = None  # sinyal/cikis nedeni
    requested_notional: float | None = None
    filled_qty: float | None = None
    avg_price: float | None = None
    exchange_order_id: str | None = None
    trade_id: int | None = None
    error: str | None = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())


class MarketMetric(SQLModel, table=True):
    """Toplayicilarin biriktirdigi zaman serileri (Oneri 11b): open interest,
    long/short orani vb. Borsa bu verilerin sadece son ~30 gununu verdigi
    icin, backtest edilebilmeleri ancak bugunden itibaren biriktirilmeleriyle
    mumkun.

    ts: borsanin zaman damgasi. Kullanirken degerin bilindigi an olarak
    muhafazakar bicimde ts + period alinmalidir."""

    __table_args__ = (UniqueConstraint("symbol", "metric", "ts", name="uq_market_metric"),)

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    metric: str = Field(index=True)  # "open_interest" | "open_interest_value" | "long_short_ratio"
    period: str = "1h"
    ts: datetime = Field(sa_column=_tz_column(index=True))
    value: float
    collected_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())


# ------------------------------------------------------------- hesaplar
class Account(SQLModel, table=True):
    """Bir islem hesabinin kalici tanimi. Paper hesabin nakdi
    (initial + gerceklesen - acik maliyet) baslangic sermayesine baglidir;
    bu deger burada saklanir ki panel/rapor ayni sonucu uretsin. Ayni hesap
    farkli sermayeyle yeniden baslatilamaz (muhasebe tutarsizligi)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # "paper", "paper:<profil>", "binance"
    mode: str = "paper"  # "paper" | "live"
    initial_capital: float = 0.0
    profile: str | None = None
    timeframe: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())


class EquitySnapshot(SQLModel, table=True):
    """Runner tick'i sonunda hesap durumu: equity grafiginin kaynagi."""

    id: int | None = Field(default=None, primary_key=True)
    account: str = Field(index=True)
    ts: datetime = Field(sa_column=_tz_column(index=True))
    cash: float
    allocated: float
    market_value: float
    equity: float
    open_positions: int = 0


# --------------------------------------------------------------- admin paneli
class AdminUser(SQLModel, table=True):
    """Panel kullanicisi. Rol yok: butun aktif kullanicilar ayni yetkide."""

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    last_login_at: datetime | None = Field(default=None, sa_column=_tz_column())


class AdminSession(SQLModel, table=True):
    """Sunucu tarafli oturum: cerezde rastgele token, DB'de yalnizca sha256'si."""

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int = Field(index=True, foreign_key="adminuser.id")
    csrf_token: str
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    expires_at: datetime = Field(sa_column=_tz_column(index=True))
    revoked: bool = False
    ip: str | None = None
    user_agent: str | None = None


class AdminAction(SQLModel, table=True):
    """Panelden yapilan her durum degisikliginin denetim kaydi."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    username: str = Field(index=True)
    action: str = Field(index=True)
    account: str | None = None
    detail: str | None = None  # JSON metni
    ok: bool = True


class BacktestRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(default="QUEUED", index=True)  # QUEUED | RUNNING | DONE | FAILED | CANCELLED
    params: str  # JSON
    result: str | None = None  # JSON
    error: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    started_at: datetime | None = Field(default=None, sa_column=_tz_column())
    finished_at: datetime | None = Field(default=None, sa_column=_tz_column())


class CollectorRun(SQLModel, table=True):
    """Bir toplayicinin tek bir calismasi (izleme modulu: monitor/collectors.py).
    Durum: RUNNING -> OK | PARTIAL (bazi semboller hata verdi) | FAILED;
    surec calisma sirasinda olduyse sonraki calisma INTERRUPTED isaretler."""

    id: int | None = Field(default=None, primary_key=True)
    collector: str = Field(index=True)
    trigger: str = "schedule"  # schedule | manual (panel) | cli
    status: str = Field(default="RUNNING", index=True)
    started_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    finished_at: datetime | None = Field(default=None, sa_column=_tz_column())
    duration_ms: int | None = None
    symbols_total: int = 0
    symbols_failed: int = 0
    rows_added: int = 0
    rows_purged: int = 0
    backfilled: str | None = None  # JSON: geri doldurma yapilan semboller
    errors: str | None = None  # JSON: {sembol: hata}
    error: str | None = None  # calismanin tamamini durduran hata
    pid: int | None = None


class Report(SQLModel, table=True):
    """Olusturuldugu andaki verinin donmus goruntusu (reports/ modulu).
    data: reports.builder.build_report() ciktisi (JSON)."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # day | week | month | custom
    title: str
    account: str | None = Field(default=None, index=True)  # None: tum hesaplar
    period_start: datetime = Field(sa_column=_tz_column(index=True))
    period_end: datetime = Field(sa_column=_tz_column())  # haric
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    created_by: str  # kullanici adi | "schedule" | "cli"
    data: str  # JSON
    sent_at: datetime | None = Field(default=None, sa_column=_tz_column())
