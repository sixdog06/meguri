"""Deterministic fakes for the adapter ports.

Used by tests (wired at the HTTP seam via dependency overrides) and by local
development while live adapters don't exist yet.
"""

from typing import Any


class FakeLLMGateway:
    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[list[dict[str, str]]] = []  # 每次 complete 收到的消息，供测试断言

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self._scripted:
            return self._scripted.pop(0)
        return "fake-llm-response"


class FakeSeichiRepository:
    def __init__(self, seichi: list[dict[str, Any]] | None = None) -> None:
        self._seichi = list(seichi or [])

    def search_seichi(self, work: str, area: str) -> list[dict[str, Any]]:
        return [s for s in self._seichi if s.get("work") == work and s.get("area") == area]


class FakeTransitClient:
    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, Any]:
        return {"mode": "fake", "duration_minutes": 0, "fare_yen": 0, "estimate": True}
