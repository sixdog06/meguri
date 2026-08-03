import pytest

from app.adapters import anitabi, fakes, providers


@pytest.fixture(autouse=True)
def reset_settings_cache():
    yield
    providers.get_settings.cache_clear()


def test_fake_mode_wires_fake_adapters(monkeypatch):
    monkeypatch.setenv("MEGURI_ADAPTER_MODE", "fake")
    monkeypatch.setenv("MEGURI_SEICHI_MODE", "fake")
    providers.get_settings.cache_clear()

    assert isinstance(providers.get_llm_gateway(), fakes.FakeLLMGateway)
    assert isinstance(providers.get_seichi_repository(), fakes.FakeSeichiRepository)
    assert isinstance(providers.get_transit_client(), fakes.FakeTransitClient)


def test_live_seichi_mode_wires_anitabi_repository(monkeypatch):
    monkeypatch.setenv("MEGURI_SEICHI_MODE", "live")
    providers.get_settings.cache_clear()

    assert isinstance(providers.get_seichi_repository(), anitabi.AnitabiSeichiRepository)


def test_live_adapter_mode_raises_until_live_llm_exists(monkeypatch):
    monkeypatch.setenv("MEGURI_ADAPTER_MODE", "live")
    providers.get_settings.cache_clear()

    with pytest.raises(NotImplementedError):
        providers.get_llm_gateway()
