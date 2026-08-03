from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://meguri:meguri@localhost:5432/meguri"
    adapter_mode: Literal["fake", "live"] = "fake"
    # 圣地数据源独立开关（与 LLM 的 adapter_mode 解耦，见 #4）：
    # 开发默认 live 直连 anitabi，方便演示真实数据；测试经 conftest /
    # dependency_overrides 固定为 fake。
    seichi_mode: Literal["fake", "live"] = "live"
    # 交通/开放时间数据源（#6）：live = 本地 OTP 服务 + Overpass。
    # 默认 fake（不依赖重服务）；dev.sh / compose 显式开 live。
    # OTP 不可达时 Navigator 自动降级为估算段（degraded 标记），不报错。
    transit_mode: Literal["fake", "live"] = "fake"
    hours_mode: Literal["fake", "live"] = "fake"
    otp_base_url: str = "http://localhost:8081/otp"
    overpass_url: str = "https://overpass-api.de/api/interpreter"

    model_config = {"env_prefix": "MEGURI_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
