import logging
from datetime import UTC, datetime

import pandas as pd

from tradecrm.audit.logger import AuditLogger
from tradecrm.broker.base import AccountSnapshot, BrokerAdapter
from tradecrm.broker.order_manager import OrderManager
from tradecrm.data.provider import timeframe_to_timedelta
from tradecrm.db.repository import _as_utc
from tradecrm.notify.telegram import TelegramNotifier
from tradecrm.risk import exits as ex
from tradecrm.risk.breakers import BreakerConfig, ClosedTradeLite, evaluate_entry
from tradecrm.risk.data_checks import last_bar_is_valid, validate_market_data
from tradecrm.risk.guardrails import RiskManager
from tradecrm.risk.position_sizing import SizingConfig, compute_total_fraction
from tradecrm.risk.state import RiskStateStore
from tradecrm.risk.target_sizing import elapsed_fraction_of_utc_day
from tradecrm.strategy import indicators as ind
from tradecrm.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class LiveRunner:
    """uzlastirma -> data (kapanmis mumlar) -> veri kontrolleri -> strategy ->
    cikis katmani -> devre kesiciler -> risk check -> OrderManager -> broker
    -> db -> audit -> notify.

    Belirsizlikte (bayat/gecersiz veri, kesinlesmemis emir, hesap durumu
    alinamadi) yeni alim YAPILMAZ; cikis yolu her zaman acik kalir. Her
    emir (hesap, sembol, yon, mum) ile tekildir: ayni mum icin ikinci emir
    olusmaz.

    Kurallar backtest simulatoruyle (backtest/simulator.py) AYNI saf
    fonksiyonlardan gecer: risk/exits.py, risk/position_sizing.py,
    risk/breakers.py. Risk kontrolu hicbir adapter icin atlanamaz.

    Bilinen fark: simulator stop'u stop fiyatindan doldurur; paper modda
    stop tetiklendiginde piyasa fiyatindan satilir (bir sonraki tick).
    Canli (Faz 10) adapter borsada gercek stop emri kullanmalidir."""

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
        sizing: SizingConfig | None = None,
        exits: ex.ExitConfig | None = None,
        breakers: BreakerConfig | None = None,
        lookback_bars: int = 1000,
        is_first_live_order: bool | None = None,
        session=None,
        enricher=None,
        order_manager: OrderManager | None = None,
        notice_board: set[str] | None = None,
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
        self.sizing = sizing or SizingConfig(mode="fixed")
        self.exits = exits
        self.breakers = breakers
        self.lookback_bars = lookback_bars
        # Varsayilan: paper disindaki her broker icin ilk emir manuel onay ister.
        self.is_first_live_order = (
            is_first_live_order
            if is_first_live_order is not None
            else not str(getattr(broker, "name", "")).startswith("paper")
        )
        self.session = session
        # df -> df: ek veri kaynaklarini kolon olarak ekler (backtest'teki enrich ile ayni).
        self.enricher = enricher
        self._tf = timeframe_to_timedelta(timeframe)
        if order_manager is None:
            if session is None:
                raise ValueError("order_manager ya da session verilmeli")
            from tradecrm.db.repository import OrderRepository

            order_manager = OrderManager(broker, OrderRepository(session))
        self.order_manager = order_manager
        # Ayni hesabi paylasan runner'lar (coklu sembol) ayni seti verir:
        # hesap belirsizligi sembol sayisi kadar degil, bir kez bildirilir.
        self._notice_board = notice_board if notice_board is not None else set()

    # ------------------------------------------------------------------ tick
    def run_once(self) -> None:
        """Tek bir tick. Hata olursa yukari firlatir -- bkz. safe_tick."""
        now = datetime.now(UTC)
        today = now.date()

        # 1) Uzlastirma: yarim kalmis/belirsiz emirler borsaya sorulur.
        report = self.order_manager.reconcile(now)
        if report.resolved:
            self.audit_logger.log("orders_reconciled", symbol=self.symbol, resolved=report.resolved)
        uncertain: list[str] = [f"account_uncertain ({report.summary()})"] if report.uncertain else []

        # 2) Veri (sadece kapanmis mumlar) + kontroller.
        df = self.data_provider.fetch_latest_ohlcv(self.symbol, timeframe=self.timeframe, limit=self.lookback_bars)
        if df is None or len(df) == 0:
            self.audit_logger.log("no_data", symbol=self.symbol)
            return
        if self.enricher is not None:
            df = self.enricher(df)
        bar_time = df.index[-1].to_pydatetime()
        signal = self.strategy.generate_signal(df)
        last_close = float(df["Close"].iloc[-1])

        ticker = None
        problems: list[str] = []
        try:
            ticker = float(self.data_provider.fetch_latest_price(self.symbol))
        except Exception as exc:  # noqa: BLE001 -- fiyat yoksa sadece alim durur
            problems.append(f"ticker_unavailable ({type(exc).__name__})")
        problems += validate_market_data(df, self.timeframe, now, ticker_price=ticker)

        # 3) Hesap durumu: nakit / acik pozisyon maliyeti / piyasa degeri ayri.
        snapshot: AccountSnapshot | None = None
        try:
            snapshot = self.broker.get_snapshot()
        except Exception as exc:  # noqa: BLE001
            uncertain.append(f"snapshot_failed ({type(exc).__name__})")

        # 4) Gunluk zarar: gerceklesen + TUM acik pozisyonlarin gerceklesmemis zarari.
        realized_today = self.state.get_realized_pnl_pct(today)
        unrealized_pct = 0.0
        if snapshot is not None and snapshot.realized_equity > 0:
            unrealized_pct = snapshot.unrealized_pnl / snapshot.realized_equity
            self.risk_manager.check_and_maybe_trip_daily_loss(realized_today + unrealized_pct)

        position = self.broker.get_open_position(self.symbol)
        self.audit_logger.log(
            "signal_evaluated",
            symbol=self.symbol,
            account=self.broker.name,
            bar_time=str(df.index[-1]),
            signal=signal.value,
            close=last_close,
            ticker=ticker,
            in_position=position is not None,
            realized_pnl_pct_today=realized_today,
            unrealized_pnl_pct=unrealized_pct,
            cash=snapshot.cash if snapshot else None,
            allocated=snapshot.allocated if snapshot else None,
            equity=snapshot.equity if snapshot else None,
            data_problems=problems or None,
            uncertain=uncertain or None,
        )
        self._notify_uncertainty(uncertain)

        # 5) Cikis yolu: her zaman acik.
        if position is not None:
            exit_reason = None
            if last_bar_is_valid(df):
                exit_reason = self._manage_open_position(position, df)
            else:
                self.audit_logger.log("exit_check_skipped", symbol=self.symbol, reason="invalid_last_bar")
            if exit_reason is None and signal is Signal.SELL:
                exit_reason = "signal"
            if exit_reason is not None:
                self._exit(exit_reason, bar_time)
            return

        # 6) Alim: veri ve hesap durumu kesin olmali.
        if signal is Signal.BUY:
            blockers = problems + uncertain
            if blockers:
                self.audit_logger.log("order_blocked", symbol=self.symbol, signal="BUY", reason="; ".join(blockers))
                return
            self._enter(df, ticker or last_close, snapshot, realized_today, now, bar_time)

    def _notify_uncertainty(self, uncertain: list[str]) -> None:
        """Belirsizlik durumuna GIRIS ve CIKIS'ta hesap basina bir kez bildirir (spam yok)."""
        key = str(self.broker.name)
        was_uncertain = key in self._notice_board
        if uncertain and not was_uncertain:
            self._notice_board.add(key)
            self.notifier.send_message(f"TradeCRM [{key}] yeni alimlar durdu: {'; '.join(uncertain)}")
        elif not uncertain and was_uncertain:
            self._notice_board.discard(key)
            self.notifier.send_message(f"TradeCRM [{key}] hesap durumu tekrar kesin; alimlar serbest.")

    # -------------------------------------------------------------- entries
    def _enter(
        self, df: pd.DataFrame, ref_price: float, snapshot: AccountSnapshot, realized_today: float,
        now: datetime, bar_time: datetime,
    ) -> None:
        if self.breakers is not None:
            result = self._evaluate_breakers(now, snapshot)
            if not result.allowed:
                if result.hard_stop and not self.state.is_kill_switch_tripped():
                    self.state.trip_kill_switch(reason=result.reason)
                    self.notifier.send_message(f"TradeCRM KILL-SWITCH [{self.broker.name}] {self.symbol}: {result.reason}")
                self.audit_logger.log("order_blocked", symbol=self.symbol, signal="BUY", reason=result.reason)
                return

        atr_value = None
        stop_estimate = None
        if self.exits is not None:
            atr_value = float(ind.atr(df, self.exits.atr_period).iloc[-1])
            if not (atr_value > 0):
                self.audit_logger.log("order_blocked", symbol=self.symbol, signal="BUY", reason="invalid_atr")
                return
            stop_estimate = ex.initial_stop_price(ref_price, atr_value, self.exits)

        total_fraction, sizing_reason = compute_total_fraction(
            self.sizing,
            entry_price=ref_price,
            stop_price=stop_estimate,
            realized_pnl_pct_today=realized_today,
            elapsed_fraction_of_day=elapsed_fraction_of_utc_day(now),
        )
        check = self.risk_manager.check_buy(
            snapshot, desired_notional=total_fraction * snapshot.equity, is_first_live_order=self.is_first_live_order
        )
        if not check.allowed:
            self.audit_logger.log("order_blocked", symbol=self.symbol, signal="BUY", reason=check.reason)
            return

        execution = self.order_manager.execute(
            self.symbol, Signal.BUY, bar_time, strategy_name=self.strategy.name,
            notional=check.approved_notional, reason=sizing_reason,
        )
        result = execution.result
        levels = None
        if execution.executed and self.exits is not None and result.price:
            levels = ex.initial_levels(result.price, atr_value, self.exits)
            self.state.update_exit_levels(
                result.trade_id,
                stop_price=levels.stop,
                take_profit_price=levels.take_profit,
                highest_price=levels.highest,
                initial_stop_price=levels.initial_stop,
            )
        self.audit_logger.log(
            "order_result", symbol=self.symbol, signal="BUY", status=execution.status,
            client_order_id=execution.client_order_id, reason=result.reason if result else execution.error,
            trade_id=result.trade_id if result else None, price=result.price if result else None,
            qty=result.qty if result else None, notional=check.approved_notional, risk_reason=check.reason,
            stop=levels.stop if levels else None, take_profit=levels.take_profit if levels else None,
        )
        if execution.executed:
            stop_txt = f" stop={levels.stop:.2f}" if levels else ""
            self.notifier.send_message(
                f"TradeCRM [{self.broker.name}] {self.symbol} BUY @ {result.price} qty={result.qty:.6f} "
                f"({check.approved_notional:,.2f} USDT){stop_txt}"
            )
        elif execution.status == "UNKNOWN":
            self.notifier.send_message(
                f"TradeCRM [{self.broker.name}] {self.symbol} BUY emri BELIRSIZ ({execution.client_order_id}): "
                f"{execution.error}. Uzlastirilana kadar yeni alim yok."
            )

    def _evaluate_breakers(self, now: datetime, snapshot: AccountSnapshot):
        trades = self.state.get_closed_trades_since_last_reset()
        lite = [
            ClosedTradeLite(_as_utc(t.exit_time), t.realized_pnl or 0.0, t.realized_pnl_pct or 0.0)
            for t in trades
            if t.exit_time is not None
        ]
        # Gerceklesen equity (nakit + maliyet): simulatordeki tanimla ayni.
        initial_equity = snapshot.realized_equity - sum(t.pnl for t in lite)
        # Gunluk zarar/hedef: acik pozisyonlarin gerceklesmemis K/Z'si dahil (gunluk zarar kill-switch'i ile ayni tanim).
        unrealized_pct = snapshot.unrealized_pnl / snapshot.realized_equity if snapshot.realized_equity > 0 else 0.0
        return evaluate_entry(lite, now, initial_equity, self.breakers, unrealized_pnl_pct=unrealized_pct)

    # ---------------------------------------------------------------- exits
    def _manage_open_position(self, trade, df: pd.DataFrame) -> str | None:
        """Cikis seviyelerini, giristen sonra kapanan TUM barlar uzerinden
        bastan yeniden hesaplar (simulatorle ayni sira). Durumsuz ve
        idempotent: kacirilan tick'ler/yeniden baslatmalar bar atlatmaz."""
        if self.exits is None:
            return None

        atr_series = ind.atr(df, self.exits.atr_period)
        entry_time = pd.Timestamp(_as_utc(trade.entry_time))

        if trade.initial_stop_price is None:
            # Cikis katmani acilmadan once acilmis pozisyon: simdiki ATR ile baslat.
            atr_now = float(atr_series.iloc[-1])
            if not (atr_now > 0):
                return None
            levels = ex.initial_levels(trade.entry_price, atr_now, self.exits)
            atr_at_entry = atr_now
        else:
            atr_at_entry = (trade.entry_price - trade.initial_stop_price) / self.exits.stop_atr_mult
            levels = ex.initial_levels(trade.entry_price, atr_at_entry, self.exits)
            if trade.take_profit_price is not None or self.exits.take_profit_atr_mult is None:
                levels = ex.PositionLevels(
                    levels.entry_price, levels.initial_stop, levels.stop, trade.take_profit_price, levels.highest
                )

        after_entry = df.index + self._tf > entry_time  # giristen sonra kapanan barlar (giris bari dahil)
        bars = df[after_entry]
        atrs = atr_series[after_entry]
        reason = None
        for bars_held, ((_, bar), atr_value) in enumerate(zip(bars.iterrows(), atrs, strict=True), start=1):
            decision = ex.check_bar_exit(levels, bar["Open"], bar["High"], bar["Low"])
            if decision is not None:
                reason = decision.reason
                break
            levels = ex.update_on_close(levels, bar["High"], atr_value, self.exits)
            if ex.time_stop_due(bars_held, self.exits):
                reason = "time_stop"
                break

        if (levels.stop, levels.highest) != (trade.stop_price, trade.highest_price):
            self.state.update_exit_levels(
                trade.id, stop_price=levels.stop, take_profit_price=levels.take_profit,
                highest_price=levels.highest, initial_stop_price=levels.initial_stop,
            )
        return reason

    def _exit(self, reason: str, bar_time: datetime) -> None:
        check = self.risk_manager.check_order(Signal.SELL)
        if not check.allowed:  # SELL her zaman izinli; savunma amacli
            self.audit_logger.log("order_blocked", symbol=self.symbol, signal="SELL", reason=check.reason)
            return
        execution = self.order_manager.execute(
            self.symbol, Signal.SELL, bar_time, strategy_name=self.strategy.name, reason=reason
        )
        result = execution.result
        self.audit_logger.log(
            "order_result", symbol=self.symbol, signal="SELL", status=execution.status,
            client_order_id=execution.client_order_id, exit_reason=reason,
            reason=result.reason if result else execution.error,
            trade_id=result.trade_id if result else None, price=result.price if result else None,
            qty=result.qty if result else None,
        )
        if execution.executed:
            self.notifier.send_message(
                f"TradeCRM [{self.broker.name}] {self.symbol} SELL ({reason}) @ {result.price} qty={result.qty:.6f}"
            )
        elif execution.status == "UNKNOWN":
            self.notifier.send_message(
                f"TradeCRM [{self.broker.name}] {self.symbol} SELL emri BELIRSIZ ({execution.client_order_id}): "
                f"{execution.error}"
            )

    # ------------------------------------------------------------ safe tick
    def safe_tick(self) -> None:
        """Scheduler'in cagirdigi metod: bir tick'teki hata tum sureci
        cokertmemeli. DB hatasindan sonra session rollback edilir; aksi halde
        session 'PendingRollbackError' durumunda kalir ve sonraki BUTUN
        tick'ler basarisiz olur."""
        try:
            self.run_once()
        except Exception as exc:
            logger.exception("Runner tick basarisiz oldu")
            if self.session is not None:
                try:
                    self.session.rollback()
                except Exception:
                    logger.exception("Session rollback basarisiz")
            self.audit_logger.log("tick_error", symbol=self.symbol, error=f"{type(exc).__name__}: {exc}")
            self.notifier.send_message(f"TradeCRM HATA ({self.symbol}): {type(exc).__name__}: {exc}")


class MultiRunner:
    """Birden cok LiveRunner'i tek tick'te calistirir: portfoy uyeleri (her
    biri kendi alt hesabinda) ve/veya coklu sembol (ayni hesapta sembol basina
    bir runner; nakit, toplam pozisyon tavani (profil), risk bayraklari ve gunluk zarar
    hesap genelinde paylasilir). Her runner kendi hatasini izole eder."""

    def __init__(self, runners: list) -> None:  # LiveRunner ya da safe_tick'i olan izleyici (DriftGuard)
        self.runners = runners

    def safe_tick(self) -> None:
        for runner in self.runners:
            runner.safe_tick()
