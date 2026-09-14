from tradecrm.data.provider import CCXTDataProvider


class _FakeExchange:
    """Sayfalama davranisini test etmek icin ccxt.Exchange yerine gecen sahte borsa."""

    rateLimit = 0

    def __init__(self, pages: list[list[list]]):
        self._pages = pages
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=1000):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit})
        if not self._pages:
            return []
        return self._pages.pop(0)

    def fetch_ticker(self, symbol):
        return {"last": 12345.6}


def _candle(ts_ms: int, price: float) -> list:
    return [ts_ms, price, price + 1, price - 1, price, 10.0]


def test_fetch_ohlcv_historical_paginates_until_short_page():
    page1 = [_candle(i * 60_000, 100 + i) for i in range(3)]
    page2 = [_candle((3 + i) * 60_000, 200 + i) for i in range(2)]
    fake = _FakeExchange(pages=[page1, page2])
    provider = CCXTDataProvider(exchange=fake)

    df = provider.fetch_ohlcv_historical("BTC/USDT", timeframe="1m", limit_per_call=3)

    assert len(df) == 5
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.is_monotonic_increasing
    assert len(fake.calls) == 2


def test_fetch_ohlcv_historical_empty_result():
    fake = _FakeExchange(pages=[])
    provider = CCXTDataProvider(exchange=fake)

    df = provider.fetch_ohlcv_historical("BTC/USDT")

    assert df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_fetch_latest_ohlcv_shape():
    page = [_candle(i * 60_000, 100 + i) for i in range(5)]
    fake = _FakeExchange(pages=[page])
    provider = CCXTDataProvider(exchange=fake)

    df = provider.fetch_latest_ohlcv("BTC/USDT", timeframe="1m", limit=5)

    assert len(df) == 5
    assert df.iloc[0]["Close"] == 100


def test_fetch_latest_price():
    fake = _FakeExchange(pages=[])
    provider = CCXTDataProvider(exchange=fake)

    assert provider.fetch_latest_price("BTC/USDT") == 12345.6
