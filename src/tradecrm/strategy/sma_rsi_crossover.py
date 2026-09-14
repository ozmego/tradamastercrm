import pandas as pd
import pandas_ta as ta

from tradecrm.strategy.base import Signal, Strategy


class EmaRsiCrossoverStrategy(Strategy):
    """Baslangic stratejisi: hizli/yavas EMA kesisimi + RSI filtresi.

    - EMA(fast) yukari yonlu EMA(slow)'u kesince VE RSI asiri-alim
      esiginin altindaysa BUY.
    - EMA(fast) asagi yonlu EMA(slow)'u kesince SELL (RSI'dan bagimsiz --
      bu bir cikis/stop sinyali, filtrelenmemeli).
    - Diger her durumda HOLD.
    """

    name = "ema_rsi_crossover"

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period slow_period'dan kucuk olmali")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.required_lookback = slow_period + rsi_period + 1

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.required_lookback:
            return Signal.HOLD

        close = df["Close"]
        ema_fast = ta.ema(close, length=self.fast_period)
        ema_slow = ta.ema(close, length=self.slow_period)
        rsi = ta.rsi(close, length=self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return Signal.HOLD
        if ema_fast.iloc[-2:].isna().any() or ema_slow.iloc[-2:].isna().any() or pd.isna(rsi.iloc[-1]):
            return Signal.HOLD

        prev_diff = ema_fast.iloc[-2] - ema_slow.iloc[-2]
        curr_diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]

        return decide_signal(prev_diff, curr_diff, rsi.iloc[-1], self.rsi_overbought)


def decide_signal(prev_diff: float, curr_diff: float, rsi_value: float, rsi_overbought: float) -> Signal:
    """Saf karar mantigi: EMA fark(fast-slow) kesisimi + RSI filtresi.

    generate_signal'dan ayrildi ki gostergelerin (ema/rsi) kendisini
    yeniden hesaplamadan, kesisim/filtre mantigi deterministik olarak
    test edilebilsin.
    """
    crossed_up = prev_diff <= 0 and curr_diff > 0
    crossed_down = prev_diff >= 0 and curr_diff < 0

    if crossed_up and rsi_value < rsi_overbought:
        return Signal.BUY
    if crossed_down:
        return Signal.SELL
    return Signal.HOLD
