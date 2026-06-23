from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chainalpha.pipeline import run_scan, DEFAULT_QUERY
from chainalpha.memory.obsidian_loader import ObsidianLoader
from chainalpha.memory.index import HybridIndex
from chainalpha.api.research_runs import router as research_runs_router


app = FastAPI(title="ChainAlpha API", version="0.1.0")
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
if _FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


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
