from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # true: testnet.binance.vision (sahte bakiye; ayri anahtar). Canli hesap adi "binance:testnet" olur.
    binance_testnet: bool = False
    # KuCoin (canli): anahtar + gizli + passphrase. Test ortami yok -> once DRY_RUN=true kuru calisma.
    kucoin_api_key: str = ""
    kucoin_api_secret: str = ""
    kucoin_api_passphrase: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # CryptoPanic
    cryptopanic_api_key: str = ""

    # Islem ayarlari
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    # Calisan profil (strategy/profiles.py): strateji + cikis + boyut + devre kesiciler.
    # Backtest ve canli ayni profili kullanir.
    profile: str = "donchian"

    # Guvenlik / risk
    trading_enabled: bool = False
    dry_run: bool = True
    # Bilgi amacli; canli tavan profilin SizingConfig.max_total_fraction degerinden gelir (strategy/profiles.py).
    total_capital_allocation_pct: float = 0.35
    daily_growth_target_pct: float = 0.0075
    min_position_fraction: float = 0.2
    max_position_fraction: float = 1.0
    max_daily_loss_pct: float = 0.05
    daily_target_stop_on_hit: bool = False

    # Haber-duyarli strateji
    news_sentiment_enabled: bool = False
    news_window_hours: int = 6
    news_block_buy_threshold: float = -0.3
    news_min_votes_threshold: int = 5
    news_min_items_threshold: int = 3
    news_cache_ttl_seconds: int = 300

    # Admin paneli
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    admin_session_hours: int = 12
    # Panel HTTPS arkasinda (reverse proxy) yayinlaniyorsa true: cerez sadece HTTPS'te gider.
    admin_cookie_secure: bool = False

    # Veri toplayici (open interest, long/short orani)
    collect_symbols: str = (
        "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,LINK/USDT,AVAX/USDT,LTC/USDT"
    )
    # Bu kadar gunden eski metrikler her toplamadan sonra silinir (Binance ~30 gun tutar).
    collect_retention_days: int = 45

    # Veritabani
    database_url: str = "postgresql+psycopg://tradecrm:tradecrm@localhost:5434/tradecrm"

    # Dosya yollari
    audit_log_path: str = "logs/audit.jsonl"


def get_settings() -> Settings:
    return Settings()
