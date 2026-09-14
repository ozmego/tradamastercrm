from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from backtesting import Strategy as BacktestingStrategy
from backtesting.lib import FractionalBacktest

from tradecrm.strategy.base import Signal, Strategy

StrategyFactory = Callable[[], Strategy]


def _make_backtesting_strategy(strategy_factory: StrategyFactory) -> type[BacktestingStrategy]:
    """tradecrm.strategy.base.Strategy'yi backtesting.py'nin Strategy siniflarina
    uyarlayan ince adapter. Ayni generate_signal mantigi, canli calismada
    kullanilan mantikla birebir aynidir (davranis sapmasi yok)."""

    class _Adapter(BacktestingStrategy):
        def init(self) -> None:
            self._tc_strategy = strategy_factory()

        def next(self) -> None:
            # self.data.df, backtesting.py tarafindan su ana kadarki bara
            # sinirlandirilmis (lookahead-safe) bir DataFrame'dir.
            df = self.data.df
            signal = self._tc_strategy.generate_signal(df)
            if signal is Signal.BUY:
                if not self.position:
                    self.buy()
            elif signal is Signal.SELL:
                if self.position:
                    self.position.close()

    return _Adapter


@dataclass
class BacktestResult:
    stats: pd.Series
    trades: pd.DataFrame
    equity_curve: pd.DataFrame


def run_backtest(
    df: pd.DataFrame,
    strategy_factory: StrategyFactory,
    cash: float = 10_000,
    commission: float = 0.001,
) -> BacktestResult:
    """df: Open/High/Low/Close/Volume kolonlu, DatetimeIndex'li DataFrame
    (CCXTDataProvider.fetch_ohlcv_historical ile ayni format).
    strategy_factory: parametresiz cagrildiginda bir Strategy dondurur."""
    bt_strategy_cls = _make_backtesting_strategy(strategy_factory)
    # FractionalBacktest: kripto fiyatlari (orn. BTC) genelde islem basina 1
    # birimden buyuk oldugu icin, standart Backtest tam-birim alım
    # varsayimiyla emirleri "yetersiz teminat" diyerek iptal eder. Kesirli
    # (satoshi seviyesine kadar) birim alimina izin verir.
    # finalize_trades: backtest sonunda hala acik olan pozisyonlari da kapatip
    # istatistiklere dahil eder -- "0 islem ama %27 getiri" gibi kafa
    # karistirici, tutarsiz sonuclari onler.
    bt = FractionalBacktest(
        df, bt_strategy_cls, cash=cash, commission=commission, exclusive_orders=True, finalize_trades=True
    )
    stats = bt.run()
    return BacktestResult(
        stats=stats,
        trades=stats["_trades"],
        equity_curve=stats["_equity_curve"],
    )
