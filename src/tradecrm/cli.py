from datetime import UTC, datetime, timedelta

import typer

from tradecrm.logging_conf import setup_logging

app = typer.Typer(help="TradeCRM - Binance spot trading botu")

backtest_app = typer.Typer(help="Backtest komutlari")
live_app = typer.Typer(help="Canli/paper calistirma komutlari")
risk_app = typer.Typer(help="Risk yonetimi komutlari")
telegram_app = typer.Typer(help="Telegram bildirim komutlari")
news_app = typer.Typer(help="Haber-sentiment komutlari")
dashboard_app = typer.Typer(help="(Eski ad) admin paneli")
admin_app = typer.Typer(help="Admin paneli ve kullanicilari")
collect_app = typer.Typer(help="Veri toplayicilari (open interest, long/short orani)")

app.add_typer(backtest_app, name="backtest")
app.add_typer(live_app, name="live")
app.add_typer(risk_app, name="risk")
app.add_typer(telegram_app, name="telegram")
app.add_typer(news_app, name="news")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(admin_app, name="admin")
app.add_typer(collect_app, name="collect")
autostart_app = typer.Typer(help="Windows oturum acilisinda otomatik baslatma (collector | live | admin)")
app.add_typer(autostart_app, name="autostart")
report_app = typer.Typer(help="Performans raporlari (Oneri 13)")
app.add_typer(report_app, name="report")


@app.callback()
def main() -> None:
    setup_logging()


def _load_datasets(symbols: list[str], timeframe: str, days: int, sources: set[str] | None = None) -> dict:
    from tradecrm.backtest.service import NoDataError, load_datasets

    try:
        return load_datasets(symbols, timeframe, days, sources, log=typer.echo)
    except NoDataError:
        typer.echo("Hic veri yok.")
        raise typer.Exit(code=1) from None


def _bars(days: int, timeframe: str) -> int:
    from tradecrm.backtest.service import bars

    return bars(days, timeframe)


@backtest_app.command("run")
def backtest_run(
    symbol: str = typer.Option(None, help="Orn. BTC/USDT. Bos ise .env'deki SYMBOL kullanilir."),
    timeframe: str = typer.Option(None, help="Orn. 1h, 4h. Bos ise .env'deki TIMEFRAME kullanilir."),
    days: int = typer.Option(365, help="Kac gunluk gecmis veri cekilsin."),
    profile: str = typer.Option(None, help="Profil adi (strategy/profiles.py). Bos ise .env'deki PROFILE."),
    cash: float = typer.Option(10_000, help="Baslangic sermayesi (kagit uzerinde)."),
    commission: float = typer.Option(0.001, help="Islem basina komisyon orani."),
    slippage_bps: float = typer.Option(5.0, help="Dolum basina aleyhte kayma (baz puan)."),
) -> None:
    """Tek bir donemde profili simule eder (hizli kontrol -- kanit DEGIL; kanit icin walk-forward)."""
    from tradecrm.backtest.metrics import compute_metrics
    from tradecrm.backtest.report import format_metrics
    from tradecrm.backtest.simulator import config_from_profile, simulate
    from tradecrm.config import get_settings
    from tradecrm.strategy.profiles import get_profile

    settings = get_settings()
    resolved_symbol = symbol or settings.symbol
    resolved_timeframe = timeframe or settings.timeframe
    prof = get_profile(profile or settings.profile)
    df = _load_datasets([resolved_symbol], resolved_timeframe, days, prof.required_sources)[resolved_symbol]

    cfg = config_from_profile(prof, initial_cash=cash, fee_rate=commission, slippage_bps=slippage_bps)
    res = simulate(df, prof.build_strategy(), cfg)
    typer.echo(f"Profil: {prof.name} -- {prof.description}")
    typer.echo(format_metrics(compute_metrics(res.trades, res.equity)))
    if res.blocked:
        typer.echo(f"Engellenen girisler: {res.blocked}")
    buy_hold = df["Close"].iloc[-1] / df["Close"].iloc[0] - 1
    typer.echo(f"Al-tut getirisi: {buy_hold:+.1%}")


@backtest_app.command("walk-forward")
def backtest_walk_forward(
    profile: str = typer.Option(None, help="Profil adi. Bos ise .env'deki PROFILE."),
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT", help="Virgulle ayrilmis semboller."),
    timeframe: str = typer.Option(None, help="Bos ise .env'deki TIMEFRAME."),
    days: int = typer.Option(1095, help="Toplam gecmis (gun). Son %30 holdout olarak ayrilir."),
    train_days: int = typer.Option(180, help="Egitim penceresi (gun)."),
    test_days: int = typer.Option(60, help="Test penceresi (gun)."),
    optimize: bool = typer.Option(False, help="Her pencerede parametreleri egitim verisinde sec."),
    sensitivity: bool = typer.Option(True, help="+-%20 parametre hassasiyet analizi."),
    slippage_bps: float = typer.Option(5.0, help="Dolum basina kayma (baz puan)."),
    save_baseline: bool = typer.Option(
        False, help="OOS islem getirilerini drift alarmi icin kaydet (state/baselines/)."
    ),
) -> None:
    """Out-of-sample walk-forward + Monte Carlo + onay kapisi (Oneri 0)."""
    from tradecrm.backtest.evaluation import evaluate
    from tradecrm.backtest.walk_forward import DEFAULT_WARMUP_BARS
    from tradecrm.config import get_settings
    from tradecrm.strategy.profiles import resolve

    settings = get_settings()
    tf = timeframe or settings.timeframe
    prof = resolve(profile or settings.profile)
    datasets = _load_datasets([x.strip() for x in symbols.split(",") if x.strip()], tf, days, prof.required_sources)
    ev = evaluate(
        datasets, prof, train_bars=_bars(train_days, tf), test_bars=_bars(test_days, tf),
        warmup_bars=DEFAULT_WARMUP_BARS, optimize=optimize, run_sensitivity=sensitivity, slippage_bps=slippage_bps,
    )
    _print_evaluation(ev)
    if save_baseline:
        _save_baseline(ev, tf)


def _save_baseline(ev, timeframe: str) -> None:
    from datetime import UTC, datetime

    import pandas as pd

    from tradecrm.monitor.drift import Baseline, save_baseline
    from tradecrm.strategy.profiles import Portfolio

    if isinstance(ev.profile, Portfolio):
        typer.echo("Baseline portfoy icin degil, her uye profil icin ayri kaydedilir (canlida uyeler ayri hesaptir).")
        raise typer.Exit(code=1)
    parts = [wf.trades for wf in ev.per_symbol.values() if len(wf.trades)]
    trades = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["return_pct"])
    path = save_baseline(Baseline(
        profile=ev.profile.name, timeframe=timeframe, symbols=list(ev.per_symbol),
        returns=[float(x) for x in trades["return_pct"]], created_at=datetime.now(UTC).isoformat(),
        metrics=ev.pooled.as_dict(), gate_passed=ev.gate.passed,
    ))
    typer.echo(f"Baseline kaydedildi: {path} ({len(trades)} OOS islem, kapi={'GECTI' if ev.gate.passed else 'KALDI'})")


