"""Panelden baslatilan backtest'ler: tek worker'li kuyruk, sonuc DB'de."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import update
from sqlmodel import select

from tradecrm.backtest.service import BacktestParams, run_evaluation
from tradecrm.db.models import BacktestRun

logger = logging.getLogger(__name__)


class BacktestQueue:
    def __init__(self, session_factory, runner=run_evaluation, executor=None) -> None:
        self.session_factory = session_factory
        self.runner = runner
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")

    def mark_interrupted(self) -> int:
        """Surec yeniden basladiginda yarim kalmis isler: tekrar calistirilmaz, FAILED olur."""
        with self.session_factory() as s:
            rows = list(s.exec(select(BacktestRun).where(BacktestRun.status.in_(("QUEUED", "RUNNING")))))
            for r in rows:
                r.status, r.error, r.finished_at = "FAILED", "interrupted (sunucu yeniden basladi)", datetime.now(UTC)
                s.add(r)
            s.commit()
            return len(rows)

    def submit(self, params: BacktestParams, username: str) -> int:
        params = params.validated()  # gecersizse hemen InvalidParamsError
        with self.session_factory() as s:
            run = BacktestRun(params=json.dumps(asdict(params)), created_by=username)
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
        self.executor.submit(self._run, run_id, params)
        return run_id

    def _update(self, run_id: int, **fields) -> None:
        with self.session_factory() as s:
            run = s.get(BacktestRun, run_id)
            for k, v in fields.items():
                setattr(run, k, v)
            s.add(run)
            s.commit()

    def _claim(self, run_id: int) -> bool:
        """QUEUED -> RUNNING (atomik). Iptal edilmis is False dondurur."""
        with self.session_factory() as s:
            res = s.execute(
                update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "QUEUED")
                .values(status="RUNNING", started_at=datetime.now(UTC))
            )
            s.commit()
            return bool(res.rowcount)

    def cancel(self, run_id: int) -> bool:
        """Yalnizca siradaki (QUEUED) is iptal edilir; calisan is hesaplama
        ortasinda guvenli kesilemez. Basariliysa True."""
        with self.session_factory() as s:
            res = s.execute(
                update(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.status == "QUEUED")
                .values(status="CANCELLED", finished_at=datetime.now(UTC), error="iptal edildi")
            )
            s.commit()
            return bool(res.rowcount)

    def _run(self, run_id: int, params: BacktestParams) -> None:
        if not self._claim(run_id):
            return  # iptal edilmis
        try:
            _ev, result = self.runner(params)
            self._update(run_id, status="DONE", result=json.dumps(result), finished_at=datetime.now(UTC))
        except Exception as exc:
            logger.exception("Backtest %s basarisiz", run_id)
            self._update(run_id, status="FAILED", error=f"{type(exc).__name__}: {exc}"[:2000],
                         finished_at=datetime.now(UTC))

    def shutdown(self) -> None:
        if hasattr(self.executor, "shutdown"):
            self.executor.shutdown(wait=False, cancel_futures=True)
