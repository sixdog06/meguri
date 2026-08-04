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
    from app.adapters import anitabi

    monkeypatch.setenv("MEGURI_SEICHI_MODE", "live")
    providers.get_settings.cache_clear()

    # live = 本地映射 + anitabi 实时（故障显式 503，不降级本地数据包）
    assert isinstance(providers.get_seichi_repository(), anitabi.AnitabiSeichiRepository)


def test_live_transit_mode_wires_otp_client(monkeypatch):
    from app.adapters import otp, overpass

    monkeypatch.setenv("MEGURI_TRANSIT_MODE", "live")
    monkeypatch.setenv("MEGURI_HOURS_MODE", "live")
    providers.get_settings.cache_clear()

    assert isinstance(providers.get_transit_client(), otp.OTPTransitClient)
    assert isinstance(providers.get_opening_hours_source(), overpass.OverpassOpeningHours)


def test_live_adapter_mode_无key时报错_有key时接LangChain(monkeypatch):
    from app.adapters import llm

    monkeypatch.setenv("MEGURI_ADAPTER_MODE", "live")
    monkeypatch.delenv("MEGURI_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("app.config.Settings.openai_api_key", None, raising=False)
    providers.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        providers.get_llm_gateway()

    monkeypatch.setenv("MEGURI_OPENAI_API_KEY", "test-key")
    providers.get_settings.cache_clear()
    try:
        assert isinstance(providers.get_llm_gateway(), llm.LangChainLLMGateway)
    finally:
        providers.get_settings.cache_clear()
