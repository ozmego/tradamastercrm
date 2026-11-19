import re
import time
from datetime import UTC, datetime, timedelta

import requests

from tradecrm.news.models import NewsItem

_BASE_URL = "https://cryptopanic.com/api/v1/posts/"  # CryptoPanic guncel dokumanindan dogrulayin (v2 olabilir)

# Duygu bildiren oylar. CryptoPanic votes nesnesinde 'saved', 'comments'
# gibi duygu tasimayan alanlar da var; bunlar toplama dahil edilmez.
_POSITIVE_VOTE_KEYS = ("positive", "liked")
_NEGATIVE_VOTE_KEYS = ("negative", "disliked", "toxic")
_NEUTRAL_VOTE_KEYS = ("important",)


_AUTH_TOKEN_RE = re.compile(r"(auth_token=)[^&\s'\"]+")


class CryptoPanicUnavailableError(Exception):
    """CryptoPanic'e erisilemedi (timeout, hata, rate-limit, beklenmeyen govde).
    Cagiran (NewsAwareStrategy) bunu yakalayip teknik-only sinyale donmeli --
    "bilmiyoruz" otomatik olarak durdurma/bearish sayilmamali."""


class CryptoPanicClient:
    def __init__(
        self, api_key: str, timeout: float = 5.0, cache_ttl_seconds: int = 300, base_url: str = _BASE_URL
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple[str, int], tuple[float, list[NewsItem]]] = {}

    def get_recent_posts(self, symbol: str, hours: int = 6) -> list[NewsItem]:
        """Ucretsiz tier rate limit'i icin ~cache_ttl_seconds boyunca
        onbellekler -- scheduler her dakika tetiklense bile CryptoPanic'e
        her tick'te gitmez."""
        currency_code = _symbol_to_currency_code(symbol)
        cache_key = (currency_code, hours)
        now_ts = time.monotonic()

        cached = self._cache.get(cache_key)
        if cached is not None and (now_ts - cached[0]) < self.cache_ttl_seconds:
            return cached[1]

        items = self._fetch(currency_code, hours)
        self._cache[cache_key] = (now_ts, items)
        return items

    def _fetch(self, currency_code: str, hours: int) -> list[NewsItem]:
        params = {"auth_token": self.api_key, "currencies": currency_code, "public": "true"}
        try:
            response = requests.get(self.base_url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            # str(exc) istek URL'sini -- dolayisiyla auth_token'i -- icerir ve
            # log/audit/Telegram'a gider. Sadece tip + maskelenmis mesaj.
            raise CryptoPanicUnavailableError(
                f"{type(exc).__name__}: {_redact(str(exc), self.api_key)}"
            ) from None

        if response.status_code == 429:
            raise CryptoPanicUnavailableError("rate limited (429)")
        if response.status_code != 200:
            raise CryptoPanicUnavailableError(f"beklenmeyen durum kodu: {response.status_code}")

        try:
            payload = response.json()
            results = payload["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CryptoPanicUnavailableError(f"beklenmeyen govde: {exc}") from exc

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        items: list[NewsItem] = []
        for entry in results:
            try:
                item = _parse_entry(entry)
            except (KeyError, ValueError, TypeError):
                continue
            if item.published_at >= cutoff:
                items.append(item)
        return items


def _redact(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***")
    # Kodlanmis/kismi URL varyantlarina karsi: auth_token=... degerini her durumda maskele.
    return _AUTH_TOKEN_RE.sub(r"\g<1>***", text)


def _symbol_to_currency_code(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def _parse_entry(entry: dict) -> NewsItem:
    votes = entry.get("votes") or {}
    positive = sum(int(votes.get(k, 0) or 0) for k in _POSITIVE_VOTE_KEYS)
    negative = sum(int(votes.get(k, 0) or 0) for k in _NEGATIVE_VOTE_KEYS)
    important = sum(int(votes.get(k, 0) or 0) for k in _NEUTRAL_VOTE_KEYS)
    published_at = datetime.fromisoformat(str(entry["published_at"]))
    source = entry.get("source")
    source_title = source.get("title", "") if isinstance(source, dict) else ""
    return NewsItem(
        id=str(entry["id"]),
        title=entry.get("title", ""),
        url=entry.get("url", ""),
        published_at=published_at,
        currencies=[c.get("code", "") for c in entry.get("currencies", []) if isinstance(c, dict)],
        positive_votes=positive,
        negative_votes=negative,
        important_votes=important,
        total_votes=positive + negative + important,
        source=source_title,
    )
