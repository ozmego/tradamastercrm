import pandas as pd

from tradecrm.news.cryptopanic_client import CryptoPanicUnavailableError
from tradecrm.news.models import SentimentResult
from tradecrm.strategy.base import Signal
from tradecrm.strategy.news_aware import NewsAwareStrategy


class _StubInnerStrategy:
    name = "stub_inner"
    required_lookback = 5

    def __init__(self, signal: Signal):
        self._signal = signal
        self.call_count = 0

    def generate_signal(self, df):
        self.call_count += 1
        return self._signal


class _StubNewsClient:
    def __init__(self, items=None, raise_error: Exception | None = None):
        self._items = items or []
        self._raise_error = raise_error
        self.call_count = 0

    def get_recent_posts(self, symbol, hours=6):
        self.call_count += 1
        if self._raise_error is not None:
            raise self._raise_error
        return self._items


class _StubNotifier:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return True


class _StubAuditLogger:
    def __init__(self):
        self.entries = []

    def log(self, event_type, **fields):
        self.entries.append((event_type, fields))


def _make_strategy(inner_signal, news_client=None, **overrides):
    inner = _StubInnerStrategy(inner_signal)
    defaults = {
        "inner": inner,
        "news_client": news_client or _StubNewsClient(),
        "symbol": "BTC/USDT",
        "block_buy_threshold": -0.3,
    }
    defaults.update(overrides)
    strategy = NewsAwareStrategy(**defaults)
    return strategy, inner


_EMPTY_DF = pd.DataFrame({"Close": [1, 2, 3]})


class TestHoldNeverFetchesNews:
    def test_hold_signal_does_not_call_news_client(self):
        news_client = _StubNewsClient()
        strategy, _inner = _make_strategy(Signal.HOLD, news_client=news_client)

        result = strategy.generate_signal(_EMPTY_DF)

        assert result is Signal.HOLD
        assert news_client.call_count == 0


class TestSellAlwaysPassesThrough:
    def test_sell_passes_through_regardless_of_sentiment(self):
        news_client = _StubNewsClient()
        strategy, _inner = _make_strategy(Signal.SELL, news_client=news_client)

        result = strategy.generate_signal(_EMPTY_DF)

        assert result is Signal.SELL
        assert news_client.call_count == 0  # SELL icin haber cekmeye bile gerek yok


class TestBuyWithSentiment:
    def _sentiment_stub_client(self, monkeypatch, score, low_confidence=False):
        news_client = _StubNewsClient(items=["dummy"])  # icerik onemli degil, compute_sentiment_score mock'lanacak

        def fake_compute(items, window_hours, min_votes_threshold, min_items_threshold):
            return SentimentResult(
                score=score,
                sample_size=1,
                total_votes=10,
                window_hours=window_hours,
                low_confidence=low_confidence,
                as_of=None,
            )

        monkeypatch.setattr("tradecrm.strategy.news_aware.compute_sentiment_score", fake_compute)
        return news_client

    def test_buy_blocked_when_sentiment_below_threshold(self, monkeypatch):
        news_client = self._sentiment_stub_client(monkeypatch, score=-0.5)
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client, block_buy_threshold=-0.3)

        result = strategy.generate_signal(_EMPTY_DF)

        assert result is Signal.HOLD

    def test_buy_passes_when_sentiment_above_threshold(self, monkeypatch):
        news_client = self._sentiment_stub_client(monkeypatch, score=0.1)
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client, block_buy_threshold=-0.3)

        result = strategy.generate_signal(_EMPTY_DF)

        assert result is Signal.BUY

    def test_buy_passes_when_sentiment_low_confidence(self, monkeypatch):
        news_client = self._sentiment_stub_client(monkeypatch, score=-0.9, low_confidence=True)
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client, block_buy_threshold=-0.3)

        result = strategy.generate_signal(_EMPTY_DF)

        # dusuk guven -> "bilmiyoruz" -> notr sayilir, BUY degismeden gecer
        assert result is Signal.BUY

    def test_blocked_buy_is_audit_logged(self, monkeypatch):
        news_client = self._sentiment_stub_client(monkeypatch, score=-0.5)
        audit_logger = _StubAuditLogger()
        strategy, _inner = _make_strategy(
            Signal.BUY, news_client=news_client, block_buy_threshold=-0.3, audit_logger=audit_logger
        )

        strategy.generate_signal(_EMPTY_DF)

        event_types = [e for e, _ in audit_logger.entries]
        assert "news_blocked_buy" in event_types


class TestFallbackOnUnavailable:
    def test_falls_back_to_technical_signal_on_error(self):
        news_client = _StubNewsClient(raise_error=CryptoPanicUnavailableError("timeout"))
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client)

        result = strategy.generate_signal(_EMPTY_DF)

        assert result is Signal.BUY  # teknik sinyal aynen geçti

    def test_unavailable_is_audit_logged(self):
        news_client = _StubNewsClient(raise_error=CryptoPanicUnavailableError("timeout"))
        audit_logger = _StubAuditLogger()
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client, audit_logger=audit_logger)

        strategy.generate_signal(_EMPTY_DF)

        event_types = [e for e, _ in audit_logger.entries]
        assert "news_unavailable_fallback_technical_only" in event_types

    def test_unavailable_sends_throttled_notification(self):
        news_client = _StubNewsClient(raise_error=CryptoPanicUnavailableError("timeout"))
        notifier = _StubNotifier()
        strategy, _inner = _make_strategy(Signal.BUY, news_client=news_client, notifier=notifier)

        strategy.generate_signal(_EMPTY_DF)
        strategy.generate_signal(_EMPTY_DF)  # ikinci cagri throttle nedeniyle bildirim gondermemeli

        assert len(notifier.messages) == 1


class TestNameAndLookback:
    def test_name_reflects_inner_strategy(self):
        strategy, inner = _make_strategy(Signal.HOLD)
        assert inner.name in strategy.name

    def test_lookback_matches_inner(self):
        strategy, inner = _make_strategy(Signal.HOLD)
        assert strategy.required_lookback == inner.required_lookback
