from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import get_settings
from app.core.dependencies import get_tutor_flow
from app.core.logging import configure_logging
from app.state.db import close_db, init_db

configure_logging()

app = FastAPI(title="Proactive ITS Baseline", version="0.1.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    await get_tutor_flow().ensure_default_template()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_db()


app.include_router(router, prefix="/v1")
app.mount("/media", StaticFiles(directory=settings.media_dir, check_dir=False), name="media")
