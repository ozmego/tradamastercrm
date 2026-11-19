# ruff: noqa: RUF059  -- _setup ucluyu dondurur; her test hepsini kullanmaz
"""CcxtSpotAdapter: Binance ve KuCoin davranisini taklit eden sahte ccxt nesneleriyle."""

import math
from datetime import UTC, datetime

import ccxt
import pytest

from tradecrm.broker.base import BrokerError, BrokerTimeoutError
from tradecrm.broker.ccxt_spot_adapter import BINANCE, KUCOIN, CcxtSpotAdapter, get_spec
from tradecrm.broker.order_manager import OrderManager
from tradecrm.broker.preflight import run_preflight
from tradecrm.db.repository import OrderRepository, TradeRepository
from tradecrm.strategy.base import Signal

FEE = 0.001


class FakeExchange:
    """Ortak sahte borsa. spec=KUCOIN iken:
    - emir yaniti dolum icermez (sadece id + clientOrderId); dolum sorgu ile gelir,
    - bakiye 'trade' (Trading) ve 'main' (Funding) hesaplarina ayrilir,
    - ayni clientOid ikinci kez gelirse 126044 hatasi."""

    def __init__(self, spec, usdt=1_000.0, price=100.0, mode=None, fee_currency="base", main_usdt=0.0,
                 query_lag=0):
        self.spec = spec
        self.price = price
        self.mode = mode
        self.fee_currency = fee_currency
        self.balances = {"USDT": usdt, "BTC": 0.0}
        self.main = {"USDT": main_usdt}
        self.orders: dict[str, dict] = {}
        self.calls = 0
        self.query_lag = query_lag  # KuCoin: ilk N sorguda emir henuz dolmamis gorunur
        self.balance_params: list = []
        self.markets = {"BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "active": True,
                                     "spot": True, "limits": {"cost": {"min": 5.0}}}}
        self.permissions = {"enableWithdrawals": False, "enableSpotAndMarginTrading": True, "ipRestrict": True}
        self.kucoin_key = {"permission": "General,Spot", "ipWhitelist": "1.2.3.4"}

    # piyasa / hesap
    def load_markets(self):
        return self.markets

    def fetch_ticker(self, symbol):
        return {"last": self.price}

    def fetch_balance(self, params=None):
        params = params or {}
        self.balance_params.append(params)
        src = self.main if params.get("type") == "main" else self.balances
        return {k: {"free": v, "total": v} for k, v in src.items()}

    def fetch_time(self):
        return datetime.now(UTC).timestamp() * 1000

    def sapi_get_account_apirestrictions(self):
        return self.permissions

    def private_get_user_api_key(self, params=None):
        return {"code": "200000", "data": self.kucoin_key}

    def cost_to_precision(self, symbol, x):
        return f"{math.floor(x * 100) / 100:.2f}"

    def amount_to_precision(self, symbol, x):
        return f"{math.floor(x * 1e5) / 1e5:.5f}"

    # emirler
    def _coid(self, params):
        return params[self.spec.client_id_param]

    def _finish(self, coid, full):
        if coid in self.orders and self.spec is KUCOIN:
            raise ccxt.InvalidOrder('kucoin {"code":"126044","msg":"clientOid duplicate"}')
        self.orders[coid] = full
        if self.mode == "timeout_after_fill":
            self.mode = None
            raise ccxt.RequestTimeout("timeout")
        if self.spec is KUCOIN:  # KuCoin: yanitta dolum yok
            return {"id": full["id"], "clientOrderId": coid, "status": None, "filled": None}
        return full

    def create_market_buy_order_with_cost(self, symbol, cost, params):
        self.calls += 1
        if self.mode == "timeout_no_fill":
            self.mode = None
            raise ccxt.RequestTimeout("timeout")
        if self.mode == "insufficient":
            raise ccxt.InsufficientFunds("insufficient balance")
        coid = self._coid(params)
        if coid in self.orders:
            return self._finish(coid, self.orders[coid])
        filled = cost / self.price
        fee = filled * FEE if self.fee_currency == "base" else cost * FEE
        cur = "BTC" if self.fee_currency == "base" else "USDT"
        self.balances["USDT"] -= cost + (fee if cur == "USDT" else 0)
        self.balances["BTC"] += filled - (fee if cur == "BTC" else 0)
        return self._finish(coid, {
            "id": f"ex{self.calls}", "status": "closed", "filled": filled, "cost": cost, "average": self.price,
            "fees": [{"cost": fee, "currency": cur}], "timestamp": 1_763_000_000_000, "clientOrderId": coid})

    def create_order(self, symbol, type_, side, amount, price, params):
        self.calls += 1
        coid = self._coid(params)
        proceeds = amount * self.price
        fee = proceeds * FEE
        self.balances["BTC"] -= amount
        self.balances["USDT"] += proceeds - fee
        return self._finish(coid, {
            "id": f"ex{self.calls}", "status": "closed", "filled": amount, "cost": proceeds, "average": self.price,
            "fees": [{"cost": fee, "currency": "USDT"}], "timestamp": 1_763_000_100_000, "clientOrderId": coid})

    def fetch_order(self, id_, symbol, params):
        if self.spec is KUCOIN and id_ is None:  # gercek ccxt davranisi (canli testte bulundu)
            raise ccxt.ArgumentsRequired("kucoin fetchOrder() requires an id argument")
        coid = params[self.spec.query_param]
        o = self.orders.get(coid)
        if o is None:
            if self.spec is KUCOIN:
                raise ccxt.ExchangeError('kucoin {"code":"400100","msg":"order not exist"}')
            raise ccxt.OrderNotFound('binance {"code":-2013,"msg":"Order does not exist."}')
        if self.query_lag > 0:
            self.query_lag -= 1
            return {"id": o["id"], "clientOrderId": coid, "status": "open", "filled": 0.0}
        return o


