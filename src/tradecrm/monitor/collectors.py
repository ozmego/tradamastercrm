"""Toplayici izleme modulu.

- `COLLECTORS`: sistemdeki toplayicilarin tanimi (ad, kaynak, metrikler,
  zamanlama, surec adi). Yeni bir toplayici eklendiginde buraya kaydedilir;
  panel ve API listeyi buradan okur.
- `run_cycle`: bir toplama calismasini `CollectorRun` tablosuna kaydederek
  calistirir (baslangic, sure, durum, eklenen/silinen satir, sembol hatalari).
  Hem zamanlanmis surec (`collect run`) hem `collect once` hem panelin
  "simdi calistir" dugmesi bunu kullanir.
- `collector_health`: surec durumu + calisma gecmisi + sembol bazinda veri
  tazeligi ve bosluklardan tek bir saglik degerlendirmesi uretir.
"""

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update
from sqlmodel import Session, func, select

from tradecrm.db.models import CollectorRun, MarketMetric


@dataclass(frozen=True)
class CollectorSpec:
    name: str
    title: str
    source: str
    metrics: tuple[str, ...]
    interval: timedelta  # zamanlama sikligi
    schedule_minute: int  # saat basindan sonra kacinci dakika
    process: str  # ManagedProcess adi (state/<process>.pid)
    exchange_history_days: int  # kaynak gecmisi ne kadar tutuyor
    # Son verinin beklenen en gec zamanindan bu kadar periyot geride kalirsa "bayat".
    stale_after_periods: int = 2


COLLECTORS: dict[str, CollectorSpec] = {
    "derivatives": CollectorSpec(
        name="derivatives",
        title="Turev piyasa metrikleri",
        source="Binance USDT-M (openInterestHist, globalLongShortAccountRatio)",
        metrics=("open_interest", "open_interest_value", "long_short_ratio"),
        interval=timedelta(hours=1),
        schedule_minute=2,
        process="collector",
        exchange_history_days=29,
    ),
}

# Bu sureden uzun RUNNING kalan calisma, sureci olmus kabul edilir.
STALE_RUNNING = timedelta(minutes=30)
FAILURE_STATUSES = ("FAILED", "INTERRUPTED")
# Ust uste bu kadar basarisiz calisma "failing".
FAILING_AFTER = 3


def get_spec(name: str) -> CollectorSpec:
    if name not in COLLECTORS:
        raise KeyError(name)
    return COLLECTORS[name]


