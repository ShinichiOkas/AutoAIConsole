"""FastAPI server for AutoAIConsole production backend (ConPTY + Ollama + WebSockets)."""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from autoaiconsole import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, __version__
from autoaiconsole.ollama_client import fetch_ollama_models
from autoaiconsole.session import ConsoleSession


def _resolve_web_dir() -> Path:
    """Locate bundled static files (PyInstaller onefile extracts them under sys._MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "autoaiconsole" / "web"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "web"


WEB_DIR = _resolve_web_dir()

session = ConsoleSession()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    session.attach_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        session.shutdown()


app = FastAPI(title="AutoAIConsole", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class ChatRequest(BaseModel):
    message: str


class TerminalStartRequest(BaseModel):
    agent_preset: str = "pwsh"
    cwd: str = "."
    command: str | None = None


class TerminalInputRequest(BaseModel):
    text: str
    press_enter: bool = True


class MonitorModeRequest(BaseModel):
    mode: str = Field(pattern="^(autonomous|paused|manual)$")


class MissionUpdateRequest(BaseModel):
    goal: str
    policy: str
    boundaries: list[str] = Field(default_factory=list)


class SettingsUpdateRequest(BaseModel):
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL
    idle_threshold_sec: float = 2.5


class BoundaryResolveRequest(BaseModel):
    action: str = Field(pattern="^(recommend|allow|custom|abort)$")
    custom_text: str = ""


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "mode": "production",
        "default_model": DEFAULT_OLLAMA_MODEL,
        "default_ollama_url": DEFAULT_OLLAMA_URL,
        "pty_running": session.pty.is_running,
    }


@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    return session.get_full_state()


@app.get("/api/screen")
async def get_screen() -> dict[str, Any]:
    return {
        "pty_running": session.pty.is_running,
        "cols": session.pty.cols,
        "rows": session.pty.rows,
        "screen_text": session.pty.get_screen_text(),
    }


@app.get("/api/models")
async def list_models(ollama_url: str = DEFAULT_OLLAMA_URL) -> dict[str, Any]:
    return await fetch_ollama_models(ollama_url)


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict[str, Any]:
    return await session.handle_user_chat(req.message)


@app.post("/api/terminal/start")
async def api_terminal_start(req: TerminalStartRequest) -> dict[str, Any]:
    ok, msg = session.start_terminal(
        preset=req.agent_preset,
        cwd=req.cwd,
        custom_command=req.command,
    )
    return {"ok": ok, "message": msg, "status": session.get_status_payload()}


@app.post("/api/terminal/stop")
async def api_terminal_stop() -> dict[str, Any]:
    session.stop_terminal()
    return {"ok": True, "status": session.get_status_payload()}


@app.post("/api/terminal/input")
async def api_terminal_input(req: TerminalInputRequest) -> dict[str, Any]:
    if not session.pty.is_running:
        return {"ok": False, "message": "ターミナルが起動していません"}
    ok = await session.pty.send_text_async(req.text, press_enter=req.press_enter)
    if ok and session.mode == "autonomous" and session.engine_state in ("watching", "thinking"):
        session.engine_state = "user"
        session.last_eval_note = "ユーザー直接入力を優先中（AutoAI待機）"
        session._broadcast_state()
    return {"ok": ok}


@app.post("/api/terminal/interrupt")
async def api_terminal_interrupt() -> dict[str, Any]:
    ok = session.send_interrupt()
    return {"ok": ok}


@app.post("/api/monitor/mode")
async def api_monitor_mode(req: MonitorModeRequest) -> dict[str, Any]:
    session.set_mode(req.mode)
    return {"ok": True, "status": session.get_status_payload()}


@app.post("/api/monitor/resume")
async def api_monitor_resume() -> dict[str, Any]:
    session.resume_autonomous()
    return {"ok": True, "status": session.get_status_payload()}


@app.post("/api/monitor/evaluate")
async def api_monitor_evaluate() -> dict[str, Any]:
    return await session.evaluate_once(force=True)


@app.post("/api/boundary/resolve")
async def api_boundary_resolve(req: BoundaryResolveRequest) -> dict[str, Any]:
    return await session.resolve_boundary(req.action, req.custom_text)


@app.post("/api/mission")
async def api_mission(req: MissionUpdateRequest) -> dict[str, Any]:
    session.update_mission(req.goal, req.policy, req.boundaries)
    return {"ok": True, "mission": session.mission}


@app.post("/api/settings")
async def api_settings(req: SettingsUpdateRequest) -> dict[str, Any]:
    session.update_settings(req.ollama_url, req.model, req.idle_threshold_sec)
    return {"ok": True, "status": session.get_status_payload()}


@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = session.subscribe_pty()

    # Replay existing terminal buffer so reload/connect restores full TUI screen
    replay = session.pty.get_replay()
    if replay:
        await websocket.send_json({"type": "output", "data": replay})

    async def writer_task() -> None:
        while True:
            chunk = await queue.get()
            await websocket.send_json({"type": "output", "data": chunk})

    writer = asyncio.create_task(writer_task())
    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "input":
                data = str(msg.get("data", ""))
                if data:
                    session.handle_user_terminal_input(data)
            elif mtype == "resize":
                cols = int(msg.get("cols", 120))
                rows = int(msg.get("rows", 34))
                session.pty.resize(cols, rows)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        writer.cancel()
        session.unsubscribe_pty(queue)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = session.subscribe_events()
    await websocket.send_json({"type": "init", "state": session.get_full_state()})
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        session.unsubscribe_events(queue)
