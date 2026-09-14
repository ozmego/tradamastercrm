import logging

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram Bot API'ye dogrudan requests ile tek yonlu (outbound-only)
    bildirim gonderir. Bildirim basarisiz olursa bot'u durdurmaz -- sadece
    loglanir, exception yukselmez (Telegram, kritik islem yolunun disinda)."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send_message(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram token/chat_id tanimli degil, mesaj gonderilmedi.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url, json={"chat_id": self.chat_id, "text": text}, timeout=self.timeout
            )
            response.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Telegram mesaji gonderilemedi.")
            return False
