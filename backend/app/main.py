from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import conversations, health
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Meguri", lifespan=lifespan)
app.include_router(health.router)
app.include_router(conversations.router)