@backtest_app.command("compare")
def backtest_compare(
    profiles: str = typer.Option(
        "baseline,exits,exits_risk,guarded",
        help="Virgulle ayrilmis profiller. 'a+b' esit agirlikli portfoy, 'a:0.6+b:0.4' agirlikli.",
    ),
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT", help="Virgulle ayrilmis semboller."),
    timeframe: str = typer.Option(None, help="Bos ise .env'deki TIMEFRAME."),
    days: int = typer.Option(1095, help="Toplam gecmis (gun). Son %30 holdout olarak ayrilir."),
    train_days: int = typer.Option(180),
    test_days: int = typer.Option(60),
    optimize: bool = typer.Option(False),
    sensitivity: bool = typer.Option(False, help="Hassasiyet analizi (yavas)."),
    slippage_bps: float = typer.Option(5.0),
) -> None:
    """Birden cok profili AYNI walk-forward duzeneginde yan yana karsilastirir."""
    from tradecrm.backtest.evaluation import evaluate
    from tradecrm.backtest.report import format_table
    from tradecrm.backtest.walk_forward import DEFAULT_WARMUP_BARS
    from tradecrm.config import get_settings
    from tradecrm.strategy.profiles import resolve

    settings = get_settings()
    tf = timeframe or settings.timeframe
    profile_objs = [resolve(x.strip()) for x in profiles.split(",") if x.strip()]
    sources = set().union(*(p.required_sources for p in profile_objs))
    datasets = _load_datasets([x.strip() for x in symbols.split(",") if x.strip()], tf, days, sources)
    rows = []
    for prof_obj in profile_objs:
        name = prof_obj.name
        ev = evaluate(
            datasets, prof_obj, train_bars=_bars(train_days, tf), test_bars=_bars(test_days, tf),
            warmup_bars=DEFAULT_WARMUP_BARS, optimize=optimize, run_sensitivity=sensitivity,
            slippage_bps=slippage_bps,
        )
        m, mc = ev.pooled, ev.monte_carlo
        rows.append({
            "profil": name, "islem": m.n_trades, "kazanma": m.win_rate, "pf": m.profit_factor,
            "beklenti": m.expectancy_pct, "getiri": m.total_return, "maxdd": m.max_drawdown,
            "sharpe": m.sharpe, "mc_dd95": mc.max_dd_p95, "p_zarar": mc.prob_loss,
            "kapi": "GECTI" if ev.gate.passed else f"{len(ev.gate.failures)} hata",
        })
    typer.echo("")
    typer.echo("Out-of-sample (walk-forward, holdout haric, esit agirlikli semboller):")
    typer.echo(format_table(rows, [
        ("profil", "profil", ""), ("islem", "islem", ""), ("kazanma", "kazanma", ".0%"), ("PF", "pf", ".2f"),
        ("beklenti", "beklenti", "+.3%"), ("getiri", "getiri", "+.1%"), ("maxDD", "maxdd", ".1%"),
        ("Sharpe", "sharpe", ".2f"), ("MC DD p95", "mc_dd95", ".1%"), ("P(zarar)", "p_zarar", ".0%"),
        ("kapi", "kapi", ""),
    ]))


@backtest_app.command("filter-test")
def backtest_filter_test(
    base: str = typer.Option("donchian_fng", help="Taban profil."),
    filters: str = typer.Option("volume,vol_cap,confirm", help="Tek tek denenecek filtreler (varsayilan parametreler)."),
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT", help="Virgulle ayrilmis semboller."),
    timeframe: str = typer.Option(None, help="Bos ise .env'deki TIMEFRAME."),
    days: int = typer.Option(1095, help="Toplam gecmis (gun). Son %30 holdout olarak ayrilir."),
    train_days: int = typer.Option(180),
    test_days: int = typer.Option(60),
    alpha: float = typer.Option(0.05, help="Anlamlilik duzeyi (filtre sayisina gore Bonferroni duzeltilir)."),
    slippage_bps: float = typer.Option(5.0),
) -> None:
    """Oneri 6: her filtrenin tabana katkisini TEK TEK olcer; anlamli olmayani eklemeyin."""
    from dataclasses import replace

    from tradecrm.backtest.evaluation import evaluate
    from tradecrm.backtest.filter_test import attribute_filter
    from tradecrm.backtest.report import format_table
    from tradecrm.backtest.walk_forward import DEFAULT_WARMUP_BARS, split_holdout
    from tradecrm.config import get_settings
    from tradecrm.strategy.filters import build_filter
    from tradecrm.strategy.profiles import get_profile

    settings = get_settings()
    tf = timeframe or settings.timeframe
    base_prof = get_profile(base)
    names = [x.strip() for x in filters.split(",") if x.strip()]
    flts = [build_filter(n) for n in names]
    sources = base_prof.required_sources | {f.source for f in flts if f.source}
    datasets = _load_datasets([x.strip() for x in symbols.split(",") if x.strip()], tf, days, sources)
    dev = {s: split_holdout(df)[0] for s, df in datasets.items()}

    def run(prof):
        return evaluate(datasets, prof, train_bars=_bars(train_days, tf), test_bars=_bars(test_days, tf),
                        warmup_bars=DEFAULT_WARMUP_BARS, run_sensitivity=False, slippage_bps=slippage_bps)

    def row(label, ev, extra=None):
        m = ev.pooled
        return {"profil": label, "islem": m.n_trades, "pf": m.profit_factor, "beklenti": m.expectancy_pct,
                "sharpe": m.sharpe, "maxdd": m.max_drawdown, "mc_dd95": ev.monte_carlo.max_dd_p95,
                **(extra or {})}

    base_ev = run(base_prof)
    trades = {s: wf.trades for s, wf in base_ev.per_symbol.items()}
    threshold = alpha / max(len(flts), 1)
    rows = [row(base_prof.name, base_ev, {"elenen": "", "elenen_ort": "", "kalan_ort": "", "p": "", "karar": ""})]
    for name, flt in zip(names, flts, strict=True):
        att = attribute_filter(dev, trades, flt)
        prof = replace(base_prof, name=f"{base_prof.name}+{name}", filters=base_prof.filters + ((name, {}),))
        ev = run(prof)
        verdict = "ANLAMLI" if att.p_value < threshold else "anlamsiz"
        rows.append(row(prof.name, ev, {
            "elenen": f"{att.n_removed}/{att.n_kept + att.n_removed}",
            "elenen_ort": f"{att.removed_mean:+.2%}" if att.n_removed else "-",
            "kalan_ort": f"{att.kept_mean:+.2%}" if att.n_kept else "-",
            "p": f"{att.p_value:.3f}", "karar": verdict,
        }))
    typer.echo("")
    typer.echo(f"Filtre katki testi (OOS, holdout haric) -- esik p < {threshold:.4f} (alpha {alpha} / {len(flts)} filtre)")
    typer.echo(format_table(rows, [
        ("profil", "profil", ""), ("islem", "islem", ""), ("PF", "pf", ".2f"), ("beklenti", "beklenti", "+.3%"),
        ("Sharpe", "sharpe", ".2f"), ("maxDD", "maxdd", ".1%"), ("MC DD p95", "mc_dd95", ".1%"),
        ("elenen", "elenen", ""), ("elenen ort.", "elenen_ort", ""), ("kalan ort.", "kalan_ort", ""),
        ("p", "p", ""), ("karar", "karar", ""),
    ]))
    typer.echo("")
    typer.echo("Elenen/kalan ortalamasi: taban islemlerin fiyat getirisi. p: rastgele eleme bu kadar kotu islemleri")
    typer.echo("eler mi (permutasyon). 'anlamsiz' filtre profile EKLENMEZ (Oneri 6, overfitting korumasi).")


