from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.db import check_db_health

router = APIRouter(prefix="/api")


def get_db_health() -> str:
    return check_db_health()


@router.get("/health")
def health(
    db_health: str = Depends(get_db_health),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {
        "status": "ok" if db_health == "up" else "degraded",
        "services": {"api": "up", "db": db_health},
        "adapters": settings.adapter_mode,
    }
