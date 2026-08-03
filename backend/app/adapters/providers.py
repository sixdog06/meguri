"""Adapter wiring: selects fake or live implementations per settings.adapter_mode.

This is the single place that consumes MEGURI_ADAPTER_MODE — the rest of the
app depends on the ports (app.adapters.ports) via these providers, and tests
override the providers at the HTTP seam (FastAPI dependency_overrides).
"""

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository, FakeTransitClient
from app.adapters.ports import LLMGateway, SeichiRepository, TransitClient
from app.config import get_settings


def _live_not_available(name: str) -> None:
    raise NotImplementedError(f"live adapter for {name} arrives with a later ticket")


def get_llm_gateway() -> LLMGateway:
    if get_settings().adapter_mode == "fake":
        return FakeLLMGateway()
    _live_not_available("LLMGateway")


def get_seichi_repository() -> SeichiRepository:
    if get_settings().adapter_mode == "fake":
        return FakeSeichiRepository()
    _live_not_available("SeichiRepository")


def get_transit_client() -> TransitClient:
    if get_settings().adapter_mode == "fake":
        return FakeTransitClient()
    _live_not_available("TransitClient")
