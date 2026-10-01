"""Async client for the Hermes gateway API server (/v1/runs + SSE)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(self, base_url: str, api_key: str, model: str = "", provider: str = "", reasoning: str = ""):
        self.base = base_url.rstrip("/")
        self.model, self.provider, self.reasoning = model, provider, reasoning
        self._http = httpx.AsyncClient(
            base_url=self.base, headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(30.0, read=None))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> bool:
        try:
            r = await self._http.get("/health", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def start_run(self, text: str, session_id: str, instructions: str) -> str:
        body: dict[str, Any] = {"input": text, "session_id": session_id, "instructions": instructions}
        if self.model:
            body["model"] = self.model
        if self.provider:
            body["provider"] = self.provider
        if self.reasoning:
            body["model_options"] = {"reasoning_effort": self.reasoning}
        try:
            r = await self._http.post("/v1/runs", json=body)
        except httpx.HTTPError as e:
            raise HermesError(f"Hermes unreachable: {e}") from e
        if r.status_code >= 400:
            raise HermesError(f"Hermes rejected run ({r.status_code}): {r.text[:300]}")
        return r.json()["run_id"]

    async def events(self, run_id: str) -> AsyncIterator[dict]:
        """Yield parsed SSE events until a terminal event or the stream closes."""
        async with self._http.stream("GET", f"/v1/runs/{run_id}/events") as resp:
            if resp.status_code >= 400:
                raise HermesError(f"event stream failed ({resp.status_code})")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                yield ev
                if ev.get("event") in ("run.completed", "run.failed", "run.cancelled"):
                    return

    async def approve(self, run_id: str, choice: str, request_id: str | None = None) -> dict:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        r = await self._http.post(f"/v1/runs/{run_id}/approval", json=body)
        return r.json() if r.content else {}

    async def stop(self, run_id: str) -> None:
        try:
            await self._http.post(f"/v1/runs/{run_id}/stop", json={})
        except httpx.HTTPError:
            pass
