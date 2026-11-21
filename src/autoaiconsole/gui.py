"""Desktop GUI launcher for AutoAIConsole (pywebview + FastAPI, or browser fallback)."""
from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn

from autoaiconsole.server import app


def _pick_free_port(preferred: int = 18765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])


def _wait_server(host: str, port: int, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoAIConsole Desktop GUI")
    parser.add_argument("--port", type=int, default=0, help="Bind port (0 = auto)")
    parser.add_argument("--browser", action="store_true", help="Open in default web browser instead of pywebview")
    parser.add_argument("--server-only", action="store_true", help="Run HTTP server only without opening a window")
    args = parser.parse_args()

    host = "127.0.0.1"
    port = args.port if args.port > 0 else _pick_free_port()
    url = f"http://{host}:{port}"

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    if args.server_only:
        print(f"AutoAIConsole server listening at {url}", flush=True)
        server.run()
        return 0

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_server(host, port):
        print("[ERROR] Server failed to start in time.")
        return 1

    if args.browser:
        print(f"AutoAIConsole opening in browser: {url}", flush=True)
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    try:
        import webview  # type: ignore
    except ImportError:
        print(f"pywebview not available; opening in browser: {url}", flush=True)
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    webview.create_window(
        title="AutoAIConsole — Autonomous CLI Coding Agent Controller",
        url=url,
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#0b1020",
    )
    webview.start()
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
