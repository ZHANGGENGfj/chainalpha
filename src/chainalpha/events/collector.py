from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from dotenv import load_dotenv

from chainalpha.events.llm_analyzer import event_llm_analyzer
from chainalpha.events.service import event_service, utc_now


logger = logging.getLogger("chainalpha.events.collector")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


class EventCollector:
    """Run the existing MediaCrawler finance-news collector in the background."""

    def __init__(self) -> None:
        configured_root = Path(
            os.getenv(
                "MEDIA_CRAWLER_ROOT",
                str(PROJECT_ROOT / "third_party" / "MediaCrawler-main"),
            )
        ).expanduser()
        self.app_root = configured_root.parent if configured_root.name == "data" else configured_root
        self.python = os.getenv("MEDIA_CRAWLER_PYTHON") or sys.executable
        self.interval_seconds = max(120, int(os.getenv("EVENT_COLLECT_INTERVAL_SECONDS", "120")))
        self.timeout_seconds = max(30, int(os.getenv("EVENT_COLLECT_TIMEOUT_SECONDS", "600")))
        self.enabled = os.getenv("EVENT_AUTO_COLLECT", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.keywords = _parse_keywords(
            os.getenv("EVENT_COLLECT_KEYWORDS", "")
        )
        self.status_path = Path(
            os.getenv(
                "EVENT_COLLECT_STATUS_PATH",
                str(PROJECT_ROOT / "data" / "event_store" / "collector_status.json"),
            )
        ).expanduser()
        self.lock_path = self.status_path.with_name("collector.lock")
        self._status = self._load_status()
        self._task: asyncio.Task[Any] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._wake = asyncio.Event()
        self._running = False
        self._stopping = False

    def status(self) -> dict[str, Any]:
        return {
            **self._status,
            "enabled": self.enabled,
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "keywords": self.keywords,
            "platform": "finance_news",
            "data_dir": str(self.app_root / "data" / "finance_news"),
        }

    async def start(self) -> None:
        if not self.enabled or self._task:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._scheduler(), name="chainalpha-event-collector")
        logger.info("Event collector started; interval=%ss", self.interval_seconds)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        await self._terminate_process()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._process = None
        self._release_process_lock()

    def request_refresh(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Automatic collection is disabled"
        if self._running:
            return False, "Collection is already running"
        self._wake.set()
        return True, "Collection scheduled"

    async def collect_once(self) -> dict[str, Any]:
        if self._running:
            return self.status()
        if not self._acquire_process_lock():
            self._status.update(
                {
                    "ok": False,
                    "state": "skipped",
                    "message": "Another collector process is running",
                    "checked_at": utc_now().isoformat(),
                }
            )
            self._save_status()
            return self.status()

        self._running = True
        started_at = utc_now()
        before_ids = {event.id for event in event_service.refresh_if_changed()[1]}
        self._status = {
            "ok": None,
            "state": "running",
            "message": "MediaCrawler is collecting finance news",
            "started_at": started_at.isoformat(),
            "completed_at": None,
            "next_run_at": None,
            "returncode": None,
            "fetched": 0,
            "kept": 0,
            "processed": 0,
            "new_events": 0,
            "dedup": {},
            "error": "",
        }
        self._save_status()

        try:
            result = await self._run_crawler()
            output = f"{result['stdout']}\n{result['stderr']}"
            parsed = _parse_crawler_output(output)
            after_events = event_service.refresh_if_changed(force=True)[1]
            try:
                after_events, llm_status = await asyncio.to_thread(
                    event_llm_analyzer.enrich,
                    after_events,
                )
                event_service.replace_events(after_events)
            except Exception as exc:  # noqa: BLE001
                llm_status = {
                    "enabled": event_llm_analyzer.enabled,
                    "state": "failed",
                    "message": f"{type(exc).__name__}: {exc}",
                    "analyzed": 0,
                    "cached": 0,
                    "failed": len(after_events),
                }
                logger.exception("Event AI analysis failed; preserving rule-based events")
            after_ids = {event.id for event in after_events}
            completed_at = utc_now()
            ok = result["returncode"] == 0
            self._status.update(
                {
                    "ok": ok,
                    "state": "completed" if ok else "failed",
                    "message": (
                        (
                            f"Collection completed; {parsed['kept']} new articles kept; "
                            f"AI analyzed {llm_status.get('analyzed', 0)}, "
                            f"cache hits {llm_status.get('cached', 0)}"
                        )
                        if ok
                        else "MediaCrawler exited with an error"
                    ),
                    "completed_at": completed_at.isoformat(),
                    "next_run_at": (
                        completed_at + timedelta(seconds=self.interval_seconds)
                    ).isoformat(),
                    "returncode": result["returncode"],
                    "fetched": parsed["fetched"],
                    "kept": parsed["kept"],
                    "processed": parsed["processed"],
                    "new_events": len(after_ids - before_ids),
                    "dedup": parsed["dedup"],
                    "llm_analysis": llm_status,
                    "error": "" if ok else _tail(output, 2000),
                    "output_tail": _tail(output, 5000),
                }
            )
            logger.info(
                "Event collection completed; kept=%s new_events=%s ai_analyzed=%s ai_cached=%s ai_failed=%s",
                parsed["kept"],
                len(after_ids - before_ids),
                llm_status.get("analyzed", 0),
                llm_status.get("cached", 0),
                llm_status.get("failed", 0),
            )
        except asyncio.TimeoutError:
            await self._terminate_process()
            self._mark_failed(f"MediaCrawler timed out after {self.timeout_seconds} seconds")
        except asyncio.CancelledError:
            await self._terminate_process()
            self._mark_failed("MediaCrawler collection was cancelled")
            raise
        except Exception as exc:
            self._mark_failed(f"{type(exc).__name__}: {exc}")
            logger.exception("Event collection failed")
        finally:
            self._running = False
            self._process = None
            self._release_process_lock()
            self._save_status()
        return self.status()

    async def _scheduler(self) -> None:
        # Run once at startup, then wait at least two minutes after completion.
        while not self._stopping:
            await self.collect_once()
            if self._stopping:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _run_crawler(self) -> dict[str, Any]:
        # Windows' selector event loop has no asyncio subprocess transport.
        # A worker thread keeps the API event loop responsive on every OS.
        return await asyncio.to_thread(self._run_crawler_sync)

    def _run_crawler_sync(self) -> dict[str, Any]:
        script = self.app_root / "main.py"
        if not script.exists():
            raise FileNotFoundError(f"MediaCrawler main.py not found: {script}")
        command = [
            self.python,
            str(script),
            "--platform",
            "finance_news",
            "--type",
            "search",
            "--keywords",
            ",".join(self.keywords),
            "--save_data_option",
            "jsonl",
            "--headless",
            "true",
        ]
        env = os.environ.copy()
        env.pop("SSLKEYLOGFILE", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = subprocess.Popen(
            command,
            cwd=str(self.app_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        try:
            stdout, stderr = self._process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._process.terminate()
            try:
                self._process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.communicate()
            raise asyncio.TimeoutError from exc
        return {
            "returncode": int(self._process.returncode or 0),
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "command": command,
        }

    async def _terminate_process(self) -> None:
        process = self._process
        if not process or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=8)
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            process.kill()
            await asyncio.to_thread(process.wait)

    def _mark_failed(self, message: str) -> None:
        completed_at = utc_now()
        self._status.update(
            {
                "ok": False,
                "state": "failed",
                "message": message,
                "completed_at": completed_at.isoformat(),
                "next_run_at": (
                    completed_at + timedelta(seconds=self.interval_seconds)
                ).isoformat(),
                "error": message,
            }
        )

    def _acquire_process_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"pid": os.getpid(), "started_at": utc_now().isoformat()},
            ensure_ascii=False,
        ).encode("utf-8")
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                if attempt == 0 and self._lock_is_stale():
                    try:
                        self.lock_path.unlink()
                    except OSError:
                        return False
                    continue
                return False
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            return True
        return False

    def _lock_is_stale(self) -> bool:
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", -1))
            if pid == os.getpid():
                return True
            if not _process_exists(pid):
                return True
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                self.lock_path.stat().st_mtime,
                tz=timezone.utc,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return age.total_seconds() > self.timeout_seconds + self.interval_seconds

    def _release_process_lock(self) -> None:
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if int(payload.get("pid", -1)) != os.getpid():
                return
            self.lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _load_status(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "ok": None,
                "state": "idle",
                "message": "Collector has not run yet",
                "completed_at": None,
            }

    def _save_status(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.status(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.status_path)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return str(pid).encode("ascii") in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _parse_keywords(value: str) -> list[str]:
    separators = r"[,，;；\n]+"
    return list(
        dict.fromkeys(item.strip() for item in re.split(separators, value) if item.strip())
    )[:12]


def _parse_crawler_output(output: str) -> dict[str, Any]:
    def number(pattern: str) -> int:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    return {
        "fetched": number(r"fetched\s+(\d+)\s+raw news"),
        "kept": number(r"kept\s+(\d+)\s+new raw news"),
        "processed": number(r"processed\s+(\d+)\s+news items"),
        "dedup": {
            "duplicate_url": number(r"url=(\d+)"),
            "duplicate_title_exact_30d": number(r"exact_title=(\d+)"),
            "duplicate_title_fuzzy_7d": number(r"fuzzy_title=(\d+)"),
            "invalid": number(r"invalid=(\d+)"),
        },
    }


def _tail(value: str, limit: int) -> str:
    return value[-limit:] if len(value) > limit else value


event_collector = EventCollector()