def _print_evaluation(ev) -> None:
    from tradecrm.backtest.report import format_metrics

    typer.echo("")
    typer.echo(f"Profil: {ev.profile.name} -- {ev.profile.description}")
    for sym, wf in ev.per_symbol.items():
        typer.echo(f"  {sym:10s} {format_metrics(wf.metrics)}")
        if wf.blocked:
            typer.echo(f"  {'':10s} engellenen girisler: {wf.blocked}")
    typer.echo(f"  {'HAVUZ':10s} {format_metrics(ev.pooled)}")
    mc = ev.monte_carlo
    typer.echo(
        f"  Monte Carlo ({mc.n_sims} sim): DD p50={mc.max_dd_p50:.1%} p95={mc.max_dd_p95:.1%}  "
        f"getiri p5={mc.total_return_p5:+.1%} p50={mc.total_return_p50:+.1%}  P(zarar)={mc.prob_loss:.0%}"
    )
    if ev.sensitivity_worst is not None:
        typer.echo(f"  Hassasiyet: en kotu komsu parametre expectancy orani = {ev.sensitivity_worst:.2f}")
    typer.echo(f"  KAPI: {'GECTI' if ev.gate.passed else 'KALDI'}")
    for f in ev.gate.failures:
        typer.echo(f"    - {f}")


_ACCOUNT_HELP = "Hesap: paper (tum paper alt hesaplari dahil), paper:<profil> ya da binance."


def _open_risk_state(account: str = "paper"):
    from sqlmodel import Session

    from tradecrm.config import get_settings
    from tradecrm.db.engine import create_db_and_tables, get_engine
    from tradecrm.risk.state import RiskStateStore

    settings = get_settings()
    create_db_and_tables(settings.database_url)
    session = Session(get_engine(settings.database_url))
    return session, RiskStateStore(session, broker=account), settings


def _live_allowed_by_env(settings) -> bool:
    """.env ana salteri: canli emir icin TRADING_ENABLED=true VE DRY_RUN=false."""
    return bool(settings.trading_enabled) and not bool(settings.dry_run)


@risk_app.command("status")
def risk_status(account: str = typer.Option("paper", help=_ACCOUNT_HELP)) -> None:
    """Hesabin risk durumu ve alim izninin hangi kaynaktan geldigi."""
    session, state, settings = _open_risk_state(account)
    try:
        today = datetime.now(UTC).date()
        db_enabled = state.is_trading_enabled()
        kill = state.is_kill_switch_tripped()
        typer.echo(f"Hesap: {state.account} ({'CANLI' if state.is_live else 'paper'})")
        typer.echo(f"  DB TRADING_ENABLED (operasyonel anahtar): {db_enabled}")
        if state.is_live:
            env_ok = _live_allowed_by_env(settings)
            typer.echo(
                f"  .env ana salter: TRADING_ENABLED={settings.trading_enabled} DRY_RUN={settings.dry_run} -> "
                f"{'izin' if env_ok else 'KAPALI'}"
            )
            typer.echo(f"  Ilk canli emir onaylandi: {state.is_first_order_confirmed()}")
            effective = env_ok and db_enabled and not kill and state.is_first_order_confirmed()
        else:
            typer.echo("  .env TRADING_ENABLED/DRY_RUN paper hesaplarini etkilemez.")
            effective = db_enabled and not kill
        typer.echo(f"  Kill-switch: {kill}" + (f" -- {state.kill_switch_reason}" if kill else ""))
        typer.echo(f"  => YENI ALIM IZNI: {'EVET' if effective else 'HAYIR'}")
        pnl_scope = state.account + ("*" if ":" not in state.account else "")
        state.broker = pnl_scope
        typer.echo(f"  Bugunku ({today}) gerceklesen P&L [{pnl_scope}]: {state.get_realized_pnl_pct(today):.4%}")
    finally:
        session.close()


@risk_app.command("reset-kill-switch")
def risk_reset_kill_switch(account: str = typer.Option("paper", help=_ACCOUNT_HELP)) -> None:
    """Kill-switch'i manuel olarak resetler (sadece verilen hesap icin)."""
    session, state, _settings = _open_risk_state(account)
    try:
        state.reset_kill_switch()
        typer.echo(f"[{state.account}] kill-switch resetlendi.")
        if state.is_kill_switch_tripped():
            typer.echo(f"  Dikkat: hala tetikli (kok hesaptan): {state.kill_switch_reason}")
    finally:
        session.close()


@risk_app.command("enable-trading")
def risk_enable_trading(account: str = typer.Option("paper", help=_ACCOUNT_HELP)) -> None:
    """Hesabin DB alim bayragini acar. Kok hesap ('paper') acilinca alt hesaplar da acik sayilir.
    Canli hesaplarda ayrica .env TRADING_ENABLED=true ve DRY_RUN=false gerekir."""
    session, state, settings = _open_risk_state(account)
    try:
        state.set_trading_enabled(True)
        typer.echo(f"[{state.account}] TRADING_ENABLED = true")
        if state.is_live and not _live_allowed_by_env(settings):
            typer.echo("  Not: .env ana salteri kapali; canli emir yine de VERILMEZ.")
    finally:
        session.close()


@risk_app.command("disable-trading")
def risk_disable_trading(account: str = typer.Option("paper", help=_ACCOUNT_HELP)) -> None:
    """Hesabin DB alim bayragini kapatir (kok hesap kapatilirsa butun alt hesaplar durur)."""
    session, state, _settings = _open_risk_state(account)
    try:
        state.set_trading_enabled(False)
        typer.echo(f"[{state.account}] TRADING_ENABLED = false")
    finally:
        session.close()


@risk_app.command("confirm-first-order")
def risk_confirm_first_order(account: str = typer.Option(..., help="Canli hesap adi (orn. binance).")) -> None:
    """Ilk canli emir onayini kaydeder (canli hesaplar icin zorunlu kapi)."""
    session, state, _settings = _open_risk_state(account)
    try:
        if not state.is_live:
            typer.echo("Paper hesaplar ilk emir onayi gerektirmez.")
            raise typer.Exit(code=1)
        state.confirm_first_order()
        typer.echo(f"[{state.account}] ilk canli emir onaylandi.")
    finally:
        session.close()


def _live_exchange(settings, exchange_id: str):
    """(spec, ccxt istemcisi, hesap adi, testnet mi). Kimlik bilgisi eksikse acik hata ile cikar."""
    from tradecrm.broker.ccxt_spot_adapter import get_spec, make_exchange

    try:
        spec = get_spec(exchange_id)
    except KeyError as exc:
        typer.echo(str(exc).strip("'\""))
        raise typer.Exit(code=1) from None
    if spec.id == "binance":
        key, secret, passphrase, sandbox = (settings.binance_api_key, settings.binance_api_secret, "",
                                            settings.binance_testnet)
        missing = "BINANCE_API_KEY / BINANCE_API_SECRET"
    else:
        key, secret, passphrase, sandbox = (settings.kucoin_api_key, settings.kucoin_api_secret,
                                            settings.kucoin_api_passphrase, False)
        missing = "KUCOIN_API_KEY / KUCOIN_API_SECRET / KUCOIN_API_PASSPHRASE"
    if not (key and secret and (passphrase or not spec.needs_passphrase)):
        typer.echo(f"{missing} tanimli degil (.env).")
        raise typer.Exit(code=1)
    account = f"{spec.id}:testnet" if sandbox else spec.id
    return spec, make_exchange(spec, key, secret, passphrase, sandbox), account, sandbox


