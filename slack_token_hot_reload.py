"""Hot-reload Slack Socket Mode reconnect credentials inside the Hermes gateway."""

from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Type


logger = logging.getLogger("hermes.slack_token_hot_reload")
POLL_INTERVAL_S = 10
_clients: weakref.WeakSet = weakref.WeakSet()
_clients_lock = threading.Lock()


def read_app_token(path: str | Path, fallback: str = "") -> str:
    """Read only the app token line from Hermes's persistent dotenv file."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "SLACK_APP_TOKEN":
                candidate = value.strip().strip("\"'")
                if candidate.startswith(("xapp-", "xoxe.xapp-")):
                    return candidate
                break
    except OSError:
        pass
    return fallback


def _patch_client_class(client_class: Type, token_file: str | Path) -> None:
    """Track Slack's aiohttp SocketModeClient and update its reconnect token."""
    if getattr(client_class, "_hermes_token_reload_patched", False):
        return

    original_init = client_class.__init__

    def patched_init(self, app_token, *args, **kwargs):
        current = read_app_token(token_file, fallback=app_token)
        original_init(self, current, *args, **kwargs)
        with _clients_lock:
            _clients.add(self)

    client_class.__init__ = patched_init
    client_class._hermes_token_reload_patched = True


def _refresh_registered_clients(token_file: str | Path, fallback: str = "") -> int:
    token = read_app_token(token_file, fallback=fallback)
    if not token:
        return 0
    updated = 0
    with _clients_lock:
        clients = list(_clients)
    for client in clients:
        if getattr(client, "app_token", None) != token:
            client.app_token = token
            updated += 1
    return updated


def _watch_token_file(token_file: str | Path, fallback: str) -> None:
    while True:
        try:
            _refresh_registered_clients(token_file, fallback)
        except Exception as exc:  # keep the watcher alive after transient file/SDK issues
            logger.warning("Slack token watcher retrying after %s", type(exc).__name__)
        time.sleep(POLL_INTERVAL_S)


def install(token_file: str | Path | None = None) -> None:
    """Install the hook only in the Hermes gateway process that opts in."""
    if os.environ.get("HERMES_SLACK_TOKEN_HOT_RELOAD") != "1":
        return
    path = token_file or os.environ.get("HERMES_SLACK_TOKEN_FILE", "")
    if not path:
        logger.warning("Slack token watcher disabled: no token file configured")
        return
    try:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
    except Exception as exc:
        logger.warning("Slack token watcher disabled: Socket Mode SDK import failed (%s)", type(exc).__name__)
        return

    _patch_client_class(SocketModeClient, path)
    initial = read_app_token(path, fallback=os.environ.get("SLACK_APP_TOKEN", ""))
    watcher = threading.Thread(target=_watch_token_file, args=(path, initial), daemon=True)
    watcher.start()
    logger.info("Slack Socket Mode token auto-refresh watcher active")
