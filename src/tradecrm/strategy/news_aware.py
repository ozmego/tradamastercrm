import logging
from datetime import UTC, datetime

import pandas as pd

from tradecrm.news.cryptopanic_client import (
    CryptoPanicClient,
    CryptoPanicUnavailableError,
)
from tradecrm.news.sentiment import compute_sentiment_score
from tradecrm.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class NewsAwareStrategy(Strategy):
    """Ic (teknik) stratejiyi haber sentiment'i ile sarmalar.

    - Ic sinyal HOLD ise haber hic cekilmez, aynen doner (gereksiz API
      cagrisi yok).
    - BUY ise sentiment cekilir: dusuk guvenli (yetersiz oy/haber) ise
      BUY degismeden geçer ("bilmiyoruz" notr sayilir, bearish sayilmaz);
      skor esik altindaysa BUY, HOLD'a dusurulur.
    - SELL sinyalleri sentiment'ten bagimsiz HER ZAMAN aynen geçer --
      spot piyasada SELL bir pozisyonu kapatmaktir, ikincil bir haber
      filtresinin bir cikisi/stop-loss'u bastirmasina izin verilmez.
    - CryptoPanic'e erisilemezse ic (teknik-only) sinyale donulur;
      "bilmiyoruz"u otomatik bearish/durdurma saymak, riski azaltmak
      yerine kullanilabilirligi gereksiz dusurur.
    """

    def __init__(
        self,
        inner: Strategy,
        news_client: CryptoPanicClient,
        symbol: str,
        window_hours: int = 6,
        block_buy_threshold: float = -0.3,
        min_votes_threshold: int = 5,
        min_items_threshold: int = 3,
        notifier=None,
        audit_logger=None,
        alert_throttle_seconds: int = 3600,
    ) -> None:
        self.inner = inner
        self.news_client = news_client
        self.symbol = symbol
        self.window_hours = window_hours
        self.block_buy_threshold = block_buy_threshold
        self.min_votes_threshold = min_votes_threshold
        self.min_items_threshold = min_items_threshold
        self.notifier = notifier
        self.audit_logger = audit_logger
        self.alert_throttle_seconds = alert_throttle_seconds
        self._last_unavailable_alert_at: datetime | None = None

        self.name = f"news_aware({inner.name})"
        self.required_lookback = inner.required_lookback

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        base_signal = self.inner.generate_signal(df)

        if base_signal is not Signal.BUY:
            return base_signal

        try:
            items = self.news_client.get_recent_posts(self.symbol, hours=self.window_hours)
        except CryptoPanicUnavailableError as exc:
            self._handle_unavailable(exc)
            return base_signal

        sentiment = compute_sentiment_score(
            items,
            self.window_hours,
            min_votes_threshold=self.min_votes_threshold,
            min_items_threshold=self.min_items_threshold,
        )

        if sentiment.low_confidence:
            return base_signal

        if sentiment.score <= self.block_buy_threshold:
            if self.audit_logger is not None:
                self.audit_logger.log(
                    "news_blocked_buy",
                    symbol=self.symbol,
                    sentiment_score=sentiment.score,
                    sample_size=sentiment.sample_size,
                )
            return Signal.HOLD

        return base_signal

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Backtest edilemez: CryptoPanic sadece GUNCEL haberleri verir;
        gecmis barlara bugunun haberini uygulamak lookahead'dir."""
        raise NotImplementedError(
            "NewsAwareStrategy backtest edilemez (gecmis haber verisi yok); ic stratejiyi backtest edin."
        )

    def _handle_unavailable(self, exc: Exception) -> None:
        logger.warning("CryptoPanic erisilemedi, teknik-only sinyale donuluyor: %s", exc)
        if self.audit_logger is not None:
            self.audit_logger.log("news_unavailable_fallback_technical_only", symbol=self.symbol, error=str(exc))

        if self.notifier is None:
            return
        now = datetime.now(UTC)
        if self._last_unavailable_alert_at is not None:
            elapsed = (now - self._last_unavailable_alert_at).total_seconds()
            if elapsed < self.alert_throttle_seconds:
                return
        self.notifier.send_message(
            f"TradeCRM: CryptoPanic haber servisine erisilemiyor ({self.symbol}), "
            f"teknik-only sinyale donuldu. Hata: {exc}"
        )
        self._last_unavailable_alert_at = now