@live_app.command("check")
def live_check(
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT", help="Kontrol edilecek semboller."),
    initial_capital: float = typer.Option(..., help="Botun yonetecegi butce (USDT)."),
    exchange_id: str = typer.Option("kucoin", "--exchange", help="kucoin | binance"),
) -> None:
    """Canliya gecmeden once on kontrol: anahtar yetkileri, saat, bakiye, semboller. Emir GONDERMEZ."""
    from tradecrm.broker.preflight import run_preflight
    from tradecrm.config import get_settings

    settings = get_settings()
    spec, exchange, account, sandbox = _live_exchange(settings, exchange_id)
    trading_enabled = first_confirmed = None
    try:
        session, state, _ = _open_risk_state(account)
        try:
            trading_enabled, first_confirmed = state.is_trading_enabled(), state.is_first_order_confirmed()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 -- DB yoksa da kontrol yapilabilir
        typer.echo(f"(Veritabani okunamadi, izin durumu atlandi: {type(exc).__name__})")
    checks = run_preflight(exchange, [x.strip() for x in symbols.split(",") if x.strip()], initial_capital,
                           sandbox, _live_allowed_by_env(settings), trading_enabled, first_confirmed, spec=spec)
    mark = {"OK": "[ OK ]", "WARN": "[UYARI]", "FAIL": "[HATA]", "INFO": "[BILGI]"}
    typer.echo(f"Hesap: {account}")
    for c in checks:
        typer.echo(f"{mark[c.status]:8s} {c.name}: {c.detail}")
    fails = [c for c in checks if c.status == "FAIL"]
    typer.echo("")
    typer.echo("SONUC: " + (f"{len(fails)} HATA -- canliya gecmeyin." if fails else "Engel yok."))
    if fails:
        raise typer.Exit(code=1)


def _runner_for(mode: str):
    """(ManagedProcess, dogrulayici, gorunen ad) -- paper ya da canli borsa runner'i."""
    from tradecrm.api.services.live import prepare_exchange_start, prepare_live_start
    from tradecrm.api.services.processes import (
        exchange_runner_process,
        live_runner_process,
    )

    if mode == "paper":
        return live_runner_process(), prepare_live_start, "Paper runner"

    def prepare(db, profile, symbols, timeframe, capital):
        return prepare_exchange_start(db, mode, profile, symbols, timeframe, capital)

    return exchange_runner_process(mode), prepare, f"{mode} runner"


def _start_runner(mode: str, profile: str, symbols: list[str], timeframe: str, capital, started_by: str) -> None:
    from tradecrm.api.services.live import LiveStartError

    proc, prepare, label = _runner_for(mode)
    if proc.status(tail=0).running:
        typer.echo(f"{label} zaten calisiyor.")
        return
    # Baslatici kisa omurlu olmali (Gorev Zamanlayici sure siniri): veritabanini en fazla
    # START_DB_WAIT_S bekler; yoksa parametreleri DB'siz dogrular ve runner'i yine baslatir --
    # runner veritabanini kendisi bekler ve hesap/butce kontrolunu baslarken yapar.
    session = None
    try:
        session = _collect_session(wait=True, max_wait_s=START_DB_WAIT_S)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Veritabani hazir degil ({type(exc).__name__}); runner baslatiliyor, DB'yi kendisi bekleyecek.")
    try:
        command, meta = prepare(session, profile, symbols, timeframe, capital)
    except LiveStartError as exc:
        typer.echo(f"Baslatilmadi: {exc.detail}")
        raise typer.Exit(code=1) from None
    finally:
        if session is not None:
            session.close()
    meta["started_by"] = started_by
    st = proc.start(command=command, meta=meta)
    typer.echo(f"{label} baslatildi (pid {st.pid}): {meta['profile']} {meta['timeframe']} {','.join(meta['symbols'])}"
               f" butce {meta['initial_capital']:,.2f}. Log: {st.log_path}")


@live_app.command("start")
def live_start_cmd(
    mode: str = typer.Option(..., help="paper | kucoin | binance"),
    profile: str = typer.Option("donchian_fng"),
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"),
    timeframe: str = typer.Option("1d"),
    initial_capital: float = typer.Option(..., help="Butce (USDT). Kayitli hesapla ayni olmali."),
) -> None:
    """Runner'i arka planda baslatir (panel ve otomatik baslatma ile ayni surec; parametreler saklanir).
    Canli modda emir icin .env salteri, DB alim izni ve ilk emir onayi AYRICA gerekir."""
    if mode not in ("paper", "kucoin", "binance"):
        typer.echo("Gecersiz mod: paper | kucoin | binance")
        raise typer.Exit(code=1)
    _start_runner(mode, profile, [x.strip() for x in symbols.split(",") if x.strip()], timeframe,
                  initial_capital, "cli")


@live_app.command("stop")
def live_stop_cmd(mode: str = typer.Option(..., help="paper | kucoin | binance")) -> None:
    """Arka plandaki runner'i durdurur (acik pozisyonlar borsada kalir; stoplar izlenmez)."""
    proc, _, label = _runner_for(mode)
    was = proc.status(tail=0).running
    proc.stop()
    typer.echo(f"{label} durduruldu." if was else f"{label} zaten calismiyordu.")


@live_app.command("resume")
def live_resume(mode: str = typer.Option("paper", help="paper | kucoin | binance")) -> None:
    """Runner'i son baslatildigi parametrelerle yeniden baslatir (otomatik baslatma bunu kullanir).
    Parametreler ayni dogrulamadan gecer; calisiyorsa dokunmaz."""
    proc, _, label = _runner_for(mode)
    if proc.status(tail=0).running:
        typer.echo(f"{label} zaten calisiyor.")
        return
    meta = ((proc.last_start() or {}).get("meta")) or {}
    if not meta.get("profile"):
        typer.echo(f"Daha once baslatilmis bir {label} yok; once 'tradecrm live start --mode {mode} ...' ile baslatin.")
        raise typer.Exit(code=1)
    _start_runner(mode, meta["profile"], list(meta.get("symbols") or []), meta.get("timeframe", ""),
                  meta.get("initial_capital"), "autostart")


