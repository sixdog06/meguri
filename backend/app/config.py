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

    model_config = {"env_prefix": "MEGURI_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
