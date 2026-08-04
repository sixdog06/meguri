"""健康检查端点：报告 API/DB 状态与各适配器当前模式（便于诊断环境配置）。"""

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.db import check_db_health

router = APIRouter(prefix="/api")


def get_db_health() -> str:
    """DB 健康检查依赖（测试可 override 成 up/down 驱动降级分支）。"""
    return check_db_health()


@router.get("/health")
def health(
    db_health: str = Depends(get_db_health),
    settings: Settings = Depends(get_settings),
) -> dict:
    """GET /api/health：db 异常时 status=degraded 但仍 200（检查端点本身要可达）。"""
    return {
        "status": "ok" if db_health == "up" else "degraded",
        "services": {"api": "up", "db": db_health},
        "adapters": {"llm": settings.adapter_mode, "seichi": settings.seichi_mode},
    }
