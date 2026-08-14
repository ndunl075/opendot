"""Small Telegram Bot API client with no token persistence or logging."""

from __future__ import annotations

from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """A sanitized Telegram request failure that never includes the bot token."""


class TelegramBotClient:
    """Use the HTTPS Bot API only when a caller explicitly provides a local token."""

    def __init__(
        self,
        token: str,
        *,
        api_base: str = "https://api.telegram.org",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token.strip():
            raise TelegramAPIError("Telegram token cannot be empty")
        self._token = token
        # Ordinary sends should fail promptly. Long polling supplies its own
        # server-timeout-aware read budget below rather than inheriting one
        # blanket 60-second timeout for every Telegram request.
        self._client = httpx.Client(
            base_url=api_base,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict[str, Any]]:
        if timeout_seconds < 1 or timeout_seconds > 50:
            raise TelegramAPIError("Telegram long-poll timeout must be between 1 and 50 seconds")
        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request(
            "getUpdates",
            payload,
            timeout=httpx.Timeout(
                connect=5.0,
                read=float(timeout_seconds + 2),
                write=5.0,
                pool=5.0,
            ),
        )
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram getUpdates response did not contain an update list")
        return result

    def send_message(
        self, *, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> int:
        if not text.strip():
            raise TelegramAPIError("Telegram message text cannot be empty")
        if len(text) > 4096:
            raise TelegramAPIError("Telegram message text exceeds the 4096-character limit")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._request("sendMessage", payload)
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramAPIError("Telegram sendMessage response did not contain a message ID")
        return result["message_id"]

    def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
        if action != "typing":
            raise TelegramAPIError("unsupported Telegram chat action")
        result = self._request(
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
            timeout=httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0),
        )
        if result is not True:
            raise TelegramAPIError("Telegram sendChatAction response was not successful")

    def answer_callback_query(self, *, callback_query_id: str, text: str) -> None:
        if not callback_query_id:
            raise TelegramAPIError("Telegram callback query ID cannot be empty")
        self._request(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text[:200]},
        )

    def _request(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
    ) -> Any:
        try:
            response = self._client.post(
                f"/bot{self._token}/{method}",
                json=payload,
                **({"timeout": timeout} if timeout is not None else {}),
            )
        except httpx.HTTPError as error:
            raise TelegramAPIError(f"Telegram request failed: {error.__class__.__name__}") from error
        if response.status_code >= 400:
            raise TelegramAPIError(f"Telegram request failed with HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise TelegramAPIError("Telegram returned invalid JSON") from error
        if not isinstance(body, dict) or body.get("ok") is not True:
            description = body.get("description") if isinstance(body, dict) else None
            safe_description = str(description or "unknown error").replace(self._token, "[redacted]")
            raise TelegramAPIError(f"Telegram request was rejected: {safe_description}")
        return body.get("result")
