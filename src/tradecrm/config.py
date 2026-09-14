from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Binance
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # CryptoPanic
    cryptopanic_api_key: str = ""

    # Islem ayarlari
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"

    # Guvenlik / risk
    trading_enabled: bool = False
    dry_run: bool = True
    total_capital_allocation_pct: float = 0.75
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

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000

    # Dosya yollari
    db_path: str = "data/tradecrm.db"
    audit_log_path: str = "logs/audit.jsonl"


def get_settings() -> Settings:
    return Settings()
