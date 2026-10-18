import unittest
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    # The deployment provides httpx via requirements.txt; keep these unit tests
    # runnable in a bare Python environment by stubbing only its client symbol.
    sys.modules["httpx"] = SimpleNamespace(AsyncClient=None)

import slack_token_rotation
import slack_token_hot_reload


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, data):
        self.request = (url, data)
        return self.response


def run_immediate(coroutine):
    """Run a fake-I/O coroutine that never suspends, without opening a socket loop."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("The mocked Slack exchange unexpectedly suspended")


class SlackTokenRotationTests(unittest.TestCase):
    def test_refresh_sends_credentials_and_returns_replacement_pair(self):
        fake = FakeAsyncClient(FakeResponse({
            "ok": True,
            "token_type": "app_level",
            "access_token": "xoxe.xapp-new-access",
            "refresh_token": "xoxe-new-refresh",
            "expires_in": 43200,
        }))

        with patch.object(slack_token_rotation.httpx, "AsyncClient", return_value=fake):
            result = run_immediate(slack_token_rotation.refresh_slack_app_token(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="old-refresh",
            ))

        self.assertEqual(result.access_token, "xoxe.xapp-new-access")
        self.assertEqual(result.refresh_token, "xoxe-new-refresh")
        self.assertEqual(result.expires_in, 43200)
        url, data = fake.request
        self.assertEqual(url, slack_token_rotation.SLACK_OAUTH_ACCESS_URL)
        self.assertEqual(data["grant_type"], "refresh_token")
        self.assertEqual(data["refresh_token"], "old-refresh")

    def test_error_does_not_echo_credentials(self):
        fake = FakeAsyncClient(FakeResponse({"ok": False, "error": "invalid_refresh_token"}))

        with patch.object(slack_token_rotation.httpx, "AsyncClient", return_value=fake):
            with self.assertRaises(RuntimeError) as raised:
                run_immediate(slack_token_rotation.refresh_slack_app_token(
                    client_id="private-client-id",
                    client_secret="private-client-secret",
                    refresh_token="private-refresh-token",
                ))

        self.assertNotIn("private", str(raised.exception))

    def test_socket_client_uses_rotated_token_without_restarting(self):
        class FakeSocketModeClient:
            def __init__(self, app_token):
                self.app_token = app_token
                self.closed = False

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            token_file = Path(directory) / ".env"
            token_file.write_text("SLACK_APP_TOKEN=xoxe.xapp-initial\n", encoding="utf-8")
            slack_token_hot_reload._patch_client_class(FakeSocketModeClient, token_file)
            client = FakeSocketModeClient("xoxe.xapp-from-process-env")
            self.assertEqual(client.app_token, "xoxe.xapp-initial")

            token_file.write_text("SLACK_APP_TOKEN=xoxe.xapp-rotated\n", encoding="utf-8")
            updated = slack_token_hot_reload._refresh_registered_clients(token_file)

        self.assertEqual(updated, 1)
        self.assertEqual(client.app_token, "xoxe.xapp-rotated")


if __name__ == "__main__":
    unittest.main()