@live_app.command("run")
def live_run(
    mode: str = typer.Option("paper", help="paper | kucoin | binance (canli; once 'tradecrm live check')."),
    symbol: str = typer.Option(
        None, "--symbol", "--symbols",
        help="Sembol ya da virgulle ayrilmis liste (BTC/USDT,ETH/USDT). Bos ise .env'deki SYMBOL. "
        "Coklu sembolde ayni hesap paylasilir: nakit, toplam pozisyon tavani (profilde, su an %35) ve risk bayraklari hesap geneli.",
    ),
    timeframe: str = typer.Option(None, help="Bos ise .env'deki TIMEFRAME kullanilir."),
    profile: str = typer.Option(
        None, help="Profil ya da portfoy ('a+b', 'a:0.6+b:0.4'). Bos ise .env'deki PROFILE."
    ),
    initial_capital: float = typer.Option(
        10_000.0, help="Hesap butcesi (USDT). Canli modda bot yalnizca bu kadarini (+K/Z) yonetir.",
    ),
    run_immediately: bool = typer.Option(True, help="Baslar baslamaz bir tick calistir."),
    once: bool = typer.Option(False, help="Tek tick calistir ve cik (kuru calisma / kontrol icin)."),
) -> None:
    """Canli dongu: data -> strategy -> risk -> broker -> db -> audit -> notify.
    --mode paper: sanal para. --mode kucoin|binance: GERCEK emir (Binance'te BINANCE_TESTNET=true ile testnet);
    emir icin .env TRADING_ENABLED=true + DRY_RUN=false, DB alim izni ve ilk emir onayi gerekir.
    Zamanlanmis dongu, Ctrl+C ile durdurulana kadar calisir."""
    if mode not in ("paper", "binance", "kucoin"):
        typer.echo("Gecersiz mod: paper | kucoin | binance")
        raise typer.Exit(code=1)

    from sqlmodel import Session

    from tradecrm.audit.logger import AuditLogger
    from tradecrm.broker.paper_adapter import PaperBrokerAdapter
    from tradecrm.config import get_settings
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.data.sources.enrich import LiveEnricher
    from tradecrm.db.engine import get_engine
    from tradecrm.notify.telegram import TelegramNotifier
    from tradecrm.risk.guardrails import RiskManager
    from tradecrm.risk.state import RiskStateStore
    from tradecrm.runner.live_runner import LiveRunner, MultiRunner
    from tradecrm.runner.scheduler import run_scheduler
    from tradecrm.strategy.profiles import Portfolio, resolve

    settings = get_settings()
    symbols = [x.strip() for x in (symbol or settings.symbol).split(",") if x.strip()]
    if len(set(symbols)) != len(symbols):
        typer.echo("Ayni sembol birden fazla kez verildi.")
        raise typer.Exit(code=1)
    resolved_timeframe = timeframe or settings.timeframe
    target = resolve(profile or settings.profile)
    members = target.members if isinstance(target, Portfolio) else ((target, 1.0),)

    # Oturum acilisinda Docker/veritabani gec gelebilir: runner kapanmaz, hazir olana kadar bekler.
    _wait_for_database(settings.database_url, max_wait_s=None)
    session = Session(get_engine(settings.database_url))
    provider = CCXTDataProvider()
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    audit_logger = AuditLogger(settings.audit_log_path)

    from tradecrm.db.repository import AccountMismatchError, AccountRepository
    from tradecrm.monitor.drift import DriftGuard, load_baseline
    from tradecrm.monitor.equity import EquityRecorder

    exchange = spec = None
    live_account = None
    if mode != "paper":
        if isinstance(target, Portfolio):
            typer.echo("Canli modda portfoy desteklenmiyor; tek profil verin.")
            raise typer.Exit(code=1)
        spec, exchange, live_account, sandbox = _live_exchange(settings, mode)
        typer.echo(f"CANLI MOD: {spec.title} {'TESTNET' if sandbox else 'GERCEK HESAP'} (hesap {live_account}) -- "
                   f"emirler {'ACIK' if _live_allowed_by_env(settings) else 'KAPALI (.env TRADING_ENABLED/DRY_RUN)'}")

    runners = []
    guards: list = []
    reports: list = []
    for prof, weight in members:
        if exchange is not None:
            from tradecrm.broker.ccxt_spot_adapter import CcxtSpotAdapter

            broker = CcxtSpotAdapter(session, exchange, spec, initial_capital=initial_capital * weight,
                                     account=live_account.split(":", 1)[1] if ":" in live_account else None)
        else:
            broker = PaperBrokerAdapter(
                session, provider, initial_capital=initial_capital * weight,
                account=prof.name if isinstance(target, Portfolio) else None,
            )
        try:
            AccountRepository(session).register(
                broker.name, initial_capital * weight, mode="live" if exchange is not None else "paper",
                profile=prof.name, timeframe=resolved_timeframe,
            )
        except AccountMismatchError as exc:
            typer.echo(f"HATA: {exc}. Ayni sermayeyle baslatin ya da baska bir hesap/profil kullanin.")
            raise typer.Exit(code=1) from None
        state = RiskStateStore(session, broker=broker.name)
        risk_manager = RiskManager(
            state,
            total_capital_allocation_pct=prof.sizing.max_total_fraction,
            max_daily_loss_pct=prof.breakers.max_daily_loss_pct if prof.breakers else settings.max_daily_loss_pct,
            live_trading_allowed_by_env=_live_allowed_by_env(settings),
        )
        notice_board: set[str] = set()
        for sym in symbols:
            runners.append(LiveRunner(
                data_provider=provider,
                strategy=prof.build_strategy(),
                risk_manager=risk_manager,
                state=state,
                broker=broker,
                notifier=notifier,
                audit_logger=audit_logger,
                symbol=sym,
                timeframe=resolved_timeframe,
                sizing=prof.sizing,
                exits=prof.exits,
                breakers=prof.breakers,
                session=session,
                enricher=(
                    LiveEnricher(sym, resolved_timeframe, prof.required_sources, audit_logger)
                    if prof.required_sources
                    else None
                ),
                notice_board=notice_board,
            ))
        baseline = load_baseline(prof.name, resolved_timeframe)
        guards.append(DriftGuard(state, baseline, notifier, audit_logger))
        guards.append(EquityRecorder(broker, session, audit_logger))
        reports.append((broker, state, guards[-2]))
        drift_txt = (
            f"drift baseline={len(baseline.returns)} OOS islem" if baseline
            else f"drift IZLENMIYOR (baseline yok: backtest walk-forward --profile {prof.name} "
                 f"--timeframe {resolved_timeframe} --save-baseline)"
        )
        typer.echo(f"  {broker.name}: profil={prof.name} sermaye={initial_capital * weight:,.0f}  {drift_txt}")

    runner = MultiRunner(runners + guards)

    def _weekly_report() -> None:
        from tradecrm.reports.store import create_report

        for broker, state, guard in reports:
            try:
                notifier.send_message(_account_report(broker, state, guard.last_result, days=7))
            except Exception as exc:  # noqa: BLE001
                audit_logger.log("report_error", account=broker.name, error=f"{type(exc).__name__}: {exc}")
            try:  # Raporlar sekmesinde gorunsun: gecen haftanin (Pzt-Paz) raporu
                create_report(session, "week", account=broker.name, created_by="schedule")
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                audit_logger.log("report_error", account=broker.name, error=f"kayit: {type(exc).__name__}: {exc}")
    typer.echo(
        f"{'Paper' if mode == 'paper' else spec.title} mod baslatiliyor: {','.join(symbols)} {resolved_timeframe} "
        f"hedef={target.name} TRADING_ENABLED={runners[0].state.is_trading_enabled()}"
    )
    if once:
        runner.safe_tick()
        typer.echo("Tek tick tamamlandi (--once). Ayrintilar: logs/audit.jsonl")
        session.close()
        return
    try:
        run_scheduler(
            runner, resolved_timeframe, run_immediately=run_immediately,
            extra_jobs=[(_weekly_report, "cron", {"day_of_week": "mon", "hour": 8, "minute": 0})],
        )
    except KeyboardInterrupt:
        typer.echo("Durduruldu.")
    finally:
        session.close()


