from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from chainalpha.pipeline import run_scan, DEFAULT_QUERY
from chainalpha.memory.obsidian_loader import ObsidianLoader
from chainalpha.memory.index import HybridIndex


app = FastAPI(title="ChainAlpha API", version="0.1.0")


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
