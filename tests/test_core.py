"""Core integration tests for AutoAIConsole ConPTY + pyte + FastAPI server."""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from autoaiconsole.pty_manager import PtyManager
from autoaiconsole.server import app
from autoaiconsole.session import TERMINAL_PROTOCOL_REPLY


def test_terminal_protocol_reply_filter() -> None:
    assert TERMINAL_PROTOCOL_REPLY.match("\x1b[1;1R")
    assert TERMINAL_PROTOCOL_REPLY.match("\x1b[?1;2c")
    assert TERMINAL_PROTOCOL_REPLY.match("\x1b[I")
    assert not TERMINAL_PROTOCOL_REPLY.match("y")
    assert not TERMINAL_PROTOCOL_REPLY.match("\r")
    assert not TERMINAL_PROTOCOL_REPLY.match("git status\r")


def test_pty_manager_real_conpty_roundtrip() -> None:
    chunks: list[str] = []
    pty = PtyManager(on_output=chunks.append, cols=100, rows=24)
    try:
        py = sys.executable
        cmd = f'& "{py}" -c "import sys; print(\'\\x1b[32mREADY_PROMPT\\x1b[0m\'); sys.stdout.flush(); ans = input(\'? Continue? (Y/n) \'); print(\'GOT:\' + ans)"'
        ok, msg = pty.start(preset="custom", cwd=str(Path.cwd()), custom_command=cmd)
        assert ok, msg

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if "Continue? (Y/n)" in pty.get_screen_text():
                break
            time.sleep(0.15)
        assert "Continue? (Y/n)" in pty.get_screen_text(), f"screen={pty.get_screen_text()!r}"

        assert pty.write("y\r")
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if "GOT:y" in pty.get_screen_text():
                break
            time.sleep(0.15)
        assert "GOT:y" in pty.get_screen_text(), f"screen={pty.get_screen_text()!r}"
    finally:
        pty.stop()


def test_server_health_and_state() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["mode"] == "production"
        assert body["default_model"] == "gemma4:31b-cloud"

        st = client.get("/api/state")
        assert st.status_code == 200
        data = st.json()
        assert "mission" in data
        assert "presets" in data
        assert len(data["presets"]) >= 5


class CoreUnitTests(unittest.TestCase):
    def test_protocol_reply_filter(self) -> None:
        test_terminal_protocol_reply_filter()

    def test_real_conpty_roundtrip(self) -> None:
        test_pty_manager_real_conpty_roundtrip()

    def test_server_health(self) -> None:
        test_server_health_and_state()

    def test_send_text_async_key_tokens(self) -> None:
        written: list[str] = []

        class DummyPty(PtyManager):
            @property
            def is_running(self) -> bool:
                return True

            def write(self, data: str) -> bool:
                written.append(data)
                return True

        pty = DummyPty()
        ok = asyncio.run(pty.send_text_async("<down><enter>", press_enter=True))
        self.assertTrue(ok)
        self.assertEqual(written, ["\x1b[B", "\r"])

        written.clear()
        ok = asyncio.run(pty.send_text_async("hello", press_enter=True))
        self.assertTrue(ok)
        self.assertEqual(written, ["hello", "\r"])

        written.clear()
        ok = asyncio.run(pty.send_text_async("<down>", press_enter=False))
        self.assertTrue(ok)
        self.assertEqual(written, ["\x1b[B"])

    def test_estimate_prompt_confidence_skips_separators(self) -> None:
        from autoaiconsole.session import ConsoleSession

        s = ConsoleSession()
        screen = "Working on task...\n─────────────\n"
        conf, trigger = s._estimate_prompt_confidence(screen, silence=1.0)
        self.assertEqual(trigger, "Working on task...")
        self.assertLess(conf, 60)

        busy_screen = "⠋ Compiling assets...\n─────────────\n"
        conf_busy, trigger_busy = s._estimate_prompt_confidence(busy_screen, silence=1.0)
        self.assertEqual(conf_busy, 12)
        self.assertIn("Compiling", trigger_busy)
