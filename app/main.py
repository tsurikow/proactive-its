from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import configure_logging
from app.state.db import init_db

configure_logging()

app = FastAPI(title="Proactive ITS Baseline", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


app.include_router(router, prefix="/v1")
