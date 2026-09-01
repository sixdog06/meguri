import pytest

from app.adapters import anitabi, providers


@pytest.fixture(autouse=True)
def reset_settings_cache():
    yield
    providers.get_settings.cache_clear()


def test_live_seichi_mode_wires_anitabi_repository(monkeypatch):
    monkeypatch.setenv("MEGURI_SEICHI_MODE", "live")
    providers.get_settings.cache_clear()

    # live = 本地映射 + anitabi 实时（故障显式 503 / 本地包兜底）
    assert isinstance(providers.get_seichi_repository(), anitabi.AnitabiSeichiRepository)


def test_transit_wires_otp_client():
    from app.adapters import otp

    # 交通唯一实现即 OTP（无模式开关）
    assert isinstance(providers.get_transit_client(), otp.OTPTransitClient)


def test_llm_gateway_无key时报错_有key时接LangChain(monkeypatch):
    from app.adapters import llm

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
