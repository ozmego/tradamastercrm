"""BrokerAdapter: kod tabaninin geri kalani emir icin asla dogrudan ccxt'ye
(ya da baska bir borsa SDK'sina) dokunmaz, sadece bu arayuz uzerinden.
RiskManager'in onaylanmis kararindan SONRA cagrilir -- guvenlik kontrolu
burada tekrar yapilmaz, cagiran (runner) zaten risk check'i gecirmis olmali.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tradecrm.db.models import Trade
from tradecrm.strategy.base import Signal


@dataclass
class OrderResult:
    executed: bool
    reason: str
    trade_id: int | None = None
    price: float | None = None
    qty: float | None = None


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def get_balance(self) -> float:
        """Toplam hesap bakiyesi (quote currency, orn. USDT)."""
        raise NotImplementedError

    @abstractmethod
    def get_open_position(self, symbol: str) -> Trade | None:
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: Signal,
        capital_fraction: float,
        strategy_name: str,
        signal_reason: str | None = None,
    ) -> OrderResult:
        """side=BUY: capital_fraction (0..1, RiskManager tarafindan onaylanmis)
        kadar TOPLAM bakiyeyi kullanarak yeni pozisyon acar.
        side=SELL: capital_fraction yoksayilir, o sembol icin acik pozisyonu
        (varsa) tamamen kapatir."""
        raise NotImplementedError
