import pandas as pd
import pandas_ta as ta
import pytest

from tradecrm.strategy.base import Signal
from tradecrm.strategy.sma_rsi_crossover import EmaRsiCrossoverStrategy, decide_signal


class TestDecideSignal:
    """Kesisim + RSI filtresi karar mantigi -- deterministik, gostergelerden bagimsiz."""

    def test_cross_up_with_rsi_below_overbought_is_buy(self):
        assert decide_signal(prev_diff=-1.0, curr_diff=0.5, rsi_value=55.0, rsi_overbought=70.0) is Signal.BUY

    def test_cross_up_with_rsi_overbought_is_hold(self):
        # RSI filtresi olmasa BUY olurdu; asiri-alim bolgesinde BUY engellenmeli.
        assert decide_signal(prev_diff=-1.0, curr_diff=0.5, rsi_value=75.0, rsi_overbought=70.0) is Signal.HOLD

    def test_cross_down_is_sell_regardless_of_rsi(self):
        # Cikis sinyali RSI'dan bagimsiz gecmeli.
        assert decide_signal(prev_diff=1.0, curr_diff=-0.5, rsi_value=20.0, rsi_overbought=70.0) is Signal.SELL
        assert decide_signal(prev_diff=1.0, curr_diff=-0.5, rsi_value=90.0, rsi_overbought=70.0) is Signal.SELL

    def test_no_cross_is_hold(self):
        assert decide_signal(prev_diff=1.0, curr_diff=1.2, rsi_value=50.0, rsi_overbought=70.0) is Signal.HOLD
        assert decide_signal(prev_diff=-1.0, curr_diff=-1.2, rsi_value=50.0, rsi_overbought=70.0) is Signal.HOLD


class TestEmaRsiCrossoverStrategy:
    def test_invalid_periods_raise(self):
        with pytest.raises(ValueError):
            EmaRsiCrossoverStrategy(fast_period=26, slow_period=12)

    def test_insufficient_data_is_hold(self):
        strategy = EmaRsiCrossoverStrategy(fast_period=2, slow_period=3, rsi_period=2)
        df = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})
        assert strategy.generate_signal(df) is Signal.HOLD

    def test_generate_signal_matches_manually_computed_indicators(self):
        """Dusus sonrasi guclu yukselisle olusan gercek bir seride,
        strategy.generate_signal'in sonucu, ayni indikatorlerin dogrudan
        pandas_ta ile hesaplanip decide_signal'e verilmesiyle bulunan
        sonucla birebir eslesmeli."""
        strategy = EmaRsiCrossoverStrategy(fast_period=5, slow_period=10, rsi_period=14, rsi_overbought=70.0)

        declining = [100 - i * 0.5 for i in range(20)]
        rising = [declining[-1] + i * 0.3 for i in range(1, 15)]
        closes = declining + rising
        df = pd.DataFrame({"Close": closes})

        signal = strategy.generate_signal(df)

        close = df["Close"]
        ema_fast = ta.ema(close, length=5)
        ema_slow = ta.ema(close, length=10)
        rsi = ta.rsi(close, length=14)
        prev_diff = ema_fast.iloc[-2] - ema_slow.iloc[-2]
        curr_diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]
        expected = decide_signal(prev_diff, curr_diff, rsi.iloc[-1], 70.0)

        assert signal is expected
        assert signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
