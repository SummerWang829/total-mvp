import os

from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from . import jobs, loopwatch, mailer, project_members, ratelimit
from .listening import service as listening_service
from .config import settings
from .security import decode_access_token
from . import participant_code
from .db import Base, SessionLocal, engine, ensure_columns
from .recording import start_worker
from . import ai_calls, ai_prompts, chat_models, report_models  # noqa: F401  (registers the ai_call_log, ai_prompt_version, chat_threads and report_templates tables)
from .routers import creation as creation_router, ai_console, attachments as attachments_router, speech as speech_router, recruit_import as recruit_import_router, languages as languages_router, recruit_translate as recruit_translate_router, admin_data, ai_room, group_analysis as group_analysis_router, analysis, auth, concept, cues, group_metrics, interviews, listening, outline, room_assist as room_assist_router, insight_chat as insight_chat_router, pool, jobs as jobs_router, plans as plans_router, quant, recruit, screen_public, reels as reels_router, report, signals, studies, repo, overview, projects, public

@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)  # dev convenience; use Alembic migrations in production
    ensure_columns()  # a model that gained a column keeps an existing database working
    with SessionLocal() as db:
        project_members.backfill(db)  # nobody loses the access they had before project membership existed
        participant_code.backfill(db)  # people added before numbers were stored get theirs, in the order they came
    if settings.loop_watch_s > 0:
        loopwatch.start(settings.loop_watch_s)  # names whatever blocks the event loop in the log
    listening_service.start_worker()  # collects due sources and tags new mentions (public-opinion monitoring)
    mailer.start_worker()  # sends the account emails waiting in the outbox (does nothing without MAIL_HOST)
    if settings.run_worker:
        jobs.start_worker()  # runs queued transcribe / analyse jobs
        start_worker()  # mixes uploaded recordings once an interview recording stops
    yield


app = FastAPI(title="星桐 TOTAL MVP", version="0.1.0", lifespan=lifespan)
for r in (creation_router.router, ai_console.router, attachments_router.router, speech_router.router, auth.router, projects.router, interviews.router, overview.router, public.router, ai_room.router, concept.router, analysis.router, group_metrics.router, cues.router, signals.router, studies.router, outline.router, room_assist_router.router, languages_router.router, recruit_translate_router.router, insight_chat_router.router, repo.router, listening.router, recruit.router, recruit_import_router.router, quant.router, pool.router, report.router, admin_data.router, group_analysis_router.router, reels_router.router, jobs_router.router, plans_router.router, screen_public.router):
    app.include_router(r)
app.middleware("http")(ratelimit.middleware)  # no-op unless RATE_LIMIT=1


@app.middleware("http")
async def ai_call_org(request: Request, call_next):
    """Tell the AI call log whose request this is (from the signed token's company; attribution only - access is still decided by deps.current_ctx)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            ai_calls.set_org(decode_access_token(auth[7:].strip()).get("org", ""))
        except jwt.PyJWTError:
            pass  # an invalid token is refused by the endpoint itself
    return await call_next(request)


@app.middleware("http")
async def api_no_store(request: Request, call_next):
    """API answers are personal and change all the time: no proxy or edge node (EdgeOne) may keep a copy of them."""
    response = await call_next(request)
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response
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


if os.getenv("CONCEPT_TEST_FIXTURE") == "1":   # the automated tests' placeholder cards (the product has no materials of its own)
    app.mount("/concepts", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "tests", "concepts")), name="test_concepts")
app.mount("/", RevalidatedStaticFiles(directory=STATIC, html=True), name="static")
