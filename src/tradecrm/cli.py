from datetime import datetime, timedelta, timezone

import typer

from tradecrm.logging_conf import setup_logging

app = typer.Typer(help="TradeCRM - Binance spot trading botu")

backtest_app = typer.Typer(help="Backtest komutlari")
live_app = typer.Typer(help="Canli/paper calistirma komutlari")
risk_app = typer.Typer(help="Risk yonetimi komutlari")
telegram_app = typer.Typer(help="Telegram bildirim komutlari")
news_app = typer.Typer(help="Haber-sentiment komutlari")
dashboard_app = typer.Typer(help="Dashboard komutlari")

app.add_typer(backtest_app, name="backtest")
app.add_typer(live_app, name="live")
app.add_typer(risk_app, name="risk")
app.add_typer(telegram_app, name="telegram")
app.add_typer(news_app, name="news")
app.add_typer(dashboard_app, name="dashboard")


@app.callback()
def main() -> None:
    setup_logging()


@backtest_app.command("run")
def backtest_run(
    symbol: str = typer.Option(None, help="Orn. BTC/USDT. Bos ise .env'deki SYMBOL kullanilir."),
    timeframe: str = typer.Option(None, help="Orn. 1h, 4h. Bos ise .env'deki TIMEFRAME kullanilir."),
    days: int = typer.Option(120, help="Kaç gunluk gecmis veri cekilsin."),
    strategy: str = typer.Option("ema_rsi_crossover", help="Kullanilacak strateji adi (registry)."),
    cash: float = typer.Option(10_000, help="Baslangic sermayesi (backtest icin, kagıt uzerinde)."),
    commission: float = typer.Option(0.001, help="Islem basina komisyon orani."),
) -> None:
    """Gecmis veri uzerinde bir stratejiyi backtest eder ve ozet raporu yazdirir."""
    from tradecrm.backtest.engine import run_backtest
    from tradecrm.backtest.report import summarize
    from tradecrm.config import get_settings
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.strategy.registry import get_strategy

    settings = get_settings()
    resolved_symbol = symbol or settings.symbol
    resolved_timeframe = timeframe or settings.timeframe

    provider = CCXTDataProvider()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    df = provider.fetch_ohlcv_historical(resolved_symbol, timeframe=resolved_timeframe, since=since)
    if df.empty:
        typer.echo("Veri cekilemedi (bos DataFrame). Sembol/timeframe'i kontrol edin.")
        raise typer.Exit(code=1)

    result = run_backtest(df, lambda: get_strategy(strategy), cash=cash, commission=commission)
    typer.echo(f"Sembol: {resolved_symbol}  Zaman dilimi: {resolved_timeframe}  Bar sayisi: {len(df)}")
    typer.echo(summarize(result))


def _open_risk_state():
    from datetime import timezone as _tz

    from sqlmodel import Session

    from tradecrm.config import get_settings
    from tradecrm.db.engine import create_db_and_tables, get_engine
    from tradecrm.risk.state import RiskStateStore

    settings = get_settings()
    create_db_and_tables(settings.database_url)
    session = Session(get_engine(settings.database_url))
    return session, RiskStateStore(session), settings


@risk_app.command("status")
def risk_status() -> None:
    """Guncel risk durumunu gosterir: TRADING_ENABLED, kill-switch, bugunku gerceklesen P&L."""
    from datetime import datetime, timezone

    session, state, _settings = _open_risk_state()
    try:
        today = datetime.now(timezone.utc).date()
        typer.echo(f"TRADING_ENABLED: {state.is_trading_enabled()}")
        kill_tripped = state.is_kill_switch_tripped()
        typer.echo(f"Kill-switch tetiklendi: {kill_tripped}")
        if kill_tripped:
            typer.echo(f"  Neden: {state.kill_switch_reason}")
        typer.echo(f"Ilk canli emir onaylandi: {state.is_first_order_confirmed()}")
        typer.echo(f"Bugunku ({today}) gerceklesen P&L: {state.get_realized_pnl_pct(today):.4%}")
    finally:
        session.close()


@risk_app.command("reset-kill-switch")
def risk_reset_kill_switch() -> None:
    """Kill-switch'i manuel olarak resetler (gunluk zarar limiti asildiktan sonra)."""
    session, state, _settings = _open_risk_state()
    try:
        state.reset_kill_switch()
        typer.echo("Kill-switch resetlendi.")
    finally:
        session.close()


