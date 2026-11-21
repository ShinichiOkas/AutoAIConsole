"""FastAPI server for AutoAIConsole UX mock and UI-first API skeleton."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from autoaiconsole import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, __version__

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="AutoAIConsole", version=__version__)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class ChatRequest(BaseModel):
    message: str
    model: str = DEFAULT_OLLAMA_MODEL


class TerminalStartRequest(BaseModel):
    agent_preset: str = "claude"
    cwd: str = "."
    command: str | None = None


class TerminalInputRequest(BaseModel):
    text: str
    source: str = Field(default="user", pattern="^(user|autoai)$")


class MonitorModeRequest(BaseModel):
    mode: str = Field(pattern="^(autonomous|paused|manual)$")


class MissionUpdateRequest(BaseModel):
    goal: str
    policy: str
    boundaries: list[str] = Field(default_factory=list)


def _not_implemented(feature: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "implemented": False,
            "status": "not_implemented",
            "feature": feature,
            "message": f"未実装: {detail}",
        },
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "ui_phase": "ux-mock-v1",
        "default_model": DEFAULT_OLLAMA_MODEL,
        "default_ollama_url": DEFAULT_OLLAMA_URL,
    }


@app.get("/api/models")
async def list_models(ollama_url: str = DEFAULT_OLLAMA_URL) -> dict[str, Any]:
    """Fetch installed Ollama models if reachable; always keep default model selectable."""
    fallback = [
        DEFAULT_OLLAMA_MODEL,
        "gemma4:12b",
        "gemma4-31b-coder:tuned",
        "qwen3.5:9b",
        "qwen2.5-coder:7b",
    ]
    base = ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            res = await client.get(f"{base}/api/tags")
            res.raise_for_status()
            payload = res.json()
            names = [m["name"] for m in payload.get("models", []) if m.get("name")]
            if DEFAULT_OLLAMA_MODEL in names:
                names = [DEFAULT_OLLAMA_MODEL] + [n for n in names if n != DEFAULT_OLLAMA_MODEL]
            else:
                names = [DEFAULT_OLLAMA_MODEL] + names
            return {
                "ollama_connected": True,
                "ollama_url": base,
                "default_model": DEFAULT_OLLAMA_MODEL,
                "models": names,
            }
    except Exception as exc:
        return {
            "ollama_connected": False,
            "ollama_url": base,
            "default_model": DEFAULT_OLLAMA_MODEL,
            "models": fallback,
            "note": f"Ollama 未接続のためモック候補を表示 ({type(exc).__name__})",
        }


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> JSONResponse:
    return _not_implemented(
        "ollama_chat",
        f"Ollama ({req.model}) との実チャット接続は未実装です（次フェーズで実装予定）。UX デモモードで動作を確認できます。",
    )


@app.post("/api/terminal/start")
async def api_terminal_start(req: TerminalStartRequest) -> JSONResponse:
    return _not_implemented(
        "pty_start",
        f"子プロセス PTY 起動 ({req.agent_preset} @ {req.cwd}) は未実装です（次フェーズで pywinpty 接続予定）。",
    )


@app.post("/api/terminal/input")
async def api_terminal_input(req: TerminalInputRequest) -> JSONResponse:
    return _not_implemented(
        "pty_input",
        f"実 PTY への入力送信 ({req.source}: {req.text!r}) は未実装です。",
    )


@app.post("/api/terminal/interrupt")
async def api_terminal_interrupt() -> JSONResponse:
    return _not_implemented(
        "pty_interrupt",
        "実 PTY への Ctrl+C 送信は未実装です。",
    )


@app.post("/api/monitor/mode")
async def api_monitor_mode(req: MonitorModeRequest) -> JSONResponse:
    return _not_implemented(
        "monitor_mode",
        f"自律監視エンジン実体のモード切替 ({req.mode}) は未実装です（UI 上のモック状態のみ反映します）。",
    )


@app.post("/api/mission")
async def api_mission(req: MissionUpdateRequest) -> JSONResponse:
    return _not_implemented(
        "mission_store",
        f"ミッションの永続化保存 (goal={req.goal[:24]!r}) は未実装です（UI 上のモック状態のみ反映します）。",
    )
