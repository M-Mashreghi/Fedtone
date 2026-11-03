"""Thin HTTP client for the research API. The dashboard reads all data through it, so what
it shows is exactly what the API returns."""

from typing import Any

import httpx


class ApiError(RuntimeError):
    """The API could not be reached or answered with an error."""


class FedToneApiClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def connect(cls, base_url: str, timeout_seconds: float = 30.0) -> "FedToneApiClient":
        return cls(httpx.Client(base_url=base_url, timeout=timeout_seconds))

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            response = self._http.get(f"/api/v1{path}", params=clean)
        except httpx.HTTPError as exc:
            raise ApiError(f"FedTone API unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(f"{response.status_code}: {detail}")
        return response

    def timeline(self, **filters: Any) -> dict[str, Any]:
        return self._get("/timeline", filters).json()

    def document(self, document_id: str) -> dict[str, Any]:
        return self._get(f"/documents/{document_id}").json()

    def analogues(self, document_id: str, **params: Any) -> dict[str, Any]:
        return self._get(f"/documents/{document_id}/analogues", params).json()

    def research_note(self, document_id: str) -> str:
        return self._get(f"/documents/{document_id}/research-note").text

    def export_signals(self, fmt: str, **filters: Any) -> str:
        return self._get("/exports/signals", {"format": fmt, **filters}).text

    def telemetry(self) -> dict[str, Any]:
        return self._get("/system/telemetry").json()
