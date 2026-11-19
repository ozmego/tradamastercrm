from pathlib import Path

from tradecrm.backtest.engine import BacktestResult

_SUMMARY_KEYS = [
    "Start",
    "End",
    "Duration",
    "Return [%]",
    "Buy & Hold Return [%]",
    "Max. Drawdown [%]",
    "# Trades",
    "Win Rate [%]",
    "Sharpe Ratio",
]


def summarize(result: BacktestResult) -> str:
    lines = [f"{key}: {result.stats.get(key)}" for key in _SUMMARY_KEYS if key in result.stats]
    return "\n".join(lines)


def save_trade_log(result: BacktestResult, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    result.trades.to_csv(path, index=False)


def save_equity_curve(result: BacktestResult, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(path)


# --- Simulator / walk-forward raporlari ----------------------------------------


def format_metrics(m) -> str:
    return (
        f"islem={m.n_trades}  kazanma={m.win_rate:.1%}  PF={m.profit_factor:.2f}  "
        f"beklenti/islem={m.expectancy_pct:+.3%}  getiri={m.total_return:+.1%}  CAGR={m.cagr:+.1%}  "
        f"maxDD={m.max_drawdown:.1%}  Sharpe={m.sharpe:.2f}  pozisyonda={m.exposure:.0%}"
    )


def format_table(rows: list[dict], columns: list[tuple[str, str, str]]) -> str:
    """columns: (baslik, anahtar, format)."""
    header = [c[0] for c in columns]
    body = [[format(r[c[1]], c[2]) if c[2] else str(r[c[1]]) for c in columns] for r in rows]
    widths = [max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(header)]
    line = lambda cells: "  ".join(cell.rjust(w) for cell, w in zip(cells, widths, strict=True))
    return "\n".join([line(header), line(["-" * w for w in widths]), *(line(b) for b in body)])