def _account_report(broker, state, drift=None, days: int = 7) -> str:
    from datetime import UTC, datetime

    from tradecrm.monitor.report import build_report

    now = datetime.now(UTC)
    trades = state.get_closed_trades_between(now - timedelta(days=days), now + timedelta(seconds=1))
    try:
        snapshot = broker.get_snapshot()
    except Exception:  # noqa: BLE001 -- rapor fiyat alinamasa da gitmeli
        snapshot = None
    return build_report(
        broker.name, trades, snapshot=snapshot, drift=drift, trading_enabled=state.is_trading_enabled(),
        kill_switch_reason=state.kill_switch_reason, now=now, days=days,
    )


@report_app.command("generate")
def report_generate(
    kind: str = typer.Option("week", help="day | week | month | custom"),
    start: str = typer.Option(None, help="YYYY-MM-DD (day/week/month: referans gun; custom: baslangic)"),
    end: str = typer.Option(None, help="YYYY-MM-DD (yalnizca custom: dahil son gun)"),
    account: str = typer.Option(None, help="Hesap (paper, paper:<profil>, paper* ...). Bos: tum hesaplar."),
    send: bool = typer.Option(False, help="Ozeti Telegram'a da gonder."),
    markdown: str = typer.Option(None, help="Markdown'u bu dosyaya da yaz."),
) -> None:
    """Donem raporu olusturur ve Raporlar sekmesinde gorunmesi icin kaydeder."""
    from datetime import date

    from tradecrm.reports.builder import ReportParamsError
    from tradecrm.reports.render import to_markdown, to_text
    from tradecrm.reports.store import create_report, report_full

    session = _collect_session()
    try:
        try:
            row = create_report(session, kind, date.fromisoformat(start) if start else None,
                                date.fromisoformat(end) if end else None, account=account, created_by="cli")
        except (ReportParamsError, ValueError) as exc:
            typer.echo(f"Hata: {exc}")
            raise typer.Exit(code=1) from None
        data = report_full(row)["data"]
        typer.echo(f"Rapor #{row.id} kaydedildi.")
        typer.echo("")
        typer.echo(to_text(data))
        if markdown:
            from pathlib import Path

            Path(markdown).write_text(to_markdown(data), encoding="utf-8")
            typer.echo(f"Markdown: {markdown}")
        if send:
            from tradecrm.config import get_settings
            from tradecrm.notify.telegram import TelegramNotifier

            st = get_settings()
            ok = TelegramNotifier(st.telegram_bot_token, st.telegram_chat_id).send_message(to_text(data))
            typer.echo("Telegram'a gonderildi." if ok else "Telegram'a gonderilemedi (token/chat_id?).")
    finally:
        session.close()


@report_app.command("weekly")
def report_weekly(
    account: str = typer.Option("paper", help="paper ya da paper:<profil>."),
    profile: str = typer.Option(None, help="Drift durumu icin profil (baseline). Bos ise .env PROFILE."),
    timeframe: str = typer.Option(None, help="Bos ise .env TIMEFRAME."),
    days: int = typer.Option(7),
    initial_capital: float = typer.Option(
        None, help="Hesap kaydi yoksa paper baslangic sermayesi (varsayilan: kayitli hesaptan)."
    ),
    send: bool = typer.Option(False, help="Telegram'a da gonder."),
) -> None:
    """Donem raporu: kapanan islemler, hesap durumu, izinler ve drift."""
    from sqlmodel import Session

    from tradecrm.audit.logger import AuditLogger
    from tradecrm.broker.paper_adapter import PaperBrokerAdapter
    from tradecrm.config import get_settings
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.db.engine import create_db_and_tables, get_engine
    from tradecrm.monitor.drift import DriftGuard, load_baseline
    from tradecrm.notify.telegram import TelegramNotifier
    from tradecrm.risk.state import RiskStateStore

    if not account.startswith("paper"):
        typer.echo("Su an sadece paper hesaplar raporlanabilir (canli adapter Faz 10).")
        raise typer.Exit(code=1)
    settings = get_settings()
    create_db_and_tables(settings.database_url)
    session = Session(get_engine(settings.database_url))
    try:
        from tradecrm.db.repository import AccountRepository

        sub_account = account.split(":", 1)[1] if ":" in account else None
        registered = AccountRepository(session).get(account)
        capital = initial_capital or (registered.initial_capital if registered else 10_000.0)
        broker = PaperBrokerAdapter(session, CCXTDataProvider(), initial_capital=capital, account=sub_account)
        state = RiskStateStore(session, broker=broker.name)
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        prof_name = sub_account or profile or settings.profile
        guard = DriftGuard(
            state, load_baseline(prof_name, timeframe or settings.timeframe), notifier,
            AuditLogger(settings.audit_log_path),
        )
        drift = guard.run_once()
        text = _account_report(broker, state, drift, days=days)
        typer.echo(text)
        if send:
            typer.echo("Telegram: " + ("gonderildi" if notifier.send_message(text) else "GONDERILEMEDI"))
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


def _admin_session():
    from sqlmodel import Session

    from tradecrm.config import get_settings
    from tradecrm.db.engine import create_db_and_tables, get_engine

    settings = get_settings()
    create_db_and_tables(settings.database_url)
    return Session(get_engine(settings.database_url))


@admin_app.command("start")
def admin_start() -> None:
    """Admin panelini arka planda baslatir (logs/admin.log; calisiyorsa dokunmaz)."""
    from tradecrm.api.services.processes import admin_process

    proc = admin_process()
    before = proc.status(tail=0)
    st = proc.start()
    typer.echo(f"Admin paneli zaten calisiyor (pid {st.pid})." if before.running
               else f"Admin paneli baslatildi (pid {st.pid}). Log: {st.log_path}")


@admin_app.command("stop")
def admin_stop() -> None:
    """Arka planda calisan admin panelini durdurur ('admin start' ya da otomatik baslatma ile acilmis olani)."""
    from tradecrm.api.services.processes import admin_process

    proc = admin_process()
    was = proc.status(tail=0).running
    proc.stop()
    typer.echo("Admin paneli durduruldu." if was else "Arka planda calisan admin paneli yok (terminalde aciksa Ctrl+C).")


@admin_app.command("serve")
def admin_serve() -> None:
    """Admin panelini baslatir (varsayilan http://127.0.0.1:8000). Giris gerekir:
    ilk kullanici 'tradecrm admin create-user' ile olusturulur."""
    import uvicorn
    from sqlmodel import func, select

    from tradecrm.api.app import DEFAULT_STATIC_DIR, build_context, create_app
    from tradecrm.config import get_settings
    from tradecrm.db.models import AdminUser

    settings = get_settings()
    _wait_for_database(settings.database_url, max_wait_s=900)
    ctx = build_context(settings)
    with ctx.session_factory() as s:
        if s.exec(select(func.count()).select_from(AdminUser)).one() == 0:
            typer.echo("Henuz kullanici yok. Once: tradecrm admin create-user <kullanici-adi>")
    if not (DEFAULT_STATIC_DIR / "index.html").is_file():
        typer.echo("Uyari: arayuz derlenmemis (cd frontend && npm ci && npm run build). Sadece API calisacak.")
    if settings.dashboard_host not in ("127.0.0.1", "localhost", "::1"):
        typer.echo(
            f"UYARI: panel {settings.dashboard_host} adresinde dinleyecek. Disariya acacaksaniz HTTPS reverse "
            "proxy arkasina koyun ve ADMIN_COOKIE_SECURE=true yapin."
        )
    typer.echo(f"Admin paneli: http://{settings.dashboard_host}:{settings.dashboard_port}")
    uvicorn.run(create_app(ctx), host=settings.dashboard_host, port=settings.dashboard_port, log_level="warning")


