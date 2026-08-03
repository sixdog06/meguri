import pytest

from app.adapters import fakes, providers


def test_fake_mode_wires_fake_adapters():
    assert isinstance(providers.get_llm_gateway(), fakes.FakeLLMGateway)
    assert isinstance(providers.get_seichi_repository(), fakes.FakeSeichiRepository)
    assert isinstance(providers.get_transit_client(), fakes.FakeTransitClient)


def test_live_mode_raises_until_live_adapters_exist(monkeypatch):
    monkeypatch.setenv("MEGURI_ADAPTER_MODE", "live")
    providers.get_settings.cache_clear()

    with pytest.raises(NotImplementedError):
        providers.get_llm_gateway()

    providers.get_settings.cache_clear()
