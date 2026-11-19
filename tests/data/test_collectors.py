from datetime import UTC, datetime, timedelta

from sqlmodel import select

from tradecrm.data.collectors.derivatives import (
    DerivativesCollector,
    coverage,
    purge_older_than,
    upsert_metrics,
)
from tradecrm.db.models import MarketMetric

H = 3_600_000


class _FakeExchange:
    def __init__(self, n=3, fail=False):
        self.n = n
        self.fail = fail
        self.calls = []

    def fetch_open_interest_history(self, symbol, timeframe="1h", limit=500, since=None, params=None):
        self.calls.append(since)
        if since is not None:
            assert params["endTime"] == since + 499 * H
        if self.fail:
            raise ConnectionError("down")
        return [{"timestamp": i * H, "openInterestAmount": 100.0 + i, "openInterestValue": 1e6 + i}
                for i in range(self.n)]

    def fetch_long_short_ratio_history(self, symbol, timeframe="1h", limit=500, since=None, params=None):
        return [{"timestamp": i * H, "longShortRatio": 1.5} for i in range(self.n)]


def test_collect_writes_metrics_and_is_idempotent(db_session):
    now = datetime.fromtimestamp(3 * 3600, tz=UTC)
    collector = DerivativesCollector(db_session, exchange=_FakeExchange(n=3))
    assert collector.collect(["BTC/USDT"], now=now) == {"BTC/USDT": 9}
    # Ayni pencere tekrar cekilince yeni satir yok; pencere kayinca sadece yeniler eklenir.
    assert collector.collect(["BTC/USDT"], now=now) == {"BTC/USDT": 0}
    collector.exchange = _FakeExchange(n=4)
    assert collector.collect(["BTC/USDT"], now=now) == {"BTC/USDT": 3}

    rows = list(db_session.exec(select(MarketMetric).where(MarketMetric.metric == "open_interest")))
    assert len(rows) == 4
    cov = {(sym, metric): n for sym, metric, n, _lo, _hi in coverage(db_session)}
    assert cov[("BTC/USDT", "long_short_ratio")] == 4


def test_collect_failure_is_isolated_per_symbol(db_session):
    collector = DerivativesCollector(db_session, exchange=_FakeExchange(fail=True))
    assert collector.collect(["BTC/USDT", "ETH/USDT"]) == {"BTC/USDT": -1, "ETH/USDT": -1}


def _row(sym, ts, metric=None):
    if metric is None:  # varsayilan: butun metrikler
        return [_row(sym, ts, m)[0] for m in ("open_interest", "open_interest_value", "long_short_ratio")]
    return [{"symbol": sym, "metric": metric, "period": "1h", "ts": ts, "value": 1.0}]


def test_first_run_backfills_exchange_window(db_session):
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    fake = _FakeExchange(n=1)
    DerivativesCollector(db_session, exchange=fake).collect(["BTC/USDT"], now=now)
    # Bos DB: once ~29 gun oncesinden (geri doldurma), sonra son sayfa.
    assert fake.calls == [int((now - timedelta(days=29)).timestamp() * 1000), None]


def test_recent_data_skips_backfill(db_session):
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    upsert_metrics(db_session, _row("BTC/USDT", now - timedelta(days=29)) + _row("BTC/USDT", now - timedelta(hours=1)))
    fake = _FakeExchange(n=1)
    DerivativesCollector(db_session, exchange=fake).collect(["BTC/USDT"], now=now)
    assert fake.calls == [None]


def test_missing_head_of_window_is_backfilled(db_session):
    """Onceden yalnizca son ~21 gun toplanmissa, borsada kalan eski kisim da cekilir."""
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    upsert_metrics(db_session, _row("BTC/USDT", now - timedelta(days=21)) + _row("BTC/USDT", now - timedelta(hours=1)))
    fake = _FakeExchange(n=1)
    DerivativesCollector(db_session, exchange=fake).collect(["BTC/USDT"], now=now)
    assert fake.calls == [int((now - timedelta(days=29)).timestamp() * 1000), None]


def test_gap_backfills_from_last_row(db_session):
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    last = now - timedelta(days=25)
    upsert_metrics(db_session, _row("BTC/USDT", now - timedelta(days=29)) + _row("BTC/USDT", last))
    fake = _FakeExchange(n=1)
    DerivativesCollector(db_session, exchange=fake).collect(["BTC/USDT"], now=now)
    assert fake.calls == [int(last.timestamp() * 1000), None]


def test_purge_keeps_exactly_retention_window(db_session):
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    rows = [r for d in (0, 30, 44, 45) for r in _row("BTC/USDT", now - timedelta(days=d), "open_interest")]
    rows += _row("BTC/USDT", now - timedelta(days=45, hours=1), "open_interest")
    rows += _row("ETH/USDT", now - timedelta(days=46), "open_interest")
    upsert_metrics(db_session, rows)
    assert purge_older_than(db_session, 45, now=now) == 2  # 45g+1s ve 46g
    left = sorted((now - r.ts).days for r in db_session.exec(select(MarketMetric)))
    assert left == [0, 30, 44, 45]
    assert purge_older_than(db_session, 45, now=now) == 0


def test_gap_in_one_metric_triggers_backfill(db_session):
    """Open interest tam ama long/short eksikse de geri doldurulur."""
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    rows = _row("BTC/USDT", now - timedelta(days=29)) + _row("BTC/USDT", now - timedelta(hours=1))
    rows = [r for r in rows if not (r["metric"] == "long_short_ratio" and r["ts"] < now - timedelta(days=2))]
    rows += _row("BTC/USDT", now - timedelta(days=21), "long_short_ratio")
    upsert_metrics(db_session, rows)
    fake = _FakeExchange(n=1)
    DerivativesCollector(db_session, exchange=fake).collect(["BTC/USDT"], now=now)
    assert fake.calls == [int((now - timedelta(days=29)).timestamp() * 1000), None]
