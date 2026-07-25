from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_ROOT = PROJECT_ROOT / "data" / "topic_runs"
SESSION_ROOT = PROJECT_ROOT / "data" / "chat_sessions"
SAFE_ARTIFACTS = {
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".jsonl": "application/x-ndjson",
}

router = APIRouter(prefix="/api", tags=["research-runs"])


class CreateRunRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=300)
    # The web product opens in single-company deep-research mode. Other
    # workflows remain available from the CLI for internal use.
    report_type: Literal["topic", "company", "industry-chain", "daily"] = "company"
    depth: Literal["quick", "standard", "deep"] = "standard"
    llm_summary: bool = True
    chain_visual: bool = True
    report_quality_check: bool = True
    include_a_share_reports: bool = True
    company_profile_source: Literal["llm", "local", "hybrid", "resolver"] = "llm"
    retrieval_backend: Literal["auto", "hybrid", "llamaindex"] = "auto"
    skip_refresh_news: bool = True
    limit: int = Field(80, ge=1, le=300)
    top_k_chunks: int = Field(60, ge=1, le=300)
    top_k_companies: int = Field(6, ge=1, le=30)
    session_id: str = ""
    assistant_message_id: str = ""


class ChatSessionPayload(BaseModel):
    title: str = "新研究"
    messages: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class RunningTask:
    run_id: str
    topic: str
    started_at: str
    output_dir: Path
    log_path: Path
    process: subprocess.Popen
    request: dict = field(default_factory=dict)


_RUNNING: dict[str, RunningTask] = {}


