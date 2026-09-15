import logging
from datetime import datetime, timezone

from tradecrm.audit.logger import AuditLogger
from tradecrm.broker.base import BrokerAdapter
from tradecrm.notify.telegram import TelegramNotifier
from tradecrm.risk.guardrails import RiskManager
from tradecrm.risk.state import RiskStateStore
from tradecrm.risk.target_sizing import compute_position_fraction, elapsed_fraction_of_utc_day
from tradecrm.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class LiveRunner:
    """data -> strategy(+news varsa) -> risk check -> broker -> db -> audit -> notify
    akisini yurutur. Risk kontrolu hicbir adapter icin atlanamaz -- broker
    her zaman RiskManager.check_order'in onaylanmis capital_fraction'i ile
    cagrilir."""

    def __init__(
        self,
        data_provider,
        strategy: Strategy,
        risk_manager: RiskManager,
        state: RiskStateStore,
        broker: BrokerAdapter,
        notifier: TelegramNotifier,
        audit_logger: AuditLogger,
        symbol: str,
        timeframe: str,
        daily_growth_target_pct: float,
        min_position_fraction: float,
        max_position_fraction: float,
        daily_target_stop_on_hit: bool = False,
        max_daily_loss_pct: float = 0.05,
        lookback_bars: int = 200,
        is_first_live_order: bool = False,
    ) -> None:
        self.data_provider = data_provider
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.state = state
        self.broker = broker
        self.notifier = notifier
        self.audit_logger = audit_logger
        self.symbol = symbol
        self.timeframe = timeframe
        self.daily_growth_target_pct = daily_growth_target_pct
        self.min_position_fraction = min_position_fraction
        self.max_position_fraction = max_position_fraction
        self.daily_target_stop_on_hit = daily_target_stop_on_hit
        self.max_daily_loss_pct = max_daily_loss_pct
        self.lookback_bars = lookback_bars
        self.is_first_live_order = is_first_live_order

    def run_once(self) -> None:
        """Tek bir tick. Hata olursa yukari firlatir -- exception yonetimi
        icin bkz. safe_tick (scheduler bunu cagirir)."""
        today = datetime.now(timezone.utc).date()

        df = self.data_provider.fetch_latest_ohlcv(self.symbol, timeframe=self.timeframe, limit=self.lookback_bars)
        signal = self.strategy.generate_signal(df)

        realized_pnl_pct_today = self.state.get_realized_pnl_pct(today)
        self.risk_manager.check_and_maybe_trip_daily_loss(realized_pnl_pct_today)

        sizing = compute_position_fraction(
            realized_pnl_pct_today=realized_pnl_pct_today,
            elapsed_fraction_of_day=elapsed_fraction_of_utc_day(),
            daily_target_pct=self.daily_growth_target_pct,
            min_fraction=self.min_position_fraction,
            max_fraction=self.max_position_fraction,
            stop_on_hit=self.daily_target_stop_on_hit,
        )

        self.audit_logger.log(
            "signal_evaluated",
            symbol=self.symbol,
            signal=signal.value,
            realized_pnl_pct_today=realized_pnl_pct_today,
            sizing_fraction_of_pool=sizing.fraction_of_pool,
            sizing_pace_ratio=sizing.pace_ratio,
            sizing_reason=sizing.reason,
        )

        if signal is Signal.HOLD:
            return

        check = self.risk_manager.check_order(
            signal,
            proposed_pool_fraction=sizing.fraction_of_pool,
            is_first_live_order=self.is_first_live_order,
        )

        if not check.allowed:
            self.audit_logger.log("order_blocked", symbol=self.symbol, signal=signal.value, reason=check.reason)
            return

        result = self.broker.place_order(
            self.symbol,
            signal,
            check.approved_capital_fraction,
            strategy_name=self.strategy.name,
            signal_reason=sizing.reason,
        )
        self.audit_logger.log(
            "order_result",
            symbol=self.symbol,
            signal=signal.value,
            executed=result.executed,
            reason=result.reason,
            trade_id=result.trade_id,
            price=result.price,
            qty=result.qty,
        )

        if result.executed:
            self.notifier.send_message(
                f"TradeCRM [{self.broker.name}] {self.symbol} {signal.value} "
                f"@ {result.price} qty={result.qty} ({result.reason})"
            )

    def safe_tick(self) -> None:
        """Scheduler'in cagirdigi metod: bir tick'teki hata (orn. gecici
        network sorunu) tum zamanlanmis surecin cokmesine yol acmamali."""
        try:
            self.run_once()
        except Exception as exc:  # noqa: BLE001 -- kasitli genis yakalama, tek tick'i izole eder
            logger.exception("Runner tick basarisiz oldu")
            self.audit_logger.log("tick_error", symbol=self.symbol, error=str(exc))
            self.notifier.send_message(f"TradeCRM HATA ({self.symbol}): {exc}")
