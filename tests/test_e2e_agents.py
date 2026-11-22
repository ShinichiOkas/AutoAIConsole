"""Real E2E tests: AutoAIConsole drives actual CLI Coding Agents (claude, agy) + Ollama."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from autoaiconsole import DEFAULT_OLLAMA_URL
from autoaiconsole.server import app, session


def _ollama_available() -> bool:
    try:
        r = httpx.get(f"{DEFAULT_OLLAMA_URL.rstrip('/')}/api/tags", timeout=2.5)
        return r.status_code == 200
    except Exception:
        return False


class CodingAgentsE2ETest(unittest.TestCase):
    def _run_agent_e2e(self, preset: str, ready_markers: tuple[str, ...]) -> None:
        if not shutil.which(preset):
            self.skipTest(f"{preset} CLI is not installed on PATH")
        if not _ollama_available():
            self.skipTest("Ollama server is not reachable")

        with tempfile.TemporaryDirectory(prefix=f"autoai_e2e_{preset}_") as tmpdir:
            work = Path(tmpdir)
            # Isolate global session timeline/counters between test runs
            session.chat_timeline.clear()
            session.auto_reply_count = 0
            session.pending_boundary = None
            with TestClient(app) as client:
                health = client.get("/api/health").json()
                self.assertTrue(health["ok"])

                # 1. Start real CLI agent inside ConPTY
                start_res = client.post(
                    "/api/terminal/start",
                    json={"agent_preset": preset, "cwd": str(work)},
                )
                self.assertEqual(start_res.status_code, 200)
                start_body = start_res.json()
                self.assertTrue(start_body["ok"], start_body)

                try:
                    # 2. Wait for autonomous monitor to clear trust prompt and reach main prompt
                    ready = False
                    deadline = time.monotonic() + 45.0
                    while time.monotonic() < deadline:
                        time.sleep(1.0)
                        scr = client.get("/api/screen").json()
                        self.assertTrue(
                            scr["pty_running"],
                            f"{preset} exited prematurely: {scr['screen_text']}",
                        )
                        text = scr["screen_text"]
                        if "Yes, I trust this folder" not in text and any(
                            m in text for m in ready_markers
                        ):
                            ready = True
                            break
                    self.assertTrue(ready, f"{preset} did not reach main prompt in time")

                    # 3. Send user instruction via AutoAIConsole chat API
                    timeline_before = len(client.get("/api/state").json()["timeline"])
                    chat_res = client.post(
                        "/api/chat",
                        json={
                            "message": (
                                "Pythonで簡単なCLI電卓アプリ calc.py（add, subtract, multiply, divide と "
                                "コマンドライン実行）と、unittest のテスト test_calc.py を作成して、"
                                "テストが通るところまで完了させてください。"
                            )
                        },
                    )
                    self.assertEqual(chat_res.status_code, 200)
                    chat_body = chat_res.json()
                    self.assertTrue(chat_body["ok"], chat_body)
                    self.assertTrue(
                        chat_body.get("sent_to_terminal"),
                        f"Expected prompt sent to terminal: {chat_body}",
                    )

                    # 4. Wait for CLI agent + autonomous monitor to finish app & tests
                    goal_done = False
                    deadline = time.monotonic() + 180.0
                    while time.monotonic() < deadline:
                        time.sleep(2.0)
                        st = client.get("/api/state").json()
                        self.assertTrue(
                            st["status"]["pty_running"],
                            f"{preset} stopped unexpectedly",
                        )
                        new_items = st["timeline"][timeline_before:]
                        if any(
                            item.get("kind") == "ai" and "【完了検知】" in item.get("text", "")
                            for item in new_items
                        ):
                            goal_done = True
                            break

                    self.assertTrue(
                        goal_done,
                        f"AutoAIConsole did not detect goal_done for {preset} within 180s",
                    )

                    # 5. Verify generated app and run its unittest suite directly
                    calc_py = work / "calc.py"
                    test_calc_py = work / "test_calc.py"
                    self.assertTrue(calc_py.is_file(), f"calc.py missing in {list(work.iterdir())}")
                    self.assertTrue(test_calc_py.is_file(), f"test_calc.py missing in {list(work.iterdir())}")

                    proc = subprocess.run(
                        [sys.executable, "-m", "unittest", "discover", "-s", str(work), "-v"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(
                        proc.returncode,
                        0,
                        f"Generated app tests failed ({preset}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                    )
                finally:
                    client.post("/api/terminal/stop")

    def test_claude_builds_and_tests_cli_app_end_to_end(self) -> None:
        self._run_agent_e2e("claude", ("for shortcuts", "Claude Code", "Welcome"))

    def test_agy_builds_and_tests_cli_app_end_to_end(self) -> None:
        self._run_agent_e2e("agy", ("for shortcuts", "Antigravity CLI", "Auto mode"))
