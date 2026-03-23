import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.config import settings
from app.database import engine
from app.models import Base
from app.routers import projects, prompts, videos
from app.services.entra_auth import create_default_entra_auth_status, evaluate_startup_entra_auth_status
from app.services.media_backend_health import (
    create_default_media_backend_status,
    evaluate_startup_media_backend_status,
)
from app.services.openai_client import close_openai_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時建立資料表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.entra_auth_status, app.state.media_backend_status = await asyncio.gather(
        evaluate_startup_entra_auth_status(),
        evaluate_startup_media_backend_status(),
    )
    yield
    await close_openai_client()
    await engine.dispose()


app = FastAPI(title="Sora VideoGen", version="0.1.0", lifespan=lifespan)
app.state.entra_auth_status = create_default_entra_auth_status()
app.state.media_backend_status = create_default_media_backend_status()
FAVICON_PATH = Path("app/static/favicon.ico")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/projects/", status_code=307)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(FAVICON_PATH, media_type="image/vnd.microsoft.icon")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")

app.include_router(projects.router)
app.include_router(prompts.router)
app.include_router(videos.router)


def run() -> None:
    # 若透過 Python 而非 uvicorn CLI 啟動應用程式，
    # 就從 pydantic settings 讀取 host/port，讓 APP_HOST 與 APP_PORT 真的生效。
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )


if __name__ == "__main__":
    run()