@router.post("/runs")
def create_run(req: CreateRunRequest) -> dict:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RUN_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    # The public web workspace is intentionally a single-company product.
    # Normalize stale browser state and direct API calls before persisting or
    # starting a task, so an old "industry-chain" selection cannot leak into
    # the company-report pipeline.
    request_data = req.dict()
    request_data["report_type"] = "company"
    request_data["llm_summary"] = True
    request_data["chain_visual"] = True
    request_data["report_quality_check"] = True
    request_data["include_a_share_reports"] = True
    (output_dir / "run_request.json").write_text(
        json.dumps(request_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cmd = _build_run_command(req, run_id)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    env.pop("SSLKEYLOGFILE", None)

    log_file = log_path.open("w", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        log_file.close()
        raise

    _RUNNING[run_id] = RunningTask(
        run_id=run_id,
        topic=req.topic,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        output_dir=output_dir,
        log_path=log_path,
        process=process,
        request=request_data,
    )
    return {"run_id": run_id, "status": "running", "output_dir": _rel(output_dir)}


@router.get("/sessions")
def list_sessions() -> dict:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    sessions = [_read_json(path) for path in SESSION_ROOT.glob("*.json")]
    sessions = [item for item in sessions if item.get("id")]
    sessions.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return {"sessions": sessions[:100]}


@router.post("/sessions")
def create_session(payload: ChatSessionPayload) -> dict:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S_%f")
    session = {
        "id": session_id,
        "title": payload.title or "新研究",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": payload.messages,
    }
    _write_session(session)
    return session


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return _read_json(path)


@router.put("/sessions/{session_id}")
def save_session(session_id: str, payload: ChatSessionPayload) -> dict:
    path = _session_path(session_id)
    existing = _read_json(path) if path.exists() else {}
    session = {
        "id": session_id,
        "title": payload.title or existing.get("title") or "新研究",
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
        "messages": payload.messages,
    }
    _write_session(session)
    return session


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    path.unlink()
    return {"deleted": True, "id": session_id}


@router.get("/runs")
def list_runs() -> dict:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    run_dirs = [p for p in RUN_ROOT.iterdir() if p.is_dir()]
    run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"runs": [_run_summary(path) for path in run_dirs[:50]]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    path = _run_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_summary(path)


@router.get("/runs/{run_id}/stream")
def stream_run(run_id: str) -> StreamingResponse:
    path = _run_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    def events():
        last_payload = ""
        for _ in range(60 * 60 * 5):
            summary = _run_summary(path)
            payload = json.dumps(summary, ensure_ascii=False)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if summary.get("status") in {"completed", "failed", "unknown"}:
                break
            time.sleep(0.05)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, format: Literal["markdown", "html"] = "markdown") -> dict:
    path = _run_path(run_id)
    report_type = _report_type_for(path, _RUNNING.get(run_id))
    report_path = _best_report_path(path, format, report_type)
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "run_id": run_id,
        "format": format,
        "content": report_path.read_text(encoding="utf-8", errors="replace"),
        "source": report_path.name,
    }


@router.get("/runs/{run_id}/artifacts")
def list_artifacts(run_id: str) -> dict:
    path = _run_path(run_id)
    artifacts = [_artifact_info(run_id, item) for item in path.iterdir() if item.is_file() and item.suffix.lower() in SAFE_ARTIFACTS]
    artifacts.sort(key=lambda item: item["name"])
    return {"run_id": run_id, "artifacts": artifacts}


@router.get("/runs/{run_id}/files/{filename}")
def get_artifact_file(run_id: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = _run_path(run_id) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media_type = SAFE_ARTIFACTS.get(path.suffix.lower())
    if not media_type:
        raise HTTPException(status_code=400, detail="Unsupported artifact type")
    return FileResponse(path, media_type=media_type, filename=path.name)


def _build_run_command(req: CreateRunRequest, run_id: str) -> list[str]:
    cmd = [
        sys.executable,
        "-B",
        "run_topic_research.py",
        "--topic",
        req.topic,
        "--run-id",
        run_id,
        "--limit",
        str(req.limit),
        "--top-k-chunks",
        str(req.top_k_chunks),
        "--top-k-companies",
        str(req.top_k_companies),
        "--company-profile-source",
        req.company_profile_source,
        "--retrieval-backend",
        req.retrieval_backend,
        "--output-mode",
        "final",
        "--research-type",
        "company",
    ]
    cmd.extend(["--llm-summary", "--chain-visual", "--include-a-share-reports"])
    if req.skip_refresh_news:
        cmd.append("--skip-refresh-news")
    return cmd


def _run_summary(path: Path) -> dict:
    run_id = path.name
    running = _RUNNING.get(run_id)
    status = _status_for(run_id)
    request_data = running.request if running else _read_json(path / "run_request.json")
    if request_data.get("report_type") not in {"topic", "company", "industry-chain", "daily"}:
        request_data = {**request_data, "report_type": _report_type_for(path, running)}
    topic_data = _read_json(path / "topic_research.json")
    query_data = _read_json(path / "query_expansion.json")
    artifacts = [item.name for item in path.iterdir() if item.is_file() and item.suffix.lower() in SAFE_ARTIFACTS]
    companies = _company_names(topic_data, query_data)
    timeline = _timeline_for(path)
    # The chat UI intentionally exposes source pages, not intermediate model
    # drafts. Final output is shown only after the report artifact is complete.
    live_output = ""
    return {
        "run_id": run_id,
        "topic": topic_data.get("topic") or (running.topic if running else query_data.get("original_query")) or run_id,
        "status": status,
        "stage": "已完成" if status == "completed" else "运行中",
        "started_at": running.started_at if running else _mtime(path),
        "output_dir": _rel(path),
        "metrics": {
            "documents": _document_count(topic_data),
            "signals": len(topic_data.get("signals") or []),
            "companies": len(companies),
            "data_score": _data_score(path, topic_data, companies),
        },
        "companies": companies,
        "artifacts": artifacts,
        "request": request_data,
        "log_tail": _tail(path / "run.log"),
        "timeline": timeline,
        "live_output": live_output,
    }


def _timeline_for(path: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(_read_run_events(path / "run_events.jsonl"))
    if not events:
        events.extend(_timeline_from_existing_artifacts(path))
    visible = [item for item in events if item.get("type") != "model_delta"]
    return [_normalize_timeline_event(item) for item in visible[-limit:]]


def _live_output_for(path: Path, *, max_chars: int = 16000) -> str:
    parts: list[str] = []
    for item in _read_run_events(path / "run_events.jsonl"):
        if item.get("type") == "model_delta":
            parts.append(str(item.get("message") or ""))
    text = "".join(parts)
    return text[-max_chars:]


def _read_run_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _timeline_from_existing_artifacts(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    query = _read_json(path / "query_expansion.json")
    if query:
        events.append(
            {
                "ts": _mtime(path / "query_expansion.json"),
                "type": "stage",
                "title": "查询理解完成",
                "message": ", ".join([str(item) for item in (query.get("keywords") or query.get("search_keywords") or [])[:8]]),
                "data": {"artifact": "query_expansion.json"},
            }
        )
    tool = _read_json(path / "tool_research_agent.json")
    for step in (tool.get("steps") or [])[:20]:
        if not isinstance(step, dict):
            continue
        events.append(
            {
                "ts": tool.get("generated_at") or _mtime(path / "tool_research_agent.json"),
                "type": "tool_result",
                "title": "工具检索",
                "message": f"{step.get('action')}: {step.get('query')} ({step.get('documents_found')} docs)",
                "data": step,
            }
        )
    if (path / "final_report.md").exists():
        events.append(
            {
                "ts": _mtime(path / "final_report.md"),
                "type": "model_output",
                "title": "最终报告已生成",
                "message": _tail(path / "final_report.md", max_chars=900),
                "data": {"artifact": "final_report.md"},
            }
        )
    return events


def _normalize_timeline_event(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    documents = data.get("documents") if isinstance(data.get("documents"), list) else []
    sites: list[str] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url") or "")
        host = urlparse(url).netloc
        if host:
            sites.append(host)
    return {
        "ts": item.get("ts") or item.get("generated_at") or "",
        "type": item.get("type") or "event",
        "title": item.get("title") or "运行事件",
        "message": item.get("message") or "",
        "sites": list(dict.fromkeys(sites))[:8],
        "data": data,
    }


def _current_stage(status: str, timeline: list[dict[str, Any]]) -> str:
    if status == "completed":
        return "已完成"
    for item in reversed(timeline):
        if item.get("type") in {"stage", "tool_plan", "tool_result", "model_output", "artifact"}:
            title = str(item.get("title") or "").strip()
            if title:
                return title
    if status == "failed":
        return "失败"
    return "运行中"


def _status_for(run_id: str) -> str:
    running = _RUNNING.get(run_id)
    if not running:
        path = _run_path(run_id)
        report_type = _report_type_for(path, None)
        if _best_report_path(path, "markdown", report_type).exists() or _best_report_path(path, "html", report_type).exists():
            return "completed"
        if (path / "run.log").exists():
            return "failed"
        return "unknown"
    code = running.process.poll()
    if code is None:
        return "running"
    _RUNNING.pop(run_id, None)
    return "completed" if code == 0 else "failed"


def _report_type_for(path: Path, running: RunningTask | None) -> str:
    request_data = running.request if running else _read_json(path / "run_request.json")
    report_type = request_data.get("report_type")
    if report_type in {"topic", "company", "industry-chain", "daily"}:
        return str(report_type)
    if (path / "daily_market_scan.md").exists() or (path / "daily_market_scan.html").exists():
        return "daily"
    if (
        (path / "llm_chain_bottleneck_report.md").exists()
        or (path / "chain_bottleneck_report.md").exists()
        or (path / "llm_chain_bottleneck_report.html").exists()
    ):
        return "industry-chain"
    return "topic"


def _best_report_path(path: Path, format: str, report_type: str) -> Path:
    candidates_by_type = _report_candidates(format)
    candidates = candidates_by_type.get(report_type) or candidates_by_type["topic"]
    for name in candidates:
        candidate = path / name
        if candidate.exists():
            return candidate
    return path / candidates[0]


def _report_candidates(format: str) -> dict[str, list[str]]:
    if format == "html":
        return {
            "daily": [
                "daily_market_scan.html",
            ],
            "industry-chain": [
                "llm_chain_bottleneck_report.html",
                "chain_bottleneck_report.html",
                "final_report.html",
                "five_agent_report.html",
                "topic_professional_report.html",
                "topic_research.html",
            ],
            "company": [
                "final_report.html",
                "llm_professional_report.html",
                "topic_professional_report.html",
                "five_agent_report.html",
                "topic_research.html",
            ],
            "topic": [
                "final_report.html",
                "llm_professional_report.html",
                "topic_professional_report.html",
                "five_agent_report.html",
                "topic_research.html",
            ],
        }
    return {
        "daily": [
            "daily_market_scan.md",
        ],
        "industry-chain": [
            "llm_chain_bottleneck_report.md",
            "chain_bottleneck_report.md",
            "final_report.md",
            "five_agent_report.md",
            "topic_professional_report.md",
        ],
        "company": [
            "final_report.md",
            "llm_professional_report.md",
            "topic_professional_report.md",
            "five_agent_report.md",
        ],
        "topic": [
            "final_report.md",
            "llm_professional_report.md",
            "topic_professional_report.md",
            "five_agent_report.md",
        ],
    }


def _run_path(run_id: str) -> Path:
    cleaned = "".join(ch for ch in run_id if ch.isalnum() or ch in "._-")
    if cleaned != run_id or not cleaned:
        raise HTTPException(status_code=400, detail="Invalid run_id")
    return RUN_ROOT / cleaned


def _session_path(session_id: str) -> Path:
    cleaned = "".join(ch for ch in session_id if ch.isalnum() or ch in "._-")
    if cleaned != session_id or not cleaned:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    return SESSION_ROOT / f"{cleaned}.json"


def _write_session(session: dict[str, Any]) -> None:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    path = _session_path(str(session["id"]))
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def _document_count(topic_data: dict) -> int:
    keys = ("selected_documents", "recent_documents", "recent_news", "documents")
    return max((len(topic_data.get(key) or []) for key in keys), default=0)


def _company_names(topic_data: dict, query_data: dict) -> list[str]:
    names: list[str] = []
    for item in topic_data.get("companies") or []:
        company = item.get("company") if isinstance(item, dict) else None
        if isinstance(company, dict):
            name = company.get("name")
        else:
            name = item.get("name") if isinstance(item, dict) else None
        if name:
            names.append(str(name))
    for name in query_data.get("company_names") or []:
        names.append(str(name))
    for keyword in query_data.get("keywords") or []:
        if len(str(keyword)) <= 12 and any(token in str(keyword) for token in ("科技", "股份", "材料", "巨石")):
            names.append(str(keyword))
    return list(dict.fromkeys(names))[:12]


def _data_score(path: Path, topic_data: dict, companies: list[str]) -> int:
    score = 0
    if (path / "final_report.md").exists():
        score += 20
    if (path / "report_quality_audit.json").exists():
        score += 15
    if (path / "chain_visual.svg").exists():
        score += 10
    score += min(_document_count(topic_data) * 3, 25)
    score += min(len(topic_data.get("signals") or []) * 5, 15)
    score += min(len(companies) * 3, 15)
    return min(score, 100)


def _artifact_info(run_id: str, path: Path) -> dict:
    return {
        "name": path.name,
        "kind": path.suffix.lower().lstrip("."),
        "url": f"/api/runs/{run_id}/files/{path.name}",
        "size": path.stat().st_size,
    }


def _tail(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
