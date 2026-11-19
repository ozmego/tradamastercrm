"""Turev piyasa metrikleri toplayicisi (Oneri 11b): open interest ve
hesap bazli long/short orani, Binance USDT-M, 1h.

Idempotent: ayni (sembol, metrik, ts) ikinci kez yazilmaz (ON CONFLICT DO
NOTHING). Her calismada son 500 saat (~21 gun) cekilir; DB'de o sembol icin
veri yoksa ya da son kayit bundan eskiyse, Binance'in tuttugu ~30 gunun
geri kalani da ayrica cekilir (geri doldurma).

Saklama: `purge_older_than` her toplamadan sonra `retention_days`'ten eski
satirlari siler (varsayilan 45 gun -- borsanin 30 gunluk penceresinden
uzun; 46. gune giren saatlik kayit bir sonraki calismada silinir).
"""

import logging
from datetime import UTC, datetime, timedelta

import ccxt
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, func, select

from tradecrm.data.provider import timeframe_to_timedelta
from tradecrm.data.sources.funding import perp_symbol
from tradecrm.db.models import MarketMetric

logger = logging.getLogger(__name__)


def _rows_from_oi(symbol: str, period: str, entries: list[dict]) -> list[dict]:
    rows = []
    for e in entries:
        ts = datetime.fromtimestamp(int(e["timestamp"]) / 1000, tz=UTC)
        if e.get("openInterestAmount") is not None:
            rows.append({"symbol": symbol, "metric": "open_interest", "period": period, "ts": ts,
                         "value": float(e["openInterestAmount"])})
        if e.get("openInterestValue") is not None:
            rows.append({"symbol": symbol, "metric": "open_interest_value", "period": period, "ts": ts,
                         "value": float(e["openInterestValue"])})
    return rows


def _rows_from_ls(symbol: str, period: str, entries: list[dict]) -> list[dict]:
    return [
        {"symbol": symbol, "metric": "long_short_ratio", "period": period,
         "ts": datetime.fromtimestamp(int(e["timestamp"]) / 1000, tz=UTC), "value": float(e["longShortRatio"])}
        for e in entries
        if e.get("longShortRatio") is not None
    ]


def upsert_metrics(session: Session, rows: list[dict]) -> int:
    """Yeni eklenen satir sayisini dondurur."""
    if not rows:
        return 0
    table = MarketMetric.__table__
    stmt = (
        insert(table).values(rows).on_conflict_do_nothing(constraint="uq_market_metric").returning(table.c.id)
    )
    # Cok satirli INSERT'te rowcount -1 donebilir; RETURNING ile gercekten eklenenleri say.
    inserted = len(session.exec(stmt).all())
    session.commit()
    return inserted


# Binance openInterestHist / globalLongShortAccountRatio: yalnizca son ~30 gun,
# istek basina en fazla 500 kayit.
EXCHANGE_HISTORY_DAYS = 29
PAGE_LIMIT = 500


def purge_older_than(session: Session, retention_days: int, now: datetime | None = None) -> int:
    """`retention_days`'ten eski metrikleri siler; silinen satir sayisini dondurur."""
    if retention_days <= 0:
        raise ValueError("retention_days pozitif olmali")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    result = session.execute(delete(MarketMetric).where(MarketMetric.ts < cutoff))
    session.commit()
    return int(result.rowcount or 0)


METRICS = ("open_interest", "open_interest_value", "long_short_ratio")


def time_range(session: Session, symbol: str, metrics: tuple[str, ...] = METRICS
               ) -> tuple[datetime | None, datetime | None]:
    """Metrikler arasinda en dar kapsam: (en gec baslayan metrigin ilk kaydi,
    en erken biten metrigin son kaydi). Bir metrik hic yoksa (None, None):
    bir metrikteki bosluk digerinin dolu olmasiyla gizlenmesin."""
    rows = session.exec(
        select(MarketMetric.metric, func.min(MarketMetric.ts), func.max(MarketMetric.ts))
        .where(MarketMetric.symbol == symbol, MarketMetric.metric.in_(metrics))
        .group_by(MarketMetric.metric)
    ).all()
    if len(rows) < len(metrics):
        return None, None

    def utc(t):
        return t.replace(tzinfo=UTC) if t.tzinfo is None else t

    return max(utc(lo) for _, lo, _ in rows), min(utc(hi) for _, _, hi in rows)


class DerivativesCollector:
    def __init__(self, session: Session, exchange=None, period: str = "1h") -> None:
        self.session = session
        self.exchange = exchange or ccxt.binanceusdm({"enableRateLimit": True})
        self.period = period
        # Son collect() cagrisinin ayrintilari (izleme modulu okur).
        self.last_errors: dict[str, str] = {}
        self.last_backfilled: list[str] = []

    def _backfill_since(self, symbol: str, now: datetime) -> int | None:
        """Son 500 saatlik sayfanin disinda kalan, borsada hala bulunan veri
        icin baslangic (ms); gerek yoksa None.
        - DB bos ya da en eski kayit borsa penceresinin basindan belirgin
          sekilde yeni: pencerenin basindan (ilk kurulum / yeni sembol).
        - Son kayit son sayfadan eski (toplayici uzun sure kapali): son kayittan."""
        window_start = now - timedelta(days=EXCHANGE_HISTORY_DAYS)
        first, last = time_range(self.session, symbol)
        if first is None or first > window_start + timedelta(days=1):
            return int(window_start.timestamp() * 1000)
        if last < now - timedelta(hours=PAGE_LIMIT - 1):
            return int(max(last, window_start).timestamp() * 1000)
        return None

    def _fetch(self, sym: str, perp: str, since: int | None) -> list[dict]:
        kw = {"timeframe": self.period, "limit": PAGE_LIMIT}
        if since is not None:
            # Binance long/short ucu yalniz startTime verilince onu yok sayip son
            # sayfayi dondurur; endTime ile pencere acikca belirtilir.
            step_ms = int(timeframe_to_timedelta(self.period).total_seconds() * 1000)
            kw["since"] = since
            kw["params"] = {"endTime": since + (PAGE_LIMIT - 1) * step_ms}
        return (_rows_from_oi(sym, self.period, self.exchange.fetch_open_interest_history(perp, **kw))
                + _rows_from_ls(sym, self.period, self.exchange.fetch_long_short_ratio_history(perp, **kw)))

    def collect(self, symbols: list[str], now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        added: dict[str, int] = {}
        self.last_errors, self.last_backfilled = {}, []
        for sym in symbols:
            rows: list[dict] = []
            perp = perp_symbol(sym)
            try:
                since = self._backfill_since(sym, now)
                if since is not None:
                    rows += self._fetch(sym, perp, since)
                    self.last_backfilled.append(sym)
                rows += self._fetch(sym, perp, None)
            except Exception as exc:  # noqa: BLE001 -- bir sembolun hatasi digerlerini durdurmamali
                logger.warning("%s metrikleri cekilemedi: %s", sym, type(exc).__name__)
                added[sym] = -1  # hata isareti
                self.last_errors[sym] = f"{type(exc).__name__}: {str(exc)[:200]}"
                continue
            added[sym] = upsert_metrics(self.session, rows)
        return added


def coverage(session: Session) -> list[tuple[str, str, int, datetime, datetime]]:
    stmt = select(
        MarketMetric.symbol, MarketMetric.metric, func.count(), func.min(MarketMetric.ts), func.max(MarketMetric.ts)
    ).group_by(MarketMetric.symbol, MarketMetric.metric).order_by(MarketMetric.symbol, MarketMetric.metric)
    return list(session.exec(stmt))