BAR = datetime(2026, 11, 18, tzinfo=UTC)
SPECS = [pytest.param(BINANCE, id="binance"), pytest.param(KUCOIN, id="kucoin")]


def _setup(db_session, spec, **kw):
    ex = FakeExchange(spec, **kw)
    adapter = CcxtSpotAdapter(db_session, ex, spec, initial_capital=500.0, sleep=lambda s: None)
    om = OrderManager(adapter, OrderRepository(db_session))
    return ex, adapter, om


@pytest.mark.parametrize("spec", SPECS)
def test_buy_records_net_qty_and_cost_basis(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "donchian", notional=100.0)
    assert res.status == "FILLED"
    t = TradeRepository(db_session).get_open_trades(broker=spec.id)[0]
    assert t.qty == pytest.approx(1.0 * (1 - FEE))  # komisyon coinden kesildi
    assert t.entry_price == 100.0
    assert t.entry_price * t.qty + t.entry_fees == pytest.approx(100.0)  # maliyet = odenen USDT
    assert ex.calls == 1


@pytest.mark.parametrize("spec", SPECS)
def test_same_client_order_id_never_sends_twice(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "donchian", notional=100.0)
    again = adapter.submit_order(res.client_order_id, "BTC/USDT", Signal.BUY, 100.0, "donchian")
    assert again.executed and ex.calls == 1
    assert om.execute("BTC/USDT", Signal.BUY, BAR, "donchian", notional=100.0).status == "DUPLICATE"


@pytest.mark.parametrize("spec", SPECS)
def test_min_notional_and_insufficient_cash_rejected(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    assert om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=3.0).status == "REJECTED"
    ex2, adapter2, om2 = _setup(db_session, spec, usdt=50.0)
    res = om2.execute("BTC/USDT", Signal.BUY, datetime(2026, 11, 19, tzinfo=UTC), "d", notional=100.0)
    assert res.status == "REJECTED" and "insufficient" in res.error
    assert ex.calls == 0 and ex2.calls == 0


