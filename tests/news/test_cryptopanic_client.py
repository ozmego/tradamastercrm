from datetime import UTC, datetime, timedelta

import requests
import responses

from tradecrm.news.cryptopanic_client import (
    CryptoPanicClient,
    CryptoPanicUnavailableError,
)

_URL = "https://cryptopanic.com/api/v1/posts/"


def _entry(id_, title, published_at, positive=1, negative=0):
    return {
        "id": id_,
        "title": title,
        "url": f"https://example.com/{id_}",
        "published_at": published_at,
        "currencies": [{"code": "BTC"}],
        "votes": {"positive": positive, "negative": negative, "important": 0},
        "source": {"title": "Example"},
    }


class TestNormalParse:
    @responses.activate
    def test_parses_results_within_window(self):
        now = datetime.now(UTC)
        recent = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        old = (now - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
        responses.add(
            responses.GET,
            _URL,
            json={"results": [_entry(1, "recent", recent), _entry(2, "old", old)]},
            status=200,
        )

        client = CryptoPanicClient(api_key="KEY")
        items = client.get_recent_posts("BTC/USDT", hours=6)

        assert len(items) == 1
        assert items[0].title == "recent"
        assert items[0].total_votes == 1

    @responses.activate
    def test_empty_results(self):
        responses.add(responses.GET, _URL, json={"results": []}, status=200)
        client = CryptoPanicClient(api_key="KEY")
        assert client.get_recent_posts("BTC/USDT") == []

    @responses.activate
    def test_malformed_entry_is_skipped_not_fatal(self):
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        good = _entry(1, "good", now)
        bad = {"id": 2, "title": "bad, no published_at"}
        responses.add(responses.GET, _URL, json={"results": [good, bad]}, status=200)

        client = CryptoPanicClient(api_key="KEY")
        items = client.get_recent_posts("BTC/USDT")
        assert len(items) == 1
        assert items[0].title == "good"


class TestErrorHandling:
    @responses.activate
    def test_500_raises_unavailable(self):
        responses.add(responses.GET, _URL, json={"error": "server error"}, status=500)
        client = CryptoPanicClient(api_key="KEY")
        try:
            client.get_recent_posts("BTC/USDT")
            assert False, "beklenen istisna firlatilmadi"
        except CryptoPanicUnavailableError:
            pass

    @responses.activate
    def test_429_raises_unavailable(self):
        responses.add(responses.GET, _URL, json={}, status=429)
        client = CryptoPanicClient(api_key="KEY")
        try:
            client.get_recent_posts("BTC/USDT")
            assert False, "beklenen istisna firlatilmadi"
        except CryptoPanicUnavailableError:
            pass

    @responses.activate
    def test_malformed_body_raises_unavailable(self):
        responses.add(responses.GET, _URL, body="not json", status=200)
        client = CryptoPanicClient(api_key="KEY")
        try:
            client.get_recent_posts("BTC/USDT")
            assert False, "beklenen istisna firlatilmadi"
        except CryptoPanicUnavailableError:
            pass

    @responses.activate
    def test_timeout_raises_unavailable(self):
        responses.add(responses.GET, _URL, body=requests.exceptions.ConnectTimeout())
        client = CryptoPanicClient(api_key="KEY")
        try:
            client.get_recent_posts("BTC/USDT")
            assert False, "beklenen istisna firlatilmadi"
        except CryptoPanicUnavailableError:
            pass


class TestCaching:
    @responses.activate
    def test_second_call_within_ttl_uses_cache(self):
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        responses.add(responses.GET, _URL, json={"results": [_entry(1, "a", now)]}, status=200)

        client = CryptoPanicClient(api_key="KEY", cache_ttl_seconds=300)
        client.get_recent_posts("BTC/USDT")
        client.get_recent_posts("BTC/USDT")

        assert len(responses.calls) == 1

    @responses.activate
    def test_different_symbol_bypasses_cache(self):
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        responses.add(responses.GET, _URL, json={"results": [_entry(1, "a", now)]}, status=200)
        responses.add(responses.GET, _URL, json={"results": [_entry(2, "b", now)]}, status=200)

        client = CryptoPanicClient(api_key="KEY", cache_ttl_seconds=300)
        client.get_recent_posts("BTC/USDT")
        client.get_recent_posts("ETH/USDT")

        assert len(responses.calls) == 2


class TestSecretRedaction:
    @responses.activate
    def test_connection_error_message_does_not_leak_token(self):
        secret = "SUPERSECRET123"
        err = requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='cryptopanic.com', port=443): Max retries exceeded with url: "
            f"/api/v1/posts/?auth_token={secret}&currencies=BTC&public=true"
        )
        responses.add(responses.GET, _URL, body=err)
        client = CryptoPanicClient(api_key=secret)
        try:
            client.get_recent_posts("BTC/USDT")
            assert False, "beklenen istisna firlatilmadi"
        except CryptoPanicUnavailableError as exc:
            assert secret not in str(exc)
            assert exc.__cause__ is None  # zincirde de token tasinmamali

    def test_redact_masks_auth_token_param(self):
        from tradecrm.news.cryptopanic_client import _redact

        assert _redact("url?auth_token=abc&x=1", secret="") == "url?auth_token=***&x=1"


class TestVoteCounting:
    @responses.activate
    def test_non_sentiment_vote_fields_excluded(self):
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        entry = _entry(1, "a", now)
        entry["votes"] = {"positive": 2, "negative": 1, "important": 1, "liked": 1, "disliked": 0,
                          "lol": 5, "toxic": 1, "saved": 9, "comments": 20}
        responses.add(responses.GET, _URL, json={"results": [entry]}, status=200)
        item = CryptoPanicClient(api_key="KEY").get_recent_posts("BTC/USDT")[0]
        assert item.positive_votes == 3
        assert item.negative_votes == 2
        assert item.total_votes == 6
