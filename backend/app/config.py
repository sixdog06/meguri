from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://meguri:meguri@localhost:5432/meguri"
    adapter_mode: Literal["fake", "live"] = "fake"

    model_config = {"env_prefix": "MEGURI_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