@dashboard_app.command("run")
def dashboard_run() -> None:
    """(Eski ad) admin serve ile ayni."""
    admin_serve()


def _prompt_password() -> str:
    from tradecrm.api.security import MIN_PASSWORD_LENGTH

    return typer.prompt(f"Parola (en az {MIN_PASSWORD_LENGTH} karakter)", hide_input=True, confirmation_prompt=True)


@admin_app.command("create-user")
def admin_create_user(username: str = typer.Argument(..., help="Kullanici adi (3-32: harf, rakam, _ . -)")) -> None:
    """Panel kullanicisi olusturur. Butun kullanicilar ayni yetkidedir."""
    from tradecrm.api.security import WeakPasswordError
    from tradecrm.api.services.users import UserError, create_user, record_action

    session = _admin_session()
    try:
        user = create_user(session, username, _prompt_password())
        record_action(session, "cli", "user_created", detail={"username": user.username})
        typer.echo(f"Kullanici olusturuldu: {user.username}")
    except (UserError, WeakPasswordError) as exc:
        typer.echo(f"Hata: {exc}")
        raise typer.Exit(code=1) from None
    finally:
        session.close()


@admin_app.command("list-users")
def admin_list_users() -> None:
    from sqlmodel import select

    from tradecrm.db.models import AdminUser

    session = _admin_session()
    try:
        for u in session.exec(select(AdminUser).order_by(AdminUser.username)):
            last = u.last_login_at.isoformat() if u.last_login_at else "-"
            typer.echo(f"{u.username:20s} {'aktif' if u.is_active else 'PASIF':6s} son giris: {last}")
    finally:
        session.close()


def _user_or_exit(session, username: str):
    from tradecrm.api.services.users import get_user

    user = get_user(session, username)
    if user is None:
        typer.echo(f"Kullanici yok: {username}")
        raise typer.Exit(code=1)
    return user


@admin_app.command("disable-user")
def admin_disable_user(username: str) -> None:
    """Kullaniciyi pasif yapar ve acik oturumlarini kapatir."""
    from tradecrm.api.services.users import UserError, record_action, set_active

    session = _admin_session()
    try:
        set_active(session, _user_or_exit(session, username), False)
        record_action(session, "cli", "user_deactivated", detail={"username": username})
        typer.echo(f"{username} devre disi; oturumlari kapatildi.")
    except UserError as exc:
        typer.echo(f"Hata: {exc}")
        raise typer.Exit(code=1) from None
    finally:
        session.close()


@admin_app.command("enable-user")
def admin_enable_user(username: str) -> None:
    from tradecrm.api.services.users import record_action, set_active

    session = _admin_session()
    try:
        set_active(session, _user_or_exit(session, username), True)
        record_action(session, "cli", "user_activated", detail={"username": username})
        typer.echo(f"{username} aktif.")
    finally:
        session.close()


@admin_app.command("set-password")
def admin_set_password(username: str) -> None:
    """Parolayi degistirir; kullanicinin butun oturumlari kapatilir."""
    from tradecrm.api.security import WeakPasswordError
    from tradecrm.api.services.users import record_action, set_password

    session = _admin_session()
    try:
        set_password(session, _user_or_exit(session, username), _prompt_password())
        record_action(session, "cli", "user_password_changed", detail={"username": username})
        typer.echo("Parola degistirildi.")
    except WeakPasswordError as exc:
        typer.echo(f"Hata: {exc}")
        raise typer.Exit(code=1) from None
    finally:
        session.close()


START_DB_WAIT_S = 120.0


def _collect_session(wait: bool = False, max_wait_s: float | None = None):
    from sqlmodel import Session

    from tradecrm.config import get_settings
    from tradecrm.db.engine import get_engine

    settings = get_settings()
    _wait_for_database(settings.database_url, max_wait_s=max_wait_s if wait else 0)
    return Session(get_engine(settings.database_url))


def _wait_for_database(url: str, max_wait_s: float | None = None, interval_s: float = 30.0, sleep=None) -> None:
    """Veritabani hazir olana kadar bekler (oturum acilisinda Docker gec gelebilir).
    max_wait_s=None: sinirsiz; 0: tek deneme (hata yukari firlatilir)."""
    import time

    from sqlalchemy.exc import OperationalError

    from tradecrm.db.engine import create_db_and_tables

    sleep = sleep or time.sleep
    waited = 0.0
    while True:
        try:
            create_db_and_tables(url)
            if waited:
                typer.echo(f"{datetime.now(UTC):%Y-%m-%d %H:%M} veritabani hazir ({waited:.0f} sn beklendi)")
            return
        except OperationalError as exc:
            if max_wait_s is not None and waited >= max_wait_s:
                raise
            if not waited:
                typer.echo(f"{datetime.now(UTC):%Y-%m-%d %H:%M} veritabanina baglanilamadi "
                           f"({type(exc.orig).__name__}); {interval_s:.0f} sn arayla tekrar denenecek")
            sleep(interval_s)
            waited += interval_s


_SYMBOLS_HELP = "Virgulle ayrilmis semboller. Bos ise .env'deki COLLECT_SYMBOLS."
_RETENTION_HELP = "Bu kadar gunden eski metrikler silinir. Bos ise .env'deki COLLECT_RETENTION_DAYS (45)."


def _collect_config(symbols: str | None, retention_days: int | None) -> tuple[list[str], int]:
    from tradecrm.config import get_settings

    settings = get_settings()
    syms = [x.strip() for x in (symbols or settings.collect_symbols).split(",") if x.strip()]
    days = retention_days if retention_days is not None else settings.collect_retention_days
    if days <= 0:
        typer.echo("Saklama suresi pozitif olmali.")
        raise typer.Exit(code=1)
    return syms, days


def _collect_cycle(collector, session, syms: list[str], retention_days: int, trigger: str = "schedule") -> str:
    from tradecrm.monitor.collectors import run_cycle, run_summary

    run = run_cycle(session, "derivatives", collector, syms, retention_days, trigger=trigger)
    return run_summary(run) + f" (saklama {retention_days} gun)"


@collect_app.command("once")
def collect_once(
    symbols: str = typer.Option(None, help=_SYMBOLS_HELP),
    retention_days: int = typer.Option(None, help=_RETENTION_HELP),
) -> None:
    """Open interest + long/short oranini bir kez ceker, DB'ye yazar (idempotent) ve eski satirlari siler."""
    from tradecrm.data.collectors.derivatives import DerivativesCollector

    syms, days = _collect_config(symbols, retention_days)
    session = _collect_session()
    try:
        typer.echo(_collect_cycle(DerivativesCollector(session), session, syms, days, trigger="cli"))
    finally:
        session.close()


