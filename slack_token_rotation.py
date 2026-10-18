"""Slack app-level token rotation for Socket Mode deployments."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"


@dataclass(frozen=True)
class RotatedSlackAppToken:
    access_token: str
    refresh_token: str
    expires_in: int


async def refresh_slack_app_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    timeout: float = 20.0,
) -> RotatedSlackAppToken:
    """Exchange a single-use Slack refresh token for the next token pair.

    Token values are never included in raised errors, so callers can safely
    report the exception type without leaking credentials into app logs.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            SLACK_OAUTH_ACCESS_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Slack rejected the app token refresh")
    if payload.get("token_type") != "app_level":
        raise RuntimeError("Slack returned a non-app-level token")

    access_token = payload.get("access_token")
    next_refresh_token = payload.get("refresh_token")
    try:
        expires_in = int(payload.get("expires_in", 0))
    except (TypeError, ValueError):
        expires_in = 0

    if not isinstance(access_token, str) or not access_token.startswith("xoxe.xapp-"):
        raise RuntimeError("Slack returned an invalid app access token")
    if not isinstance(next_refresh_token, str) or not next_refresh_token:
        raise RuntimeError("Slack returned no replacement refresh token")
    if expires_in <= 0:
        raise RuntimeError("Slack returned an invalid token expiry")

    return RotatedSlackAppToken(
        access_token=access_token,
        refresh_token=next_refresh_token,
        expires_in=expires_in,
    )
