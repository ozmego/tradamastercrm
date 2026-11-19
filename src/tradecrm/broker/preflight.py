"""Canliya gecmeden once ON KONTROL (tradecrm live check). Emir GONDERMEZ.

Her kontrol: (ad, durum, ayrinti) -- durum: OK | WARN | FAIL | INFO.
FAIL varsa canli calistirma onerilmez.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

QUOTE = "USDT"
# KuCoin Funding hesabinda bundan az USDT varsa transfer uyarisi verilmez (artik/dust).
MIN_FUNDING_WARN = 1.0
# Tek islemin hesaba orani (gunluk mum, %1 risk): backtest'te ort. ~%6, en az ~%3.
TYPICAL_MIN_FRACTION = 0.03


@dataclass
class Check:
    name: str
    status: str  # OK | WARN | FAIL | INFO
    detail: str


def _binance_permissions(exchange, add) -> None:
    r = exchange.sapi_get_account_apirestrictions()
    withdraw = str(r.get("enableWithdrawals")).lower() == "true"
    spot = str(r.get("enableSpotAndMarginTrading")).lower() == "true"
    ip = str(r.get("ipRestrict")).lower() == "true"
    _permission_checks(add, withdraw, spot, ip)


def _kucoin_permissions(exchange, add) -> None:
    r = exchange.private_get_user_api_key()
    data = r.get("data") or {}
    perms = {p.strip().lower() for p in str(data.get("permission") or "").split(",") if p.strip()}
    _permission_checks(add, withdraw="withdrawal" in perms, spot="spot" in perms,
                       ip=bool(str(data.get("ipWhitelist") or "").strip()))


def _permission_checks(add, withdraw: bool, spot: bool, ip: bool) -> None:
    add("Para cekme yetkisi", "FAIL" if withdraw else "OK",
        "ACIK -- anahtari para cekme KAPALI olacak sekilde yeniden olusturun" if withdraw else "kapali")
    add("Spot islem yetkisi", "OK" if spot else "FAIL", "acik" if spot else "KAPALI -- emir verilemez")
    add("IP kisitlamasi", "OK" if ip else "WARN",
        "acik" if ip else "yok -- anahtari bu bilgisayarin IP'si ile sinirlamaniz onerilir")


_PERMISSIONS = {"binance": _binance_permissions, "kucoin": _kucoin_permissions}


def run_preflight(exchange, symbols: list[str], budget: float, testnet: bool, env_orders_open: bool,
                  trading_enabled: bool | None = None, first_order_confirmed: bool | None = None,
                  clock=time.time, spec=None) -> list[Check]:
    from tradecrm.broker.ccxt_spot_adapter import BINANCE

    spec = spec or BINANCE
    out: list[Check] = []

    def add(name, status, detail):
        out.append(Check(name, status, detail))

    add("Ortam", "INFO", f"{spec.title} " + ("TESTNET (sahte bakiye)" if testnet else "GERCEK HESAP (gercek para)"))

    # 1) Baglanti + saat
    try:
        server_ms = exchange.fetch_time()
        drift = abs(server_ms - clock() * 1000)
        add("Baglanti / saat farki", "OK" if drift < 1000 else "WARN",
            f"sunucu saatiyle fark {drift:.0f} ms" + ("" if drift < 1000 else " -- Windows saatini esitleyin"))
    except Exception as exc:  # noqa: BLE001
        add("Baglanti", "FAIL", f"Binance'e ulasilamadi: {type(exc).__name__}")
        return out

    # 2) Kimlik dogrulama + bakiye
    try:
        bal = exchange.fetch_balance(dict(spec.balance_params))
    except Exception as exc:  # noqa: BLE001
        add("API anahtari", "FAIL", f"bakiye okunamadi: {type(exc).__name__}: {str(exc)[:120]}")
        return out
    add("API anahtari", "OK", "bakiye okunabiliyor")

    # 3) Anahtar yetkileri (testnet'te bu uc yok)
    if not testnet:
        try:
            _PERMISSIONS[spec.id](exchange, add)
        except Exception as exc:  # noqa: BLE001
            add("Anahtar yetkileri", "WARN", f"okunamadi ({type(exc).__name__}); {spec.title} panelinden kontrol edin")

    # 4) Butce
    free = float((bal.get(QUOTE) or {}).get("free") or 0.0)
    if free >= budget:
        add("USDT bakiyesi", "OK", f"serbest {free:,.2f} >= butce {budget:,.2f}")
    elif free > 0:
        add("USDT bakiyesi", "WARN", f"serbest {free:,.2f} < butce {budget:,.2f} -- bot en fazla mevcut bakiyeyi kullanir")
    else:
        add("USDT bakiyesi", "FAIL", "serbest USDT yok" + (" (Trading hesabinda)" if spec.balance_params else ""))
    if spec.id == "kucoin" and free < budget:
        # KuCoin: yatirilan para once Funding (main) hesabina duser; bot Trading hesabini kullanir.
        try:
            main = exchange.fetch_balance({"type": "main"})
            main_free = float((main.get(QUOTE) or {}).get("free") or 0.0)
            if main_free >= MIN_FUNDING_WARN:  # kurus alti artik (dust) icin uyarma
                add("Funding hesabi", "WARN",
                    f"Funding'de {main_free:,.2f} USDT var -- KuCoin'de Funding -> Trading transferi yapin")
        except Exception as exc:  # noqa: BLE001
            add("Funding hesabi", "INFO", f"okunamadi ({type(exc).__name__})")

    # 5) Semboller ve en kucuk emir tutari
    try:
        markets = exchange.load_markets()
    except Exception as exc:  # noqa: BLE001
        add("Piyasalar", "FAIL", f"okunamadi: {type(exc).__name__}")
        return out
    typical = budget * TYPICAL_MIN_FRACTION
    for sym in symbols:
        m = markets.get(sym)
        if m is None or not m.get("active", True) or not m.get("spot", True):
            add(f"Sembol {sym}", "FAIL", "spot'ta islem gormuyor")
            continue
        min_cost = float(((m.get("limits") or {}).get("cost") or {}).get("min") or 0.0)
        held = float((bal.get(m["base"]) or {}).get("total") or 0.0)
        note = f" · hesapta {held:.8g} {m['base']} var (bot yalnizca kendi actigi pozisyonu satar)" if held > 0 else ""
        if min_cost and typical < min_cost:
            add(f"Sembol {sym}", "WARN", f"en kucuk emir {min_cost} USDT; kucuk islemler (~{typical:,.0f}) reddedilebilir{note}")
        else:
            add(f"Sembol {sym}", "OK", f"en kucuk emir {min_cost or '-'} USDT{note}")

    # 6) Emir anahtarlari (bilgi)
    add(".env emir salteri", "INFO",
        "ACIK (TRADING_ENABLED=true, DRY_RUN=false)" if env_orders_open
        else "KAPALI -- runner yalnizca izler, emir gondermez (guvenli kuru calisma)")
    if trading_enabled is not None:
        add("DB alim izni", "INFO", "acik" if trading_enabled else "kapali (tradecrm risk enable-trading --account ...)")
    if first_order_confirmed is not None:
        add("Ilk emir onayi", "INFO", "verildi" if first_order_confirmed
            else "yok (tradecrm risk confirm-first-order --account ...)")
    return out