@risk_app.command("enable-trading")
def risk_enable_trading() -> None:
    """TRADING_ENABLED bayragini acar (varsayilan kapalidir)."""
    session, state, _settings = _open_risk_state()
    try:
        state.set_trading_enabled(True)
        typer.echo("TRADING_ENABLED = true")
    finally:
        session.close()


@risk_app.command("disable-trading")
def risk_disable_trading() -> None:
    """TRADING_ENABLED bayragini kapatir."""
    session, state, _settings = _open_risk_state()
    try:
        state.set_trading_enabled(False)
        typer.echo("TRADING_ENABLED = false")
    finally:
        session.close()


@live_app.command("run")
def live_run(
    mode: str = typer.Option("paper", help="Su an sadece 'paper' destekleniyor (Faz 10'da 'binance' eklenecek)."),
    symbol: str = typer.Option(None, help="Bos ise .env'deki SYMBOL kullanilir."),
    timeframe: str = typer.Option(None, help="Bos ise .env'deki TIMEFRAME kullanilir."),
    strategy: str = typer.Option("ema_rsi_crossover", help="Kullanilacak strateji adi (registry)."),
    initial_capital: float = typer.Option(10_000.0, help="Paper modda baslangic sermayesi."),
    run_immediately: bool = typer.Option(True, help="Baslar baslamaz bir tick calistir."),
) -> None:
    """Paper modda canli calisir: data -> strategy -> risk -> broker -> db -> audit -> notify.
    Zamanlanmis dongu, Ctrl+C ile durdurulana kadar calisir."""
    if mode != "paper":
        typer.echo("Su an sadece --mode paper destekleniyor.")
        raise typer.Exit(code=1)

    from sqlmodel import Session

    from tradecrm.audit.logger import AuditLogger
    from tradecrm.broker.paper_adapter import PaperBrokerAdapter
    from tradecrm.config import get_settings
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.db.engine import create_db_and_tables, get_engine
    from tradecrm.notify.telegram import TelegramNotifier
    from tradecrm.risk.guardrails import RiskManager
    from tradecrm.risk.state import RiskStateStore
    from tradecrm.runner.live_runner import LiveRunner
    from tradecrm.runner.scheduler import run_scheduler
    from tradecrm.strategy.registry import get_strategy

    settings = get_settings()
    resolved_symbol = symbol or settings.symbol
    resolved_timeframe = timeframe or settings.timeframe

    create_db_and_tables(settings.database_url)
    session = Session(get_engine(settings.database_url))

    provider = CCXTDataProvider()
    strategy_instance = get_strategy(strategy)
    state = RiskStateStore(session)
    risk_manager = RiskManager(
        state,
        total_capital_allocation_pct=settings.total_capital_allocation_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
    )
    broker = PaperBrokerAdapter(session, provider, initial_capital=initial_capital)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    audit_logger = AuditLogger(settings.audit_log_path)

    runner = LiveRunner(
        data_provider=provider,
        strategy=strategy_instance,
        risk_manager=risk_manager,
        state=state,
        broker=broker,
        notifier=notifier,
        audit_logger=audit_logger,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
        daily_growth_target_pct=settings.daily_growth_target_pct,
        min_position_fraction=settings.min_position_fraction,
        max_position_fraction=settings.max_position_fraction,
        daily_target_stop_on_hit=settings.daily_target_stop_on_hit,
        max_daily_loss_pct=settings.max_daily_loss_pct,
    )

    typer.echo(
        f"Paper mod baslatiliyor: {resolved_symbol} {resolved_timeframe} strateji={strategy} "
        f"TRADING_ENABLED={state.is_trading_enabled()}"
    )
    try:
        run_scheduler(runner, resolved_timeframe, run_immediately=run_immediately)
    except KeyboardInterrupt:
        typer.echo("Durduruldu.")
    finally:
        session.close()


@telegram_app.command("test")
def telegram_test() -> None:
    """Telegram kurulumunu gercek bir mesaj gondererek dogrular."""
    from tradecrm.config import get_settings
    from tradecrm.notify.telegram import TelegramNotifier

    settings = get_settings()
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    ok = notifier.send_message("TradeCRM: Telegram baglantisi calisiyor.")
    if ok:
        typer.echo("Mesaj basariyla gonderildi.")
    else:
        typer.echo("Mesaj gonderilemedi. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID degerlerini kontrol edin.")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
