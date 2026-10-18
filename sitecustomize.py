"""Opt-in Python startup hook for live Slack app-token refresh in Hermes gateway."""

import os


if os.environ.get("HERMES_SLACK_TOKEN_HOT_RELOAD") == "1":
    try:
        from slack_token_hot_reload import install

        install()
    except Exception as exc:
        # Do not prevent Hermes from starting if an optional reload hook fails.
        import logging

        logging.getLogger("hermes.slack_token_hot_reload").warning(
            "Slack token watcher could not start (%s)", type(exc).__name__
        )
