import os

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from . import loopwatch
from .config import settings
from .db import Base, engine, ensure_columns
from .recording import start_worker
from .routers import ai_room, analysis, auth, concept, interviews, overview, projects, public

@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)  # dev convenience; use Alembic migrations in production
    ensure_columns()  # a model that gained a column keeps an existing database working
    if settings.loop_watch_s > 0:
        loopwatch.start(settings.loop_watch_s)  # names whatever blocks the event loop in the log
    if settings.run_worker:
        start_worker()  # mixes uploaded recordings once an interview recording stops
    yield


app = FastAPI(title="星桐 TOTAL MVP", version="0.1.0", lifespan=lifespan)
for r in (auth.router, projects.router, interviews.router, overview.router, public.router, ai_room.router, concept.router, analysis.router):
    app.include_router(r)
if settings.loop_watch_s > 0:
    app.middleware("http")(loopwatch.slow_request_logger)  # diagnostics only: LOOP_WATCH_S=2 logs requests slower than 2 s


@app.get("/api/health")
def health():
    return {"ok": True}


STATIC = os.path.join(os.path.dirname(__file__), "static")


class RevalidatedStaticFiles(StaticFiles):
    """Static files that browsers must re-check before reuse.

    Without a Cache-Control header browsers guess a cache lifetime from the file's age and keep showing an old page,
    script or logo after an update. "no-cache" still lets them reuse the file when it is unchanged (ETag answer 304).
    """

    async def get_response(self, path: str, scope: dict) -> Response:
        """Serve the file as StaticFiles does, adding Cache-Control: no-cache."""
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatedStaticFiles(directory=STATIC, html=True), name="static")
