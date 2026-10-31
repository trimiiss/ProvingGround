"""A small client for the ProvingGround platform API (used by `pground push` and `api:` baselines)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .models import Run

DEFAULT_URL = "http://localhost:8000"


class PlatformError(RuntimeError):
    pass


class PlatformClient:
    def __init__(
        self, base_url: str | None = None, api_key: str | None = None, *, timeout: float = 120.0
    ) -> None:
        self.base_url = (base_url or os.environ.get("PGROUND_API_URL") or DEFAULT_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("PGROUND_API_KEY") or ""
        self._client = httpx.Client(
            base_url=self.base_url, headers={"x-api-key": self.api_key}, timeout=timeout
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(f"cannot reach the platform at {self.base_url}: {exc}") from exc
        if resp.status_code == 401:
            raise PlatformError("the platform rejected the API key (set PGROUND_API_KEY)")
        if resp.status_code >= 400:
            raise PlatformError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    def push_run(self, run: Run) -> dict[str, Any]:
        """Upload a run (results, scores, spans) so it appears in the dashboard and can be a baseline."""
        data: dict[str, Any] = self._request("POST", "/v1/runs", content=run.model_dump_json(),
                                             headers={"content-type": "application/json"}).json()  # fmt: skip
        return data

    def get_run(self, run_id: str) -> Run:
        """Download a stored run with all its case results."""
        listing = self._request("GET", f"/v1/runs/{run_id}/report", params={"format": "json"})
        return Run.model_validate_json(listing.text)

    def latest_run(self, agent: str, *, kind: str = "eval", label: str | None = None) -> Run | None:
        params: dict[str, Any] = {"agent": agent, "kind": kind, "limit": 1}
        if label:
            params["label"] = label
        items = self._request("GET", "/v1/runs", params=params).json()["items"]
        return self.get_run(items[0]["id"]) if items else None

    def close(self) -> None:
        self._client.close()
