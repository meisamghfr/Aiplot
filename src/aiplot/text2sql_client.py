"""Narrow HTTP client for the authoritative Text-to-SQL service."""

from __future__ import annotations

from typing import Any

import httpx

from aiplot.dashboard.models import AnalyticsCapabilities


class TextToSQLError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class TextToSQLClient:
    def __init__(self, base_url: str, timeout_seconds: float = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    async def databases(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/databases")
        if not isinstance(data, list):
            raise TextToSQLError("Text-to-SQL returned an invalid database catalog.")
        return data

    async def models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/models")
        if not isinstance(data, list):
            raise TextToSQLError("Text-to-SQL returned an invalid model catalog.")
        return data

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._request("POST", "/api/chat", json=payload)
        if not isinstance(data, dict):
            raise TextToSQLError("Text-to-SQL returned an invalid chat response.")
        return data

    async def analytics_capabilities(self, db_id: str) -> AnalyticsCapabilities:
        data = await self._request("GET", f"/api/databases/{db_id}/analytics-capabilities")
        try:
            return AnalyticsCapabilities.model_validate(data)
        except ValueError as exc:
            raise TextToSQLError("Text-to-SQL returned invalid analytics capabilities.") from exc

    async def health(self) -> bool:
        try:
            data = await self._request("GET", "/api/health")
        except TextToSQLError:
            return False
        return isinstance(data, dict) and data.get("status") == "online"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TextToSQLError(
                "Text-to-SQL timed out while processing the request.", status_code=504
            ) from exc
        except httpx.RequestError as exc:
            raise TextToSQLError(
                "Text-to-SQL is unavailable. Start the service and verify TEXT2SQL_API_URL."
            ) from exc
        if response.is_error:
            detail = _error_detail(response)
            raise TextToSQLError(f"Text-to-SQL request failed: {detail}", status_code=502)
        try:
            return response.json()
        except ValueError as exc:
            raise TextToSQLError("Text-to-SQL returned a non-JSON response.") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(
            payload.get("detail") or payload.get("message") or f"HTTP {response.status_code}"
        )
    return f"HTTP {response.status_code}"
