from fastapi import FastAPI

from app.api import health

app = FastAPI(title="Meguri")
app.include_router(health.router)