def _utc(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


# --------------------------------------------------------------------- kayit
def mark_interrupted(session: Session, name: str, now: datetime | None = None) -> int:
    """STALE_RUNNING'den eski RUNNING calismalari INTERRUPTED yapar."""
    now = now or datetime.now(UTC)
    res = session.execute(
        update(CollectorRun)
        .where(CollectorRun.collector == name, CollectorRun.status == "RUNNING",
               CollectorRun.started_at < now - STALE_RUNNING)
        .values(status="INTERRUPTED", finished_at=now, error="surec calisma sirasinda durdu")
    )
    session.commit()
    return int(res.rowcount or 0)


def active_run(session: Session, name: str, now: datetime | None = None) -> CollectorRun | None:
    now = now or datetime.now(UTC)
    return session.exec(
        select(CollectorRun)
        .where(CollectorRun.collector == name, CollectorRun.status == "RUNNING",
               CollectorRun.started_at >= now - STALE_RUNNING)
        .order_by(CollectorRun.started_at.desc())
    ).first()


def purge_runs(session: Session, retention_days: int, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    res = session.execute(delete(CollectorRun).where(CollectorRun.started_at < cutoff))
    session.commit()
    return int(res.rowcount or 0)


def run_cycle(
    session: Session,
    name: str,
    collector,
    symbols: list[str],
    retention_days: int,
    trigger: str = "schedule",
    clock=time.perf_counter,
) -> CollectorRun:
    """Tek toplama calismasi: topla -> eski metrikleri sil -> kaydet. Hata
    firlatmaz; sonuc her durumda CollectorRun olarak dondurulur."""
    from tradecrm.data.collectors.derivatives import purge_older_than

    get_spec(name)
    mark_interrupted(session, name)
    run = CollectorRun(collector=name, trigger=trigger, symbols_total=len(symbols), pid=os.getpid())
    session.add(run)
    session.commit()
    session.refresh(run)
    t0 = clock()
    status, error = "FAILED", None
    added: dict[str, int] = {}
    purged = 0
    try:
        added = collector.collect(symbols)
        purged = purge_older_than(session, retention_days)
        purge_runs(session, retention_days)
        failed = sum(1 for n in added.values() if n < 0)
        status = "OK" if failed == 0 else ("FAILED" if failed == len(added) else "PARTIAL")
    except Exception as exc:  # noqa: BLE001 -- izleme kaydi her durumda yazilmali
        session.rollback()
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    run = session.get(CollectorRun, run.id) or run
    run.status = status
    run.error = error
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((clock() - t0) * 1000)
    run.rows_added = sum(n for n in added.values() if n > 0)
    run.rows_purged = purged
    run.symbols_failed = sum(1 for n in added.values() if n < 0)
    errors = getattr(collector, "last_errors", None) or {}
    run.errors = json.dumps(errors) if errors else None
    backfilled = getattr(collector, "last_backfilled", None) or []
    run.backfilled = json.dumps(backfilled) if backfilled else None
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def run_summary(run: CollectorRun) -> str:
    msg = f"{run.status}: {run.rows_added} yeni satir, {run.rows_purged} eski satir silindi"
    if run.duration_ms is not None:
        msg += f" ({run.duration_ms / 1000:.1f} sn)"
    if run.errors:
        msg += f", hatali semboller: {', '.join(json.loads(run.errors))}"
    if run.error:
        msg += f", hata: {run.error}"
    return msg


def run_to_dict(run: CollectorRun, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    started = _utc(run.started_at)
    status = run.status
    if status == "RUNNING" and started < now - STALE_RUNNING:
        status = "INTERRUPTED"
    return {
        "id": run.id, "collector": run.collector, "trigger": run.trigger, "status": status,
        "started_at": started.isoformat(),
        "finished_at": _utc(run.finished_at).isoformat() if run.finished_at else None,
        "duration_ms": run.duration_ms, "symbols_total": run.symbols_total, "symbols_failed": run.symbols_failed,
        "rows_added": run.rows_added, "rows_purged": run.rows_purged,
        "backfilled": json.loads(run.backfilled) if run.backfilled else [],
        "errors": json.loads(run.errors) if run.errors else {},
        "error": run.error, "pid": run.pid,
    }


def list_runs(session: Session, name: str, limit: int = 50, offset: int = 0,
              status: str | None = None) -> tuple[list[CollectorRun], int]:
    q = select(CollectorRun).where(CollectorRun.collector == name)
    cq = select(func.count()).select_from(CollectorRun).where(CollectorRun.collector == name)
    if status:
        q = q.where(CollectorRun.status == status)
        cq = cq.where(CollectorRun.status == status)
    rows = list(session.exec(q.order_by(CollectorRun.started_at.desc()).offset(offset).limit(limit)))
    return rows, int(session.exec(cq).one())


# --------------------------------------------------------------------- saglik
def _floor(ts: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + ((ts - epoch) // step) * step


def next_run_at(spec: CollectorSpec, now: datetime) -> datetime:
    candidate = _floor(now, timedelta(hours=1)) + timedelta(minutes=spec.schedule_minute)
    while candidate <= now:
        candidate += spec.interval
    return candidate


def freshness(session: Session, spec: CollectorSpec, symbols: list[str], now: datetime) -> list[dict]:
    """Sembol x metrik: satir, ilk/son zaman, gecikme (periyot), kayip periyot."""
    period = spec.interval
    rows = session.exec(
        select(MarketMetric.symbol, MarketMetric.metric, func.count(), func.min(MarketMetric.ts),
               func.max(MarketMetric.ts))
        .where(MarketMetric.metric.in_(spec.metrics))
        .group_by(MarketMetric.symbol, MarketMetric.metric)
    ).all()
    found = {(s, m): (n, _utc(lo), _utc(hi)) for s, m, n, lo, hi in rows}
    # Borsa, kapanan periyodun degerini bir sonraki periyotta yayinlar.
    expected_last = _floor(now, period) - period
    out = []
    all_symbols = list(dict.fromkeys([*symbols, *sorted({s for s, _ in found})]))
    for sym in all_symbols:
        for metric in spec.metrics:
            hit = found.get((sym, metric))
            if hit is None:
                out.append({"symbol": sym, "metric": metric, "configured": sym in symbols, "rows": 0,
                            "first": None, "last": None, "lag_periods": None, "missing_periods": None,
                            "status": "missing"})
                continue
            n, lo, hi = hit
            lag = max(0, int((expected_last - hi) / period))
            expected = int((hi - lo) / period) + 1
            out.append({
                "symbol": sym, "metric": metric, "configured": sym in symbols, "rows": int(n),
                "first": lo.isoformat(), "last": hi.isoformat(), "lag_periods": lag,
                "missing_periods": max(0, expected - int(n)),
                "status": "stale" if lag > spec.stale_after_periods else "ok",
            })
    return out


def collector_health(
    session: Session,
    spec: CollectorSpec,
    *,
    process_running: bool,
    process_pid: int | None,
    process_started_at: str | None,
    symbols: list[str],
    retention_days: int,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    recent, _ = list_runs(session, spec.name, limit=50)
    recent_d = [run_to_dict(r, now) for r in recent]
    last = recent_d[0] if recent_d else None
    last_ok = next((r for r in recent_d if r["status"] in ("OK", "PARTIAL")), None)
    consecutive_failures = 0
    for r in recent_d:
        if r["status"] in FAILURE_STATUSES:
            consecutive_failures += 1
        elif r["status"] != "RUNNING":
            break

    since = now - timedelta(hours=24)
    day = [r for r in recent_d if datetime.fromisoformat(r["started_at"]) >= since and r["status"] != "RUNNING"]
    durations = [r["duration_ms"] for r in day if r["duration_ms"] is not None]
    stats_24h = {
        "runs": len(day),
        "ok": sum(r["status"] == "OK" for r in day),
        "partial": sum(r["status"] == "PARTIAL" for r in day),
        "failed": sum(r["status"] in FAILURE_STATUSES for r in day),
        "rows_added": sum(r["rows_added"] for r in day),
        "rows_purged": sum(r["rows_purged"] for r in day),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
    }

    fresh = freshness(session, spec, symbols, now)
    configured = [f for f in fresh if f["configured"]]
    stale = sorted({f["symbol"] for f in configured if f["status"] == "stale"})
    missing = sorted({f["symbol"] for f in configured if f["status"] == "missing"})
    gaps = sum(f["missing_periods"] or 0 for f in configured)
    lasts = [datetime.fromisoformat(f["last"]) for f in configured if f["last"]]
    total_rows = sum(f["rows"] for f in fresh)

    issues: list[str] = []
    if not recent_d and total_rows == 0:
        status = "never"
        issues.append("Toplayici hic calismadi")
    else:
        status = "healthy"
        if consecutive_failures >= FAILING_AFTER or (configured and len(stale) + len(missing) == len(symbols)):
            status = "failing"
        elif not process_running:
            status = "stopped"
        elif stale or missing or (last and last["status"] in ("PARTIAL", *FAILURE_STATUSES)):
            status = "degraded"
        if consecutive_failures:
            issues.append(f"Ust uste {consecutive_failures} basarisiz calisma")
        if not process_running:
            issues.append(
                f"Surec calismiyor -- {spec.exchange_history_days} gunden uzun kapali kalirsa veri kaybolur"
            )
        if last and last["status"] == "PARTIAL":
            issues.append(f"Son calismada hata veren semboller: {', '.join(last['errors']) or '?'}")
        if stale:
            issues.append(f"Bayat veri: {', '.join(stale)}")
        if missing:
            issues.append(f"Hic veri yok: {', '.join(missing)}")

    return {
        "name": spec.name,
        "title": spec.title,
        "source": spec.source,
        "metrics": list(spec.metrics),
        "interval_minutes": int(spec.interval.total_seconds() // 60),
        "schedule_minute": spec.schedule_minute,
        "exchange_history_days": spec.exchange_history_days,
        "retention_days": retention_days,
        "symbols": symbols,
        "status": status,
        "issues": issues,
        "process": {"running": process_running, "pid": process_pid, "started_at": process_started_at},
        "last_run": last,
        "last_success_at": last_ok["finished_at"] if last_ok else None,
        "next_run_at": next_run_at(spec, now).isoformat() if process_running else None,
        "consecutive_failures": consecutive_failures,
        "stats_24h": stats_24h,
        "data": {
            "rows": total_rows,
            "oldest": min((f["first"] for f in fresh if f["first"]), default=None),
            "newest": max(lasts).isoformat() if lasts else None,
            "stale_symbols": stale,
            "missing_symbols": missing,
            "missing_periods": gaps,
        },
        "freshness": fresh,
        "checked_at": now.isoformat(),
    }


def health_alerts(health: dict) -> list[dict]:
    """Genel bakis uyari seridi icin."""
    level = {"failing": "critical", "stopped": "warning", "degraded": "warning"}.get(health["status"])
    if level is None:
        return []
    return [{"level": level, "account": None, "source": f"collector:{health['name']}",
             "text": f"Veri toplayici ({health['name']}): {'; '.join(health['issues']) or health['status']}"}]
