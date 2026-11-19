"""Emirlerin kalici, tekrarsiz (idempotent) yurutulmesi ve uzlastirma.

Akis (execute):
  1. reserve: Order(NEW) DB'ye yazilir. (hesap, sembol, yon, mum) zaten
     varsa -> "duplicate" (ayni mum icin ikinci emir yok; yeniden baslatma
     ve eszamanli runner'lar dahil). DB yazilamazsa emir GONDERILMEZ.
  2. submit: broker.submit_order(client_order_id, ...).
     - Basari/ret -> FILLED / REJECTED.
     - Zaman asimi/ag hatasi -> emir borsada olusmus olabilir: AYNI kimlikle
       get_order sorgulanir. Bulunursa kaydedilir; borsa "yok" derse ayni
       kimlikle bir kez daha gonderilir (kimlik ayni oldugu icin borsa
       cift emir olusturmaz). Hala belirsizse -> UNKNOWN.
  3. reconcile (her tick basinda + baslangicta): NEW/UNKNOWN emirler
     borsaya sorulur ve kesinlestirilir; pozisyon/bakiye uyusmazliklari
     raporlanir. Kesinlesmeyen emir ya da uyusmazlik varken hesap
     "belirsiz" sayilir -> yeni alim yok, satis serbest.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tradecrm.broker.base import (
    BrokerAdapter,
    BrokerError,
    BrokerTimeoutError,
    OrderResult,
)
from tradecrm.db.repository import DuplicateOrderError, OrderRepository
from tradecrm.strategy.base import Signal

logger = logging.getLogger(__name__)

# Gonderilmeden yarim kalmis NEW emir bu sureden eskiyse ve borsada yoksa terk edilir.
STALE_NEW_ORDER_AGE = timedelta(minutes=2)


@dataclass
class ExecutionResult:
    status: str  # FILLED | REJECTED | UNKNOWN | DUPLICATE
    client_order_id: str | None
    result: OrderResult | None = None
    error: str | None = None

    @property
    def executed(self) -> bool:
        return self.status == "FILLED"


@dataclass
class ReconcileReport:
    resolved: list[str] = field(default_factory=list)
    still_unknown: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def uncertain(self) -> bool:
        return bool(self.still_unknown or self.mismatches or self.errors)

    def summary(self) -> str:
        parts = []
        if self.still_unknown:
            parts.append(f"belirsiz emir: {len(self.still_unknown)}")
        if self.mismatches:
            parts.append("uyusmazlik: " + "; ".join(self.mismatches))
        if self.errors:
            parts.append("hata: " + "; ".join(self.errors))
        return ", ".join(parts) or "ok"


class OrderManager:
    def __init__(self, broker: BrokerAdapter, orders: OrderRepository, max_submit_attempts: int = 2) -> None:
        self.broker = broker
        self.orders = orders
        self.max_submit_attempts = max_submit_attempts

    @property
    def account(self) -> str:
        return self.broker.name

    def execute(
        self,
        symbol: str,
        side: Signal,
        bar_time: datetime,
        strategy_name: str,
        notional: float | None = None,
        reason: str | None = None,
    ) -> ExecutionResult:
        try:
            order = self.orders.reserve(self.account, symbol, side.value, bar_time, reason, notional)
        except DuplicateOrderError:
            return ExecutionResult("DUPLICATE", None, error="ayni mum icin emir zaten var")

        coid = order.client_order_id
        last_error = None
        for attempt in range(1, self.max_submit_attempts + 1):
            self.orders.update(order, attempts=attempt)
            try:
                result = self.broker.submit_order(coid, symbol, side, notional, strategy_name, reason)
            except BrokerTimeoutError as exc:
                last_error = f"timeout: {exc}"
                logger.warning("Emir %s zaman asimi (deneme %d); borsaya soruluyor", coid, attempt)
                try:
                    found = self.broker.get_order(coid)
                except Exception as qexc:  # noqa: BLE001 -- sorgu da basarisiz: sonuc belirsiz
                    self.orders.update(order, status="UNKNOWN", error=f"{last_error}; sorgu: {qexc}")
                    return ExecutionResult("UNKNOWN", coid, error=f"{last_error}; sorgu: {qexc}")
                if found is not None:
                    return self._record(order, found)
                continue  # borsa emri gormemis -> AYNI kimlikle tekrar dene
            except BrokerError as exc:
                self.orders.update(order, status="REJECTED", error=str(exc))
                return ExecutionResult("REJECTED", coid, error=str(exc))
            except Exception as exc:
                # Beklenmeyen hata: emrin borsaya ulasip ulasmadigi bilinmiyor.
                self.orders.update(order, status="UNKNOWN", error=f"{type(exc).__name__}: {exc}")
                raise
            return self._record(order, result)

        self.orders.update(order, status="UNKNOWN", error=last_error)
        return ExecutionResult("UNKNOWN", coid, error=last_error)

    def _record(self, order, result: OrderResult) -> ExecutionResult:
        status = "FILLED" if result.executed else "REJECTED"
        self.orders.update(
            order,
            status=status,
            filled_qty=result.qty,
            avg_price=result.price,
            trade_id=result.trade_id,
            exchange_order_id=result.exchange_order_id,
            error=None if result.executed else result.reason,
        )
        return ExecutionResult(status, order.client_order_id, result)

    def reconcile(self, now: datetime | None = None) -> ReconcileReport:
        now = now or datetime.now(UTC)
        report = ReconcileReport()
        for order in self.orders.unresolved(self.account):
            try:
                found = self.broker.get_order(order.client_order_id)
            except Exception as exc:  # noqa: BLE001 -- uzlastirma tick'i durdurmamali
                report.errors.append(f"{order.client_order_id}: {type(exc).__name__}")
                report.still_unknown.append(order.client_order_id)
                continue
            if found is not None:
                self._record(order, found)
                report.resolved.append(order.client_order_id)
                continue
            created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=UTC)
            if order.status == "UNKNOWN" or now - created > STALE_NEW_ORDER_AGE:
                # Borsa emri hic gormemis: guvenle terk edilebilir (yeniden gonderilmez;
                # sinyal bayatladi, bir sonraki mumda yeni karar verilir).
                self.orders.update(order, status="ABANDONED", error=(order.error or "") + " | borsada yok")
                report.resolved.append(order.client_order_id)
            else:
                report.still_unknown.append(order.client_order_id)  # baska bir surec su an gonderiyor olabilir
        try:
            report.mismatches.extend(self.broker.reconcile_positions())
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"pozisyon uzlastirma: {type(exc).__name__}")
        return report
