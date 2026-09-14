# TradeCRM

Binance spot piyasasinda calisan, Python/ccxt tabanli algoritmik trading botu.

## Kurulum

```
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

`.env` dosyasini kendi API anahtarlarinizla doldurun. Binance API anahtari **sadece trading** yetkili olmali, **withdrawal kapali** birakilmalidir.

## Kullanim

```
tradecrm --help
```

## Guvenlik

- `TRADING_ENABLED=false` ve `DRY_RUN=true` varsayilandir. Gercek emir vermeden once bkz. proje planindaki fazli dogrulama akisi.
- Sirlar (`.env`) asla commit edilmez.
