"""FastAPI 应用入口：装配路由与启动期建表（init_db）。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import conversations, health
from app.config import get_settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表（含 pgvector 扩展），关闭无额外清理。"""
    init_db()
    yield


app = FastAPI(title="Meguri", lifespan=lifespan)
app.include_router(health.router)
app.include_router(conversations.router)

# 离线数据包截图（file 模式）：ingest_seichi 把 anitabi 截图下载到
# <seichi_data_dir>/images/，数据包 JSON 的 image 字段改写到这个挂载点
app.mount(
    "/api/seichi-images",
    StaticFiles(directory=Path(get_settings().seichi_data_dir) / "images", check_dir=False),
    name="seichi-images",
)
