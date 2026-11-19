"""BrokerAdapter: kod tabaninin geri kalani emir icin asla dogrudan ccxt'ye
(ya da baska bir borsa SDK'sina) dokunmaz, sadece bu arayuz uzerinden.

Emirler OrderManager (broker/order_manager.py) uzerinden gelir: her emrin
deterministik bir client_order_id'si vardir ve once veritabanina kaydedilir.
Adapter'in sozlesmesi:

- submit_order ayni client_order_id ile ikinci kez cagrilirsa ikinci bir
  emir OLUSTURMAMALIDIR (borsa tarafinda newClientOrderId tekilligi; paper'da
  DB kontrolu).
- Ag/zaman asimi hatasinda BrokerTimeoutError firlatir -- emir borsada
  olusmus OLABILIR; cagiran get_order ile sorgular, kor tekrar gondermez.
- get_order(client_order_id): borsadaki emrin son durumu ya da (borsa
  emri hic gormediyse) None.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from tradecrm.db.models import Trade
from tradecrm.strategy.base import Signal


class BrokerError(Exception):
    """Kesin hata: emir olusmadi (orn. reddedildi, yetersiz bakiye)."""


class BrokerTimeoutError(BrokerError):
    """Sonuc belirsiz: emir borsada olusmus olabilir."""


@dataclass
class OrderResult:
    executed: bool
    reason: str
    trade_id: int | None = None
    price: float | None = None
    qty: float | None = None
    client_order_id: str | None = None
    exchange_order_id: str | None = None


@dataclass
class PositionView:
    symbol: str
    qty: float
    cost: float  # giris maliyeti (komisyon dahil)
    price: float  # guncel fiyat
    market_value: float
    trade_id: int | None = None


@dataclass
class AccountSnapshot:
    """Hesap durumu -- uc deger AYRI hesaplanir:
    cash        : kullanilabilir nakit (acik pozisyonlara baglananlar haric)
    allocated   : acik pozisyonlarin maliyeti (komisyon dahil)
    market_value: acik pozisyonlarin guncel piyasa degeri
    equity      : cash + market_value (toplam portfoy degeri)
    """

    cash: float
    allocated: float
    market_value: float
    positions: list[PositionView] = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def realized_equity(self) -> float:
        """cash + maliyet: sadece gerceklesen P&L'i yansitir (devre
        kesiciler simulatorle ayni tanimi kullanir)."""
        return self.cash + self.allocated

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.allocated

    @property
    def exposure_fraction(self) -> float:
        return self.market_value / self.equity if self.equity > 0 else 0.0


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def get_snapshot(self) -> AccountSnapshot:
        """Fiyat ya da bakiye alinamazsa istisna firlatir (cagiran yeni
        alimlari durdurur)."""
        raise NotImplementedError

    @abstractmethod
    def get_open_position(self, symbol: str) -> Trade | None:
        raise NotImplementedError

    @abstractmethod
    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: Signal,
        notional: float | None,
        strategy_name: str,
        reason: str | None = None,
    ) -> OrderResult:
        """side=BUY: `notional` quote tutari (komisyon dahil) kadar alim.
        side=SELL: notional yoksayilir, o sembol icin acik pozisyonu kapatir."""
        raise NotImplementedError

    @abstractmethod
    def get_order(self, client_order_id: str) -> OrderResult | None:
        raise NotImplementedError

    def reconcile_positions(self) -> list[str]:
        """Borsadaki pozisyon/bakiye ile yerel kayit arasindaki uyusmazliklar
        (bos liste = tutarli). Canli adapter borsaya sormali."""
        return []

    def get_balance(self) -> float:
        """Geriye uyumluluk: gerceklesen equity (nakit + maliyet)."""
        return self.get_snapshot().realized_equity
