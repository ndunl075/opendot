"""Slack SDK adapters; tokens remain in the caller's OS credential store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from .audit import AuditEvent, AuditLog
from .slack import SlackEvent, SlackGateway


class SlackBotClient:
    """Small `chat.postMessage` adapter that does not persist or log the bot token."""

    def __init__(self, bot_token: str) -> None:
        if not bot_token.strip():
            raise ValueError("Slack bot token cannot be empty")
        self._client = WebClient(token=bot_token)

    @property
    def web_client(self) -> WebClient:
        return self._client

    def post_message(self, *, channel_id: str, text: str) -> str:
        if not channel_id.strip() or not text.strip():
            raise ValueError("Slack channel and message text are required")
        response = self._client.chat_postMessage(channel=channel_id, text=text)
        timestamp = response.get("ts")
        if not isinstance(timestamp, str):
            raise RuntimeError("Slack postMessage response did not contain a message timestamp")
        return timestamp


class SlackSocketReceiver:
    """Own one Slack Socket Mode connection and acknowledge every envelope first."""

    def __init__(self, *, app_token: str, bot_client: SlackBotClient, gateway: SlackGateway) -> None:
        if not app_token.strip():
            raise ValueError("Slack app token cannot be empty")
        self.gateway = gateway
        self._client = SocketModeClient(app_token=app_token, web_client=bot_client.web_client)
        self._client.socket_mode_request_listeners.append(self._handle_request)

    def start(self) -> None:
        self._client.connect()

    def close(self) -> None:
        self._client.close()

    def _handle_request(self, client: SocketModeClient, request: SocketModeRequest) -> None:
        # Socket Mode envelopes are authenticated by Slack.  Acknowledge before
        # any local database work so retries cannot amplify a slow disk.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type != "events_api" or not isinstance(request.payload, dict):
            return
        event_id = request.payload.get("event_id")
        raw_event = request.payload.get("event")
        if not isinstance(event_id, str) or not isinstance(raw_event, dict):
            return
        try:
            event = SlackEvent.model_validate(raw_event)
            occurred_at = _event_time(event.ts)
            self.gateway.handle_event(event_id=event_id, event=event, occurred_at=occurred_at)
        except (PermissionError, ValueError) as error:
            # The gateway's persisted audit/outbox rules apply to accepted work;
            # Slack already received its acknowledgement, so rejected input must
            # never cause a retry storm or leak pairing details to Slack.
            AuditLog(self.gateway.database).append(
                AuditEvent(
                    actor="system:slack",
                    client="slack",
                    tool="slack_event_rejected",
                    outcome="rejected",
                    result={"event_id": event_id, "reason": error.__class__.__name__},
                )
            )
            return


def _event_time(timestamp: str | None) -> datetime:
    if timestamp is None:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(float(timestamp), UTC)
    except ValueError:
        return datetime.now(UTC)
