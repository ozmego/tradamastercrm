from datetime import UTC, datetime, timedelta

from tradecrm.risk.breakers import BreakerConfig, ClosedTradeLite, evaluate_entry

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
CFG = BreakerConfig(
    max_daily_loss_pct=0.05,
    max_consecutive_losses=3,
    cooldown_hours=24,
    max_weekly_loss_pct=0.08,
    max_drawdown_pct=0.15,
    max_trades_per_day=None,
)


def t(hours_ago: float, pnl: float, pnl_pct: float | None = None) -> ClosedTradeLite:
    return ClosedTradeLite(NOW - timedelta(hours=hours_ago), pnl, pnl_pct if pnl_pct is not None else pnl / 10_000)


def test_no_trades_allowed():
    assert evaluate_entry([], NOW, 10_000, CFG).allowed


def test_loss_streak_triggers_cooldown():
    trades = [t(30, -10), t(20, -10), t(2, -10)]
    res = evaluate_entry(trades, NOW, 10_000, CFG)
    assert not res.allowed
    assert res.reason.startswith("loss_streak_3")
    assert not res.hard_stop


def test_loss_streak_cooldown_expires():
    trades = [t(80, -10), t(70, -10), t(25, -10)]
    assert evaluate_entry(trades, NOW, 10_000, CFG).allowed


def test_win_breaks_streak():
    trades = [t(30, -10), t(20, -10), t(10, 50), t(2, -10)]
    assert evaluate_entry(trades, NOW, 10_000, CFG).allowed


def test_weekly_loss_blocks():
    trades = [t(100, -400, -0.04), t(5 * 24, 10, 0.001), t(50, -450, -0.045)]
    trades.sort(key=lambda x: x.exit_time)
    res = evaluate_entry(trades, NOW, 10_000, CFG)
    assert not res.allowed and res.reason.startswith("weekly_loss")


def test_daily_loss_includes_unrealized():
    trades = [t(1, -300, -0.03)]
    assert evaluate_entry(trades, NOW, 10_000, CFG).allowed
    res = evaluate_entry(trades, NOW, 10_000, CFG, unrealized_pnl_pct=-0.025)
    assert not res.allowed and res.hard_stop and res.scope == "day"


def test_drawdown_from_peak_is_hard_stop():
    trades = [t(500, 2_000), t(400, -1_000), t(300, 100), t(200, -1_000)]  # zirve 12000 -> 10100 (%15.8)
    res = evaluate_entry(trades, NOW, 10_000, CFG)
    assert not res.allowed and res.hard_stop and res.scope == "halt"


def test_max_trades_per_day():
    cfg = BreakerConfig(max_consecutive_losses=None, max_trades_per_day=2)
    trades = [t(3, 10), t(1, 10)]
    assert not evaluate_entry(trades, NOW, 10_000, cfg).allowed


TARGET = BreakerConfig(max_consecutive_losses=None, max_weekly_loss_pct=None, max_drawdown_pct=None,
                       daily_profit_target_pct=0.05)


def test_daily_target_blocks_rest_of_day_without_kill_switch():
    assert evaluate_entry([t(1, 400, 0.04)], NOW, 10_000, TARGET).allowed
    res = evaluate_entry([t(1, 400, 0.04), t(0.5, 100, 0.01)], NOW, 10_000, TARGET)
    assert not res.allowed and not res.hard_stop and res.scope == "day"
    assert res.reason.startswith("daily_target_reached")


def test_daily_target_counts_unrealized_and_resets_next_day():
    res = evaluate_entry([t(1, 300, 0.03)], NOW, 10_000, TARGET, unrealized_pnl_pct=0.025)
    assert not res.allowed
    # ertesi gun: dunku kazanc bugune sayilmaz
    assert evaluate_entry([t(1, 600, 0.06)], NOW + timedelta(days=1), 10_000, TARGET).allowed


def test_no_target_by_default():
    assert BreakerConfig().daily_profit_target_pct is None
    assert evaluate_entry([t(1, 900, 0.09)], NOW, 10_000, CFG).allowed
