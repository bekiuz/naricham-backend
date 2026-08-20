from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas import ChatResponse, ManusErrorPayload


logger = logging.getLogger(__name__)


class ManusClientError(Exception):
    """Base class for errors raised by the Manus API client."""


class ManusConfigurationError(ManusClientError):
    """Raised when the server is not configured with a Manus API key."""


class ManusTimeoutError(ManusClientError):
    """Raised when the Manus API does not respond within the configured timeout."""


class ManusInvalidResponseError(ManusClientError):
    """Raised when Manus returns a malformed or unusable JSON response."""


class ManusApiError(ManusClientError):
    """Raised for a non-success Manus API response."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        request_id: str | None = None,
        endpoint: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.endpoint = endpoint
        self.task_id_present = task_id is not None
        super().__init__(message)

    def public_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.request_id:
            detail["request_id"] = self.request_id
        return detail

    def diagnostic_detail(self) -> dict[str, Any]:
        endpoint_map = {
            "/v2/task.create": "task.create",
            "/v2/task.listMessages": "task.listMessages",
        }
        return {
            "failed_endpoint": endpoint_map.get(self.endpoint, "another"),
            "http_status": self.status_code,
            "request_id": self.request_id,
            "task_id_present": self.task_id_present,
            "error_code": self.code,
            "error_message": self.message,
        }


class ManusClient:
    """Small async client for the official Manus API v2 task endpoints."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.manus_api_url,
            timeout=httpx.Timeout(settings.manus_api_timeout_seconds),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def diagnostics_enabled(self) -> bool:
        return self._settings.manus_diagnostics_enabled

    async def send_chat(
        self,
        message: str,
        *,
        task_id: str | None = None,
    ) -> ChatResponse:
        """Create a task by default; continue only an explicitly supplied task ID."""
        api_key = self._settings.manus_api_key
        if api_key is None:
            raise ManusConfigurationError("MANUS_API_KEY is not configured")

        if task_id:
            endpoint = "/v2/task.sendMessage"
            payload: dict[str, Any] = {
                "task_id": task_id,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": message,
                        }
                    ]
                },
            }
        else:
            endpoint = "/v2/task.create"
            payload = {
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": message,
                        }
                    ]
                }
            }
            if self._settings.manus_project_id:
                payload["project_id"] = self._settings.manus_project_id

        headers = {
            "Content-Type": "application/json",
            "x-manus-api-key": api_key.get_secret_value(),
        }

        try:
            response = await self._client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            self._log_diagnostic(
                endpoint=endpoint,
                http_status=None,
                task_id=task_id,
                error_code="manus_timeout",
                error_message="Manus API request timed out",
            )
            raise ManusTimeoutError("Manus API request timed out") from exc
        except httpx.RequestError as exc:
            self._log_diagnostic(
                endpoint=endpoint,
                http_status=None,
                task_id=task_id,
                error_code="manus_transport_error",
                error_message="Could not reach the Manus API",
            )
            raise ManusApiError(
                status_code=502,
                code="manus_transport_error",
                message="Could not reach the Manus API",
                endpoint=endpoint,
                task_id=task_id,
            ) from exc

        data = self._decode_json(response)
        self._log_response_diagnostic(
            endpoint=endpoint,
            http_status=response.status_code,
            request_task_id=task_id,
            response_data=data,
        )
        if response.status_code >= 400 or data.get("ok") is False:
            raise self._api_error(
                response.status_code,
                data,
                endpoint=endpoint,
                task_id=task_id,
            )
        if data.get("ok") is not True:
            raise ManusInvalidResponseError("Manus response did not contain ok=true")

        try:
            task_response = ChatResponse.model_validate(
                {
                    "ok": True,
                    "request_id": data["request_id"],
                    "task_id": data["task_id"],
                    "task_title": data.get("task_title"),
                    "task_url": data.get("task_url"),
                    "share_url": data.get("share_url"),
                    "share_visibility": data.get("share_visibility"),
                }
            )
        except (KeyError, ValidationError) as exc:
            raise ManusInvalidResponseError(
                "Manus response is missing required task fields"
            ) from exc

        assistant_text = await self._wait_for_assistant_response(
            task_id=task_response.task_id,
            user_message=message,
            headers=headers,
        )
        return task_response.model_copy(update={"response": assistant_text})

    async def _wait_for_assistant_response(
        self,
        *,
        task_id: str,
        user_message: str,
        headers: dict[str, str],
    ) -> str:
        """Poll official task events until the reply to this exact user message arrives."""
        deadline = monotonic() + self._settings.manus_response_timeout_seconds
        while monotonic() < deadline:
            events = await self._list_messages(task_id=task_id, headers=headers)
            reply = self._find_assistant_reply(events, user_message)
            if reply is not None:
                return reply
            if self._has_task_error(events, user_message):
                raise ManusApiError(
                    status_code=502,
                    code="manus_task_error",
                    message="The Manus task stopped with an error",
                )
            await asyncio.sleep(self._settings.manus_poll_interval_seconds)
        raise ManusTimeoutError("Timed out waiting for the Manus assistant response")

    async def _list_messages(
        self,
        *,
        task_id: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                "/v2/task.listMessages",
                params={"task_id": task_id, "order": "desc", "limit": 200},
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            self._log_diagnostic(
                endpoint="/v2/task.listMessages",
                http_status=None,
                task_id=task_id,
                error_code="manus_timeout",
                error_message="Manus API request timed out",
            )
            raise ManusTimeoutError("Manus API request timed out") from exc
        except httpx.RequestError as exc:
            self._log_diagnostic(
                endpoint="/v2/task.listMessages",
                http_status=None,
                task_id=task_id,
                error_code="manus_transport_error",
                error_message="Could not reach the Manus API",
            )
            raise ManusApiError(
                status_code=502,
                code="manus_transport_error",
                message="Could not reach the Manus API",
                endpoint="/v2/task.listMessages",
                task_id=task_id,
            ) from exc

        data = self._decode_json(response)
        self._log_response_diagnostic(
            endpoint="/v2/task.listMessages",
            http_status=response.status_code,
            request_task_id=task_id,
            response_data=data,
        )
        if response.status_code >= 400 or data.get("ok") is False:
            raise self._api_error(
                response.status_code,
                data,
                endpoint="/v2/task.listMessages",
                task_id=task_id,
            )
        messages = data.get("messages")
        if data.get("ok") is not True or not isinstance(messages, list):
            raise ManusInvalidResponseError(
                "Manus listMessages response is missing a messages array"
            )
        events = [event for event in messages if isinstance(event, dict)]
        return sorted(
            events,
            key=lambda event: event.get("timestamp")
            if isinstance(event.get("timestamp"), int)
            else -1,
        )

    def _log_response_diagnostic(
        self,
        *,
        endpoint: str,
        http_status: int,
        request_task_id: str | None,
        response_data: dict[str, Any],
    ) -> None:
        error = response_data.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        error_message = error.get("message") if isinstance(error, dict) else None
        response_task_id = response_data.get("task_id")
        task_id = response_task_id if isinstance(response_task_id, str) else request_task_id
        request_id = response_data.get("request_id")
        self._log_diagnostic(
            endpoint=endpoint,
            http_status=http_status,
            request_id=request_id if isinstance(request_id, str) else None,
            task_id=task_id,
            error_code=error_code if isinstance(error_code, str) else None,
            error_message=error_message if isinstance(error_message, str) else None,
        )

    def _log_diagnostic(
        self,
        *,
        endpoint: str,
        http_status: int | None,
        task_id: str | None,
        request_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if not self._settings.manus_diagnostics_enabled:
            return
        logger.info(
            "manus_api_diagnostic endpoint=%s http_status=%s request_id=%s "
            "task_id_present=%s task_id_length=%s error_code=%s error_message=%s",
            endpoint,
            http_status,
            request_id,
            task_id is not None,
            len(task_id) if task_id is not None else 0,
            error_code,
            error_message,
        )

    @staticmethod
    def _find_assistant_reply(events: list[dict[str, Any]], user_message: str) -> str | None:
        user_index = -1
        for index, event in enumerate(events):
            user_payload = event.get("user_message")
            if (
                event.get("type") == "user_message"
                and isinstance(user_payload, dict)
                and user_payload.get("content") == user_message
            ):
                user_index = index

        if user_index < 0:
            return None

        for event in events[user_index + 1 :]:
            assistant_payload = event.get("assistant_message")
            if event.get("type") != "assistant_message" or not isinstance(assistant_payload, dict):
                continue
            content = assistant_payload.get("content")
            if isinstance(content, str) and content.strip():
                return content
        return None

    @staticmethod
    def _has_task_error(events: list[dict[str, Any]], user_message: str) -> bool:
        user_index = -1
        for index, event in enumerate(events):
            user_payload = event.get("user_message")
            if (
                event.get("type") == "user_message"
                and isinstance(user_payload, dict)
                and user_payload.get("content") == user_message
            ):
                user_index = index
        return user_index >= 0 and any(
            event.get("type") == "error_message" for event in events[user_index + 1 :]
        )

    @staticmethod
    def _decode_json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ManusInvalidResponseError("Manus returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ManusInvalidResponseError("Manus returned a non-object JSON response")
        return data

    @staticmethod
    def _api_error(
        status_code: int,
        data: dict[str, Any],
        *,
        endpoint: str | None = None,
        task_id: str | None = None,
    ) -> ManusApiError:
        raw_error = data.get("error")
        if isinstance(raw_error, dict):
            raw_code = raw_error.get("code")
            raw_message = raw_error.get("message")
            code = raw_code if isinstance(raw_code, str) and raw_code else "manus_api_error"
            message = (
                raw_message
                if isinstance(raw_message, str) and raw_message
                else "Manus API returned an error"
            )
        else:
            code = "manus_api_error"
            message = "Manus API returned an error"
        request_id = data.get("request_id")
        return ManusApiError(
            status_code=status_code,
            code=code,
            message=message,
            request_id=request_id if isinstance(request_id, str) else None,
            endpoint=endpoint,
            task_id=task_id,
        )
