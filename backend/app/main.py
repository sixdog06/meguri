"""FastAPI 应用入口：装配路由与启动期建表（init_db）。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import conversations, health
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建表（含 pgvector 扩展），关闭无额外清理。"""
    init_db()
    yield


app = FastAPI(title="Meguri", lifespan=lifespan)
app.include_router(health.router)
app.include_router(conversations.router)
