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
