import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLogger:
    """Append-only JSONL karar/emir logu. Her strateji degerlendirmesi
    (HOLD dahil), her risk kontrolu sonucu (nedeniyle), her emir denemesi
    buraya yazilir -- forensic amacli, db/repository.py'deki (sadece
    gerceklesen fill'ler) yapisal kaydin yerini almaz, onu tamamlar."""

    def __init__(self, path: str = "logs/audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