@pytest.mark.parametrize("spec", SPECS)
def test_sell_closes_with_real_proceeds_and_dust(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    ex.price = 110.0
    res = om.execute("BTC/USDT", Signal.SELL, datetime(2026, 11, 20, tzinfo=UTC), "d", reason="trailing_stop")
    assert res.status == "FILLED"
    t = TradeRepository(db_session).get_closed_trades_between(datetime(2020, 1, 1, tzinfo=UTC),
                                                               datetime(2030, 1, 1, tzinfo=UTC))[0]
    sold = math.floor(0.999 * 1e5) / 1e5
    proceeds = sold * 110.0
    assert t.realized_pnl == pytest.approx(proceeds - 100.0 - proceeds * FEE, abs=1e-6)
    assert t.exit_reason == "trailing_stop"


@pytest.mark.parametrize("spec", SPECS)
def test_timeout_after_fill_is_recovered_without_second_order(db_session, spec):
    ex, adapter, om = _setup(db_session, spec, mode="timeout_after_fill")
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    assert res.status == "FILLED" and ex.calls == 1
    assert len(TradeRepository(db_session).get_open_trades(broker=spec.id)) == 1


@pytest.mark.parametrize("spec", SPECS)
def test_timeout_without_fill_retries_once_with_same_id(db_session, spec):
    ex, adapter, om = _setup(db_session, spec, mode="timeout_no_fill")
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    assert res.status == "FILLED" and ex.calls == 2
    assert list(ex.orders) == [res.client_order_id]


@pytest.mark.parametrize("spec", SPECS)
def test_get_order_unknown_returns_none(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    order = OrderRepository(db_session).reserve(spec.id, "BTC/USDT", "BUY", BAR, None, 100.0)
    assert adapter.get_order(order.client_order_id) is None


@pytest.mark.parametrize("spec", SPECS)
def test_exchange_errors_mapped(db_session, spec):
    ex, adapter, _ = _setup(db_session, spec, mode="insufficient")
    with pytest.raises(BrokerError) as e:
        adapter.submit_order("x1", "BTC/USDT", Signal.BUY, 100.0, "d")
    assert not isinstance(e.value, BrokerTimeoutError)


@pytest.mark.parametrize("spec", SPECS)
def test_snapshot_budget_capped_by_real_balance(db_session, spec):
    ex, adapter, _ = _setup(db_session, spec, usdt=10_000.0)
    assert adapter.get_snapshot().cash == pytest.approx(500.0)
    ex.balances["USDT"] = 200.0
    assert adapter.get_snapshot().cash == pytest.approx(200.0)


@pytest.mark.parametrize("spec", SPECS)
def test_reconcile_detects_missing_coins(db_session, spec):
    ex, adapter, om = _setup(db_session, spec)
    om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    assert adapter.reconcile_positions() == []
    ex.balances["BTC"] = 0.2
    [msg] = adapter.reconcile_positions()
    assert msg.startswith("BTC/USDT: kayitta")


@pytest.mark.parametrize("spec", SPECS)
def test_fee_paid_in_usdt_keeps_full_qty(db_session, spec):
    ex, adapter, om = _setup(db_session, spec, fee_currency="quote")
    om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    t = TradeRepository(db_session).get_open_trades(broker=spec.id)[0]
    assert t.qty == pytest.approx(1.0) and t.entry_fees == pytest.approx(0.1)


# --------------------------------------------------------------- KuCoin'e ozgu
def test_kucoin_uses_trading_account_balance(db_session):
    ex, adapter, _ = _setup(db_session, KUCOIN)
    adapter.get_snapshot()
    assert ex.balance_params and all(p == {"type": "trade"} for p in ex.balance_params)


def test_kucoin_fill_confirmed_by_polling(db_session):
    """Yanitta dolum yok, ilk 2 sorguda 'open' -> 3. sorguda dolum; tek emir."""
    ex, adapter, om = _setup(db_session, KUCOIN, query_lag=2)
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    assert res.status == "FILLED" and ex.calls == 1
    assert res.result.qty == pytest.approx(0.999)


def test_kucoin_unconfirmed_fill_goes_to_reconcile_without_resend(db_session):
    """Yoklama suresince dolum gorulmezse emir belirsiz kalir; AYNI kimlikle tekrar gonderim borsada
    126044 ile reddedilir ve sorguya donulur -> ikinci emir olusmaz."""
    ex, adapter, om = _setup(db_session, KUCOIN, query_lag=100)
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    assert res.status == "UNKNOWN"
    assert len(ex.orders) == 1  # borsada tek emir
    ex.query_lag = 0
    report = om.reconcile()
    assert report.resolved == [res.client_order_id]
    assert len(TradeRepository(db_session).get_open_trades(broker="kucoin")) == 1


def test_kucoin_duplicate_client_id_is_queried_not_resent(db_session):
    ex, adapter, om = _setup(db_session, KUCOIN)
    res = om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    # yerel kaydi silinmis gibi davran: dogrudan borsaya ikinci kez gonder
    adapter._local = lambda coid: None
    adapter.get_open_position = lambda symbol: None
    again = adapter.submit_order(res.client_order_id, "BTC/USDT", Signal.BUY, 100.0, "d")
    assert again.executed and len(ex.orders) == 1


def test_get_spec():
    assert get_spec("kucoin") is KUCOIN and KUCOIN.needs_passphrase and not KUCOIN.has_sandbox
    with pytest.raises(KeyError):
        get_spec("btcturk")


# ------------------------------------------------------------------ on kontrol
def _status(checks, name):
    return next(c.status for c in checks if c.name == name)


def test_preflight_binance_ok_and_withdrawal_fail():
    ex = FakeExchange(BINANCE, usdt=1_000.0)
    checks = run_preflight(ex, ["BTC/USDT"], 500.0, testnet=False, env_orders_open=False, spec=BINANCE)
    assert _status(checks, "Para cekme yetkisi") == "OK"
    assert not [c for c in checks if c.status == "FAIL"]
    ex.permissions = {"enableWithdrawals": True, "enableSpotAndMarginTrading": True, "ipRestrict": False}
    checks = run_preflight(ex, ["BTC/USDT", "NOPE/USDT"], 5_000.0, testnet=False, env_orders_open=True, spec=BINANCE)
    assert _status(checks, "Para cekme yetkisi") == "FAIL"
    assert _status(checks, "IP kisitlamasi") == "WARN"
    assert _status(checks, "USDT bakiyesi") == "WARN"
    assert _status(checks, "Sembol NOPE/USDT") == "FAIL"


def test_preflight_kucoin_permissions_and_funding_warning():
    ex = FakeExchange(KUCOIN, usdt=0.0, main_usdt=800.0)
    checks = run_preflight(ex, ["BTC/USDT"], 500.0, testnet=False, env_orders_open=False, spec=KUCOIN)
    assert _status(checks, "Para cekme yetkisi") == "OK"
    assert _status(checks, "Spot islem yetkisi") == "OK"
    assert _status(checks, "IP kisitlamasi") == "OK"
    assert _status(checks, "USDT bakiyesi") == "FAIL"
    funding = next(c for c in checks if c.name == "Funding hesabi")
    assert funding.status == "WARN" and "Trading" in funding.detail
    dust = FakeExchange(KUCOIN, usdt=0.0, main_usdt=4.2e-7)  # gercek hesapta gorulen artik
    checks = run_preflight(dust, ["BTC/USDT"], 500.0, testnet=False, env_orders_open=False, spec=KUCOIN)
    assert not [c for c in checks if c.name == "Funding hesabi"]
    ex.kucoin_key = {"permission": "General,Spot,Withdrawal", "ipWhitelist": ""}
    checks = run_preflight(ex, ["BTC/USDT"], 500.0, testnet=False, env_orders_open=False, spec=KUCOIN)
    assert _status(checks, "Para cekme yetkisi") == "FAIL"
    assert _status(checks, "IP kisitlamasi") == "WARN"
    ex.kucoin_key = {"permission": "General", "ipWhitelist": "1.2.3.4"}
    checks = run_preflight(ex, ["BTC/USDT"], 500.0, testnet=False, env_orders_open=False, spec=KUCOIN)
    assert _status(checks, "Spot islem yetkisi") == "FAIL"


def test_preflight_unreachable():
    class Down(FakeExchange):
        def fetch_time(self):
            raise ccxt.NetworkError("down")

    checks = run_preflight(Down(KUCOIN), ["BTC/USDT"], 100.0, testnet=False, env_orders_open=False, spec=KUCOIN)
    assert checks[-1].status == "FAIL"


@pytest.mark.parametrize("spec", SPECS)
def test_entry_cost_uses_exchange_reported_cost_not_rounded_average(db_session, spec):
    """Borsanin 'average' alani yuvarlanmis olabilir; maliyet gercek odenen tutara (cost) esit olmali."""
    ex, adapter, om = _setup(db_session, spec, fee_currency="quote")
    real_create = ex.create_market_buy_order_with_cost

    def rounded_average(symbol, cost, params):
        out = real_create(symbol, cost, params)
        ex.orders[params[spec.client_id_param]]["average"] = 100.06  # yuvarlanmis/sapmis ortalama
        if spec is BINANCE:
            out["average"] = 100.06
        return out

    ex.create_market_buy_order_with_cost = rounded_average
    om.execute("BTC/USDT", Signal.BUY, BAR, "d", notional=100.0)
    t = TradeRepository(db_session).get_open_trades(broker=spec.id)[0]
    assert t.entry_price * t.qty + t.entry_fees == pytest.approx(100.0 + 0.1)  # odenen USDT + komisyon
