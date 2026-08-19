from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

# Managed Windows environments may inject an unwritable TLS key-log path.
os.environ.pop("SSLKEYLOGFILE", None)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from chainalpha.pipeline import run_scan, DEFAULT_QUERY
from chainalpha.memory.obsidian_loader import ObsidianLoader
from chainalpha.memory.index import HybridIndex
from chainalpha.api.research_runs import router as research_runs_router
from chainalpha.auth.router import auth_service, router as auth_router
from chainalpha.events.router import router as financial_events_router
from chainalpha.events.collector import event_collector
from chainalpha.five_good.router import router as five_good_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    auth_service.initialize()
    await event_collector.start()
    try:
        yield
    finally:
        await event_collector.stop()


app = FastAPI(title="ChainAlpha API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(research_runs_router)
app.include_router(auth_router)
app.include_router(financial_events_router)
app.include_router(five_good_router)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/app"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


class SPAStaticFiles(StaticFiles):
    """Serve index.html for client-side routes while preserving real assets."""

    async def get_response(self, path, scope):
        # The current frontend uses relative asset URLs so it can also run from
        # a plain local HTTP server. On a nested SPA route those URLs become
        # ``events/<id>/src/...``; normalize them back to the real asset path.
        normalized = path.replace("\\", "/")
        if "/src/" in normalized:
            asset_path = "src/" + normalized.split("/src/", 1)[1]
            return await super().get_response(asset_path, scope)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or "." in Path(path).name:
                raise
            return await super().get_response("index.html", scope)


if _FRONTEND_DIR.exists():
    app.mount("/app", SPAStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


@app.get("/", include_in_schema=False)
def frontend_home() -> RedirectResponse:
    return RedirectResponse(url="/app/")


class SearchRequest(BaseModel):
    vault_path: str
    query: str
    top_k: int = 8


class ScanRequest(BaseModel):
    vault_path: str
    query: str = DEFAULT_QUERY
    top_k_chunks: int = 80


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/search")
def search(req: SearchRequest) -> dict:
    loader = ObsidianLoader(req.vault_path)
    chunks = loader.load_chunks()
    index = HybridIndex(chunks)
    results = index.search(req.query, top_k=req.top_k)
    return {
        "results": [
            {
                "score": c.score,
                "path": c.path,
                "title": c.title,
                "heading": c.heading,
                "text": c.text[:800],
                "tags": c.tags,
                "wikilinks": c.wikilinks,
            }
            for c in results
        ]
    }


@app.post("/scan")
def scan(req: ScanRequest) -> dict:
    signals, markdown, _ = run_scan(req.vault_path, query=req.query, top_k_chunks=req.top_k_chunks)
    return {
        "count": len(signals),
        "signals": [s.to_dict() for s in signals],
        "markdown": markdown,
    }
