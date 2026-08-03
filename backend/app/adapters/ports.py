"""Ports for external services.

Every external dependency (LLM provider, seichi data source, transit engine)
is accessed through one of these protocols. Tests and local development wire
fakes (app.adapters.fakes); live implementations arrive with later tickets.
"""

from typing import Any, Protocol


class LLMGateway(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class SeichiRepository(Protocol):
    def search_seichi(self, work: str, area: str) -> list[dict[str, Any]]: ...


class TransitClient(Protocol):
    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, Any]: ...
