"""Hesap basina equity kaydi: runner'lardan sonra MultiRunner uyesi olarak calisir."""

import logging

from tradecrm.db.repository import EquityRepository

logger = logging.getLogger(__name__)


class EquityRecorder:
    def __init__(self, broker, session, audit_logger) -> None:
        self.broker = broker
        self.session = session
        self.audit_logger = audit_logger

    def run_once(self) -> None:
        snapshot = self.broker.get_snapshot()
        EquityRepository(self.session).add(str(self.broker.name), snapshot)

    def safe_tick(self) -> None:
        """Kritik degil: hata trading'i etkilemez, sadece audit'e yazilir."""
        try:
            self.run_once()
        except Exception as exc:  # izleme hatasi trading'i durdurmamali
            logger.exception("Equity kaydi basarisiz")
            try:
                self.session.rollback()
            except Exception:
                logger.exception("Session rollback basarisiz")
            self.audit_logger.log("equity_snapshot_error", account=str(self.broker.name),
                                  error=f"{type(exc).__name__}: {exc}")
