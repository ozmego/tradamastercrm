"""Borsa-bagimsiz ccxt spot canli adapter'i (Faz 10). Binance ve KuCoin.

Sozlesme base.BrokerAdapter ile ayni; paper adapter'in yerine gecer:

- Emirler gercek borsaya gider: MARKET alim tutar (quote) ile, MARKET satis
  miktar ile; her emirde borsanin "istemci emir kimligi" parametresi =
  OrderManager'in deterministik kimligi -> ayni kimlikle ikinci emir olusmaz.
- Dolum bilgisi (ortalama fiyat, net miktar, komisyon) borsadan alinir ve
  Trade tablosuna paper ile AYNI muhasebe tanimiyla yazilir:
  maliyet = entry_price * qty + entry_fees.
  Bazi borsalar (KuCoin) emir yanitinda dolum dondurmez: dolum, ayni kimlikle
  kisa sure sorgulanarak teyit edilir.
- Butce: bot hesaptaki butun USDT'yi degil, kayitli baslangic sermayesini
  (Account.initial_capital) + gerceklesen K/Z'yi yonetir; ayrica borsadaki
  gercek serbest USDT'yi asamaz. Hesaptaki diger varliklara dokunmaz.
- Uzlastirma: acik pozisyonlarin miktari borsadaki bakiyeyle karsilastirilir;
  borsada eksikse (elle satilmis vb.) "uyusmazlik" -> yeni alim durur.

Borsaya ozgu farklar ExchangeSpec'te. Para cekme yetkisi GEREKMEZ ve
olmamalidir (bkz. tradecrm live check).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import ccxt
from sqlmodel import Session, or_, select

from tradecrm.broker.base import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerError,
    BrokerTimeoutError,
    OrderResult,
    PositionView,
)
from tradecrm.db.models import Order, Trade
from tradecrm.db.repository import TradeRepository
from tradecrm.strategy.base import Signal

logger = logging.getLogger(__name__)

QUOTE = "USDT"
# Borsadaki bakiye kayitli miktarin bu oranindan azsa uyusmazlik (komisyon/yuvarlama payi).
POSITION_TOLERANCE = 0.01
TERMINAL_EMPTY = ("canceled", "rejected", "expired")


@dataclass(frozen=True)
class ExchangeSpec:
    id: str  # ccxt borsa kimligi ve hesap adi
    title: str
    client_id_param: str  # emir olustururken istemci kimligi parametresi
    query_param: str  # kimlikle emir sorgulama parametresi
    not_found_markers: tuple[str, ...]  # "emir yok" hata metni parcalari
    duplicate_markers: tuple[str, ...] = ()  # "kimlik zaten var" hata metni parcalari
    balance_params: dict = field(default_factory=dict)  # islem hesabinin bakiyesi
    needs_passphrase: bool = False
    has_sandbox: bool = False


BINANCE = ExchangeSpec(
    id="binance", title="Binance", client_id_param="newClientOrderId", query_param="origClientOrderId",
    not_found_markers=("-2013", "does not exist"), has_sandbox=True,
)
KUCOIN = ExchangeSpec(
    id="kucoin", title="KuCoin", client_id_param="clientOid", query_param="clientOid",
    not_found_markers=("400100", "order not exist", "not exist", "does not exist"),
    duplicate_markers=("126044",),
    balance_params={"type": "trade"},  # Trading hesabi (Funding = "main" ayri)
    needs_passphrase=True,
)
SPECS: dict[str, ExchangeSpec] = {s.id: s for s in (BINANCE, KUCOIN)}


def get_spec(exchange_id: str) -> ExchangeSpec:
    if exchange_id not in SPECS:
        raise KeyError(f"desteklenmeyen borsa: {exchange_id}. Secenekler: {', '.join(SPECS)}")
    return SPECS[exchange_id]


def make_exchange(spec: ExchangeSpec, api_key: str, api_secret: str, passphrase: str = "", sandbox: bool = False):
    """Kimlik bilgili ccxt spot istemcisi."""
    cfg = {
        "apiKey": api_key, "secret": api_secret, "enableRateLimit": True,
        "options": {"defaultType": "spot", "adjustForTimeDifference": True},
    }
    if spec.needs_passphrase:
        cfg["password"] = passphrase
    ex = getattr(ccxt, spec.id)(cfg)
    if sandbox:
        if not spec.has_sandbox:
            raise ValueError(f"{spec.title} icin test ortami yok")
        ex.set_sandbox_mode(True)
    return ex


def _has_marker(exc: Exception, markers: tuple[str, ...]) -> bool:
    msg = str(exc).lower()
    return any(m.lower() in msg for m in markers)


class CcxtSpotAdapter(BrokerAdapter):
    def __init__(self, session: Session, exchange, spec: ExchangeSpec, initial_capital: float,
                 account: str | None = None, fill_poll_attempts: int = 6, fill_poll_interval: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.spec = spec
        self.name = f"{spec.id}:{account}" if account else spec.id
        self.session = session
        self.exchange = exchange
        self.initial_capital = initial_capital
        self.trades = TradeRepository(session)
        self.fill_poll_attempts = fill_poll_attempts
        self.fill_poll_interval = fill_poll_interval
        self._sleep = sleep
        self._markets_loaded = False

    # ------------------------------------------------------------- yardimci
    def _markets(self):
        if not self._markets_loaded:
            self.exchange.load_markets()
            self._markets_loaded = True
        return self.exchange.markets

    def _price(self, symbol: str) -> float:
        t = self.exchange.fetch_ticker(symbol)
        price = t.get("last") or t.get("close")
        if not price or price <= 0:
            raise BrokerError(f"gecersiz fiyat {symbol}: {price}")
        return float(price)

    def _call(self, fn, *args, **kwargs):
        """ccxt hatalarini adapter sozlesmesine cevirir."""
        try:
            return fn(*args, **kwargs)
        except (ccxt.RequestTimeout, ccxt.NetworkError, ccxt.DDoSProtection) as exc:
            # Ag/zaman asimi/hiz siniri: emir olusmus olabilir -> cagiran get_order ile sorar.
            raise BrokerTimeoutError(f"{type(exc).__name__}: {exc}") from exc
        except ccxt.BaseError as exc:
            raise BrokerError(f"{type(exc).__name__}: {exc}") from exc

    def _balance(self) -> dict:
        return self._call(self.exchange.fetch_balance, dict(self.spec.balance_params))

    def _fee_in_quote(self, order: dict, base: str, avg_price: float) -> tuple[float, float]:
        """(quote cinsinden komisyon, base cinsinden kesilen miktar)."""
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        quote_fee, base_fee = 0.0, 0.0
        for f in fees:
            cost = float(f.get("cost") or 0.0)
            cur = f.get("currency")
            if not cost:
                continue
            if cur == base:
                base_fee += cost
                quote_fee += cost * avg_price
            elif cur == QUOTE:
                quote_fee += cost
            else:  # orn. BNB/KCS ile odenen komisyon: USDT karsiligi
                try:
                    quote_fee += cost * self._price(f"{cur}/{QUOTE}")
                except Exception:  # noqa: BLE001 -- komisyon tahmini emri bozmamali
                    logger.warning("%s komisyonu USDT'ye cevrilemedi", cur)
        return quote_fee, base_fee

    # ------------------------------------------------------------- hesap
    def _budget_cash(self, open_trades: list[Trade]) -> float:
        closed = self.session.exec(select(Trade).where(Trade.broker == self.name, Trade.status == "CLOSED"))
        realized = sum(t.realized_pnl or 0.0 for t in closed)
        cost = sum(t.entry_price * t.qty + (t.entry_fees or 0.0) for t in open_trades)
        return self.initial_capital + realized - cost

    def free_quote(self) -> float:
        return float((self._balance().get(QUOTE) or {}).get("free") or 0.0)

    def get_snapshot(self) -> AccountSnapshot:
        open_trades = self.trades.get_open_trades(broker=self.name)
        # Bot butcesi; ama borsadaki gercek serbest USDT'yi asamaz.
        cash = min(self._budget_cash(open_trades), self.free_quote())
        positions = []
        for t in open_trades:
            price = self._call(self._price, t.symbol)
            cost = t.entry_price * t.qty + (t.entry_fees or 0.0)
            positions.append(PositionView(t.symbol, t.qty, cost, price, t.qty * price, t.id))
        return AccountSnapshot(
            cash=max(cash, 0.0),
            allocated=sum(p.cost for p in positions),
            market_value=sum(p.market_value for p in positions),
            positions=positions,
        )

    def get_balance(self) -> float:
        open_trades = self.trades.get_open_trades(broker=self.name)
        return self._budget_cash(open_trades) + sum(t.entry_price * t.qty + (t.entry_fees or 0.0) for t in open_trades)

    def get_open_position(self, symbol: str) -> Trade | None:
        open_trades = self.trades.get_open_trades(symbol=symbol, broker=self.name)
        return open_trades[0] if open_trades else None

    # -------------------------------------------------------------- emirler
    def _local(self, client_order_id: str) -> OrderResult | None:
        trade = self.session.exec(
            select(Trade).where(
                Trade.broker == self.name,
                or_(Trade.entry_client_order_id == client_order_id, Trade.exit_client_order_id == client_order_id),
            )
        ).first()
        if trade is None:
            return None
        if trade.entry_client_order_id == client_order_id:
            return OrderResult(True, "filled", trade.id, trade.entry_price, trade.qty, client_order_id)
        return OrderResult(True, "closed", trade.id, trade.exit_price, trade.qty, client_order_id)

    def _query(self, symbol: str, coid: str) -> dict | None:
        """Borsadaki emir (kimlikle) ya da borsa emri hic gormediyse None."""
        try:
            return self.exchange.fetch_order(None, symbol, {self.spec.query_param: coid})
        except Exception as exc:
            if isinstance(exc, ccxt.OrderNotFound) or _has_marker(exc, self.spec.not_found_markers):
                return None
            if isinstance(exc, (ccxt.RequestTimeout, ccxt.NetworkError, ccxt.DDoSProtection)):
                raise BrokerTimeoutError(f"{type(exc).__name__}: {exc}") from exc
            raise

    def _place(self, fn, *args, symbol: str, coid: str):
        """Emri gonderir. Borsa 'bu kimlik zaten var' derse TEKRAR GONDERMEZ, sorgular."""
        try:
            return self._call(fn, *args)
        except BrokerError as exc:
            if self.spec.duplicate_markers and _has_marker(exc, self.spec.duplicate_markers):
                found = self._query(symbol, coid)
                if found is not None:
                    return found
                raise BrokerTimeoutError(f"kimlik borsada var ama emir okunamadi: {exc}") from exc
            raise

    def _await_fill(self, order: dict, symbol: str, coid: str) -> dict:
        """Yanitta dolum yoksa (KuCoin) ayni kimlikle kisa sure sorgular."""
        current = order or {}
        for _ in range(self.fill_poll_attempts):
            if float(current.get("filled") or 0.0) > 0 or current.get("status") in TERMINAL_EMPTY:
                return current
            self._sleep(self.fill_poll_interval)
            found = self._query(symbol, coid)
            if found is not None:
                current = found
        return current

    def submit_order(self, client_order_id: str, symbol: str, side: Signal, notional: float | None,
                     strategy_name: str, reason: str | None = None) -> OrderResult:
        if side is Signal.HOLD:
            return OrderResult(False, "hold_no_action")
        done = self._local(client_order_id)
        if done is not None:
            return done  # ayni kimlik ikinci emir uretmez
        market = self._markets().get(symbol)
        if market is None or not market.get("active", True):
            raise BrokerError(f"sembol islem gormuyor: {symbol}")
        params = {self.spec.client_id_param: client_order_id}

        if side is Signal.BUY:
            if self.get_open_position(symbol) is not None:
                return OrderResult(False, "position_already_open", client_order_id=client_order_id)
            if not notional or notional <= 0:
                return OrderResult(False, "zero_notional", client_order_id=client_order_id)
            min_cost = ((market.get("limits") or {}).get("cost") or {}).get("min")
            if min_cost and notional < float(min_cost):
                raise BrokerError(f"min_notional: {notional:.2f} < {min_cost} {QUOTE}")
            if notional > self.free_quote() + 1e-9:
                raise BrokerError(f"insufficient_cash ({self.spec.title} islem hesabindaki serbest USDT yetersiz)")
            cost = float(self.exchange.cost_to_precision(symbol, notional))
            order = self._place(self.exchange.create_market_buy_order_with_cost, symbol, cost, params,
                                symbol=symbol, coid=client_order_id)
            order = self._await_fill(order, symbol, client_order_id)
            return self._apply_buy(order, symbol, strategy_name, reason, client_order_id)

        trade = self.get_open_position(symbol)
        if trade is None:
            return OrderResult(False, "no_open_position", client_order_id=client_order_id)
        base = market["base"]
        free_base = float((self._balance().get(base) or {}).get("free") or 0.0)
        qty = min(trade.qty, free_base)
        amount = float(self.exchange.amount_to_precision(symbol, qty)) if qty > 0 else 0.0
        if amount <= 0:
            raise BrokerError(f"satilacak {base} bakiyesi yok (kayit {trade.qty}, borsa {free_base})")
        order = self._place(self.exchange.create_order, symbol, "market", "sell", amount, None, params,
                            symbol=symbol, coid=client_order_id)
        order = self._await_fill(order, symbol, client_order_id)
        return self._apply_sell(order, trade, reason, client_order_id)

    def _filled(self, order: dict) -> tuple[float, float, float]:
        filled = float(order.get("filled") or 0.0)
        cost = float(order.get("cost") or 0.0)
        avg = float(order.get("average") or (cost / filled if filled else 0.0))
        return filled, cost, avg

    def _apply_buy(self, order: dict, symbol: str, strategy_name: str, reason: str | None,
                   coid: str) -> OrderResult:
        filled, _cost, avg = self._filled(order)
        if filled <= 0:
            status = order.get("status")
            if status in TERMINAL_EMPTY:
                return OrderResult(False, f"order_{status}", client_order_id=coid, exchange_order_id=order.get("id"))
            raise BrokerTimeoutError(f"emir dolmadi (durum {status}); uzlastirmada tekrar sorulacak")
        base = self._markets()[symbol]["base"]
        fee_quote, fee_base = self._fee_in_quote(order, base, avg)
        qty = filled - fee_base
        trade = self.trades.open_trade(
            symbol=symbol, strategy_name=strategy_name, broker=self.name,
            entry_time=_ts(order), entry_price=avg, qty=qty, account_balance_at_entry=self.get_balance(),
            # Maliyet = avg*net_qty + fee_quote = odenen USDT (+ BNB/KCS komisyonu karsiligi).
            signal_reason=reason, entry_fees=fee_quote, entry_client_order_id=coid,
        )
        return OrderResult(True, "filled", trade.id, avg, qty, coid, exchange_order_id=str(order.get("id")))

    def _apply_sell(self, order: dict, trade: Trade, reason: str | None, coid: str) -> OrderResult:
        filled, cost, avg = self._filled(order)
        if filled <= 0:
            status = order.get("status")
            if status in TERMINAL_EMPTY:
                return OrderResult(False, f"order_{status}", client_order_id=coid, exchange_order_id=order.get("id"))
            raise BrokerTimeoutError(f"satis dolmadi (durum {status}); uzlastirmada tekrar sorulacak")
        base = self._markets()[trade.symbol]["base"]
        fee_quote, _ = self._fee_in_quote(order, base, avg)
        # Yuvarlama artigi (dust) satilamadiysa sifir degerli sayilir: kayitli miktar
        # uzerinden ETKIN cikis fiyati = gercek brut hasilat / kayitli miktar.
        effective_exit = cost / trade.qty if trade.qty else avg
        closed = self.trades.close_trade(
            trade.id, exit_time=_ts(order), exit_price=effective_exit, fees=fee_quote, exit_reason=reason,
            exit_client_order_id=coid,
        )
        return OrderResult(True, "closed", closed.id, effective_exit, closed.qty, coid,
                           exchange_order_id=str(order.get("id")))

    def get_order(self, client_order_id: str) -> OrderResult | None:
        done = self._local(client_order_id)
        if done is not None:
            return done
        row = self.session.exec(select(Order).where(Order.client_order_id == client_order_id)).first()
        if row is None:
            return None
        order = self._query(row.symbol, client_order_id)
        if not order or float(order.get("filled") or 0.0) <= 0:
            if order and order.get("status") in TERMINAL_EMPTY:
                return OrderResult(False, f"order_{order.get('status')}", client_order_id=client_order_id)
            return None
        # Borsada dolmus ama yerelde kaydi yok (zaman asimi sonrasi): kaydi simdi olustur/kapat.
        if row.side == Signal.BUY.value:
            return self._apply_buy(order, row.symbol, "recovered", row.reason, client_order_id)
        trade = self.get_open_position(row.symbol)
        if trade is None:
            return OrderResult(True, "closed", None, float(order.get("average") or 0.0),
                               float(order.get("filled") or 0.0), client_order_id)
        return self._apply_sell(order, trade, row.reason, client_order_id)

    # ---------------------------------------------------------- uzlastirma
    def reconcile_positions(self) -> list[str]:
        open_trades = self.trades.get_open_trades(broker=self.name)
        if not open_trades:
            return []
        bal = self._balance()
        problems = []
        for t in open_trades:
            base = self._markets()[t.symbol]["base"]
            total = float((bal.get(base) or {}).get("total") or 0.0)
            if total < t.qty * (1 - POSITION_TOLERANCE):
                problems.append(f"{t.symbol}: kayitta {t.qty:.8g} {base}, borsada {total:.8g}")
        return problems


def _ts(order: dict) -> datetime:
    ms = order.get("timestamp")
    return datetime.fromtimestamp(ms / 1000, tz=UTC) if ms else datetime.now(UTC)