@collect_app.command("run")
def collect_run(
    symbols: str = typer.Option(None, help=_SYMBOLS_HELP),
    retention_days: int = typer.Option(None, help=_RETENTION_HELP),
) -> None:
    """Toplayiciyi her saat basi (+2 dk) calistirir; her calismada saklama suresinden
    eski satirlari siler. Ctrl+C ile durur. Binance bu verilerin sadece ~30 gununu
    sakladigi icin surekli calismali."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    from tradecrm.data.collectors.derivatives import DerivativesCollector

    syms, days = _collect_config(symbols, retention_days)
    session = _collect_session(wait=True)
    collector = DerivativesCollector(session)
    typer.echo(f"Toplayici: {len(syms)} sembol ({', '.join(syms)}), saklama {days} gun")

    def job():
        try:
            typer.echo(f"{datetime.now(UTC):%Y-%m-%d %H:%M} {_collect_cycle(collector, session, syms, days)}")
        except Exception as exc:  # noqa: BLE001 -- tek calismanin hatasi sureci durdurmamali
            session.rollback()
            typer.echo(f"Toplama hatasi: {type(exc).__name__}: {exc}")

    job()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(job, "cron", minute=2, coalesce=True, max_instances=1)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        typer.echo("Durduruldu.")
    finally:
        session.close()


@collect_app.command("start")
def collect_start() -> None:
    """Toplayiciyi arka planda baslatir (panel ile ayni surec; calisiyorsa dokunmaz)."""
    from tradecrm.api.services.processes import collector_process

    proc = collector_process()
    before = proc.status(tail=0)
    st = proc.start()
    if before.running:
        typer.echo(f"Toplayici zaten calisiyor (pid {st.pid}).")
    else:
        typer.echo(f"Toplayici baslatildi (pid {st.pid}). Log: {st.log_path}")


@collect_app.command("stop")
def collect_stop() -> None:
    """Arka plandaki toplayiciyi durdurur."""
    from tradecrm.api.services.processes import collector_process

    proc = collector_process()
    was = proc.status(tail=0).running
    proc.stop()
    typer.echo("Toplayici durduruldu." if was else "Toplayici zaten calismiyordu.")


@collect_app.command("autostart")
def collect_autostart(
    action: str = typer.Argument("status", help="enable | disable | status"),
) -> None:
    """Windows oturumu acilinca toplayiciyi kendiliginden baslat (= tradecrm autostart <islem> collector)."""
    _autostart(action, "collector")


def _autostart(action: str, key: str) -> None:
    from pathlib import Path

    from tradecrm.runner import autostart

    try:
        spec = autostart.get_task(key)
    except KeyError:
        typer.echo(f"Bilinmeyen surec: {key}. Secenekler: {', '.join(autostart.TASKS)}")
        raise typer.Exit(code=2) from None
    try:
        if action == "enable":
            st = autostart.enable(Path.cwd(), key)
            typer.echo(f"Otomatik baslatma ACIK: '{spec.task_name}' (oturum acilisindan {spec.delay[2:]} sonra)")
            typer.echo(f"  komut: {st.command}")
            typer.echo(f"  dizin: {st.working_dir}")
        elif action == "disable":
            removed = autostart.disable(key)
            typer.echo(f"'{spec.task_name}' KAPATILDI." if removed else f"'{spec.task_name}' zaten kapali.")
        elif action == "status":
            st = autostart.status(key)
            if not st.installed:
                typer.echo(f"'{spec.task_name}' KAPALI. Acmak icin: tradecrm autostart enable {key}")
                return
            typer.echo(f"'{spec.task_name}' ACIK ({st.state})")
            typer.echo(f"  komut: {st.command}")
            typer.echo(f"  dizin: {st.working_dir}")
            typer.echo(f"  son calisma: {st.last_run or '-'} (sonuc {st.last_result})")
        else:
            typer.echo("Gecersiz islem: enable | disable | status")
            raise typer.Exit(code=2)
    except (autostart.AutostartUnsupported, ValueError, RuntimeError) as exc:
        typer.echo(f"Hata: {exc}")
        raise typer.Exit(code=1) from None


@autostart_app.command("enable")
def autostart_enable(process: str = typer.Argument(..., help="collector | live | admin")) -> None:
    """Sureci Windows oturumu acilinca kendiliginden baslat (Gorev Zamanlayici, yonetici gerekmez)."""
    _autostart("enable", process)


@autostart_app.command("disable")
def autostart_disable(process: str = typer.Argument(..., help="collector | live | admin")) -> None:
    """Otomatik baslatma gorevini siler."""
    _autostart("disable", process)


@autostart_app.command("status")
def autostart_status(process: str = typer.Argument(None, help="collector | live | admin (bos: hepsi)")) -> None:
    """Otomatik baslatma gorevlerinin durumu."""
    from tradecrm.runner import autostart

    if process:
        _autostart("status", process)
        return
    try:
        for key, st in autostart.status_all().items():
            spec = autostart.TASKS[key]
            typer.echo(f"{key:10s} {'ACIK' if st.installed else 'kapali':6s} {spec.task_name}"
                       + (f"  (son: {st.last_run or '-'}, sonuc {st.last_result})" if st.installed else ""))
    except (autostart.AutostartUnsupported, RuntimeError) as exc:
        typer.echo(f"Hata: {exc}")
        raise typer.Exit(code=1) from None


@collect_app.command("status")
def collect_status() -> None:
    """Toplayici sagligi: surec, son calismalar, sembol bazinda veri tazeligi."""
    from tradecrm.api.services.processes import collector_process
    from tradecrm.monitor.collectors import COLLECTORS, collector_health

    syms, days = _collect_config(None, None)
    proc = collector_process().status(tail=0)
    session = _collect_session()
    try:
        for spec in COLLECTORS.values():
            h = collector_health(session, spec, process_running=proc.running, process_pid=proc.pid,
                                 process_started_at=proc.started_at, symbols=syms, retention_days=days)
            typer.echo(f"{spec.name}: {h['status'].upper()}  ({spec.title}; saklama {days} gun)")
            typer.echo(f"  surec: {'calisiyor pid=' + str(proc.pid) if proc.running else 'CALISMIYOR'}"
                       + (f", sonraki calisma {h['next_run_at'][:16]}" if h["next_run_at"] else ""))
            last = h["last_run"]
            if last:
                typer.echo(f"  son calisma: {last['started_at'][:16]} {last['status']} +{last['rows_added']} "
                           f"-{last['rows_purged']} ({(last['duration_ms'] or 0) / 1000:.1f} sn, {last['trigger']})")
            st = h["stats_24h"]
            typer.echo(f"  son 24 saat: {st['runs']} calisma, {st['ok']} OK, {st['partial']} kismi, "
                       f"{st['failed']} basarisiz, +{st['rows_added']} satir")
            for issue in h["issues"]:
                typer.echo(f"  ! {issue}")
            for f in h["freshness"]:
                if f["rows"] == 0:
                    typer.echo(f"  {f['symbol']:10s} {f['metric']:20s} VERI YOK")
                    continue
                typer.echo(f"  {f['symbol']:10s} {f['metric']:20s} {f['rows']:6d} satir  {f['first'][:16]} -> "
                           f"{f['last'][:16]}  gecikme {f['lag_periods']}  kayip {f['missing_periods']}"
                           + ("" if f["configured"] else "  (listede degil)"))
    finally:
        session.close()


if __name__ == "__main__":
    app()
