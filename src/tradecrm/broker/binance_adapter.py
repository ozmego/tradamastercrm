"""Binance spot (geriye donuk uyumluluk): asil uygulama broker/ccxt_spot_adapter.py."""

from __future__ import annotations

from sqlmodel import Session

from tradecrm.broker.ccxt_spot_adapter import BINANCE, CcxtSpotAdapter
from tradecrm.broker.ccxt_spot_adapter import make_exchange as _make_exchange


def make_exchange(api_key: str, api_secret: str, testnet: bool = False, exchange=None):
    """Kimlik bilgili ccxt binance (spot). testnet=True: testnet.binance.vision."""
    return exchange if exchange is not None else _make_exchange(BINANCE, api_key, api_secret, sandbox=testnet)


class BinanceSpotAdapter(CcxtSpotAdapter):
    def __init__(self, session: Session, exchange, initial_capital: float, account: str | None = None,
                 **kwargs) -> None:
        super().__init__(session, exchange, BINANCE, initial_capital, account, **kwargs)
