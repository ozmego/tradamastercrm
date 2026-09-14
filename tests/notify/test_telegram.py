import responses

from tradecrm.notify.telegram import TelegramNotifier


class TestTelegramNotifier:
    def test_missing_credentials_returns_false_without_request(self):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        assert notifier.send_message("merhaba") is False

    @responses.activate
    def test_successful_send_returns_true(self):
        notifier = TelegramNotifier(bot_token="TOKEN123", chat_id="42")
        responses.add(
            responses.POST,
            "https://api.telegram.org/botTOKEN123/sendMessage",
            json={"ok": True},
            status=200,
        )

        result = notifier.send_message("test mesaji")

        assert result is True
        assert len(responses.calls) == 1
        assert responses.calls[0].request.url == "https://api.telegram.org/botTOKEN123/sendMessage"

    @responses.activate
    def test_server_error_returns_false(self):
        notifier = TelegramNotifier(bot_token="TOKEN123", chat_id="42")
        responses.add(
            responses.POST,
            "https://api.telegram.org/botTOKEN123/sendMessage",
            json={"ok": False},
            status=500,
        )

        assert notifier.send_message("test mesaji") is False

    @responses.activate
    def test_timeout_returns_false(self):
        import requests

        notifier = TelegramNotifier(bot_token="TOKEN123", chat_id="42")
        responses.add(
            responses.POST,
            "https://api.telegram.org/botTOKEN123/sendMessage",
            body=requests.exceptions.ConnectTimeout(),
        )

        assert notifier.send_message("test mesaji") is False
