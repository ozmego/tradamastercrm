import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_PATTERN = re.compile(
    r"(api[_-]?key|api[_-]?secret|token|chat[_-]?id)\s*[=:]\s*[\"']?[\w\-:]{6,}",
    re.IGNORECASE,
)


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _SECRET_PATTERN.search(message):
            record.msg = _SECRET_PATTERN.sub(lambda m: m.group(1) + "=***MASKED***", message)
            record.args = ()
        return True


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SecretMaskingFilter())

    file_handler = RotatingFileHandler(
        Path(log_dir) / "tradecrm.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretMaskingFilter())

    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
