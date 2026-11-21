"""Windows ConPTY child terminal manager with pyte virtual screen tracking."""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import pyte

if os.name == "nt":
    from winpty import PtyProcess  # type: ignore
else:
    PtyProcess = None  # pragma: no cover


def detect_agent_presets() -> list[dict[str, str | bool]]:
    """Return CLI Coding Agent presets with local PATH availability."""
    specs = [
        ("claude", "Claude Code (claude)", "claude"),
        ("gemini", "Gemini CLI (gemini)", "gemini"),
        ("agy", "Antigravity CLI (agy)", "agy"),
        ("codex", "Codex CLI (codex)", "codex"),
        ("opencode", "opencode", "opencode"),
        ("pwsh", "PowerShell (pwsh)", "pwsh"),
        ("custom", "カスタムコマンド", ""),
    ]
    out: list[dict[str, str | bool]] = []
    for preset_id, label, binary in specs:
        found = bool(shutil.which(binary)) if binary else True
        out.append({"id": preset_id, "label": label, "command": binary, "available": found})
    return out


def _resolve_spawn_argv(preset: str, custom_command: str | None) -> tuple[list[str], str]:
    """Build argv suitable for Windows ConPTY (handles .exe, .ps1, .cmd, and args)."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"

    if preset == "pwsh":
        return [pwsh, "-NoLogo"], "pwsh"

    raw_cmd = (custom_command or "").strip() if preset == "custom" else preset
    if not raw_cmd:
        return [pwsh, "-NoLogo"], "pwsh"

    # If simple single token and resolves directly to an .exe, launch directly for cleanest TUI.
    if " " not in raw_cmd and '"' not in raw_cmd:
        resolved = shutil.which(raw_cmd)
        if resolved and resolved.lower().endswith(".exe"):
            return [resolved], raw_cmd

    # Otherwise launch via PowerShell so .ps1, .cmd, aliases, and CLI arguments work cleanly.
    return [pwsh, "-NoLogo", "-NoExit", "-Command", raw_cmd], raw_cmd


class PtyManager:
    """Runs a real ConPTY child process and mirrors output to xterm.js and pyte.Screen."""

    def __init__(
        self,
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        cols: int = 120,
        rows: int = 34,
    ) -> None:
        self._on_output = on_output
        self._on_exit = on_exit
        self._cols = cols
        self._rows = rows

        self._lock = threading.Lock()
        self._proc: PtyProcess | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._generation = 0

        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.Stream(self._screen)
        self._replay: deque[str] = deque()
        self._replay_len = 0
        self._max_replay_chars = 160_000

        self.command_display = ""
        self.cwd = str(Path.cwd())
        self.last_output_at: float = 0.0
        self.started_at: float = 0.0
        self.exit_code: int | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and self._proc is not None

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    def start(self, preset: str, cwd: str, custom_command: str | None = None) -> tuple[bool, str]:
        """Start (or restart) the child PTY process."""
        self.stop()

        target_dir = Path(cwd).expanduser()
        if not target_dir.is_dir():
            return False, f"作業ディレクトリが存在しません: {cwd}"

        argv, display = _resolve_spawn_argv(preset, custom_command)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["FORCE_COLOR"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = PtyProcess.spawn(
                argv,
                cwd=str(target_dir.resolve()),
                env=env,
                dimensions=(self._rows, self._cols),
            )
        except Exception as exc:
            return False, f"PTY 起動失敗 ({display}): {exc}"

        with self._lock:
            self._generation += 1
            gen = self._generation
            self._proc = proc
            self._running = True
            self.command_display = display
            self.cwd = str(target_dir.resolve())
            self.exit_code = None
            self.started_at = time.monotonic()
            self.last_output_at = self.started_at
            self._screen = pyte.Screen(self._cols, self._rows)
            self._stream = pyte.Stream(self._screen)
            self._replay.clear()
            self._replay_len = 0

        thread = threading.Thread(
            target=self._read_loop,
            args=(proc, gen),
            name=f"pty-reader-{gen}",
            daemon=True,
        )
        self._reader_thread = thread
        thread.start()
        return True, f"起動しました: {display} ({self.cwd})"

    def _read_loop(self, proc: PtyProcess, gen: int) -> None:
        exit_code: int | None = None
        try:
            while True:
                try:
                    chunk = proc.read(4096)
                except EOFError:
                    break
                except OSError:
                    break
                if not chunk:
                    if not proc.isalive():
                        break
                    time.sleep(0.02)
                    continue

                with self._lock:
                    if gen != self._generation:
                        break
                    self.last_output_at = time.monotonic()
                    self._append_replay_locked(chunk)
                    try:
                        self._stream.feed(chunk)
                    except Exception:
                        pass

                if self._on_output:
                    self._on_output(chunk)
        finally:
            try:
                exit_code = proc.exitstatus
            except Exception:
                exit_code = None
            notify = False
            with self._lock:
                if gen == self._generation:
                    self._running = False
                    self.exit_code = exit_code
                    notify = True
            if notify and self._on_exit:
                self._on_exit(exit_code)

    def _append_replay_locked(self, chunk: str) -> None:
        self._replay.append(chunk)
        self._replay_len += len(chunk)
        while self._replay_len > self._max_replay_chars and len(self._replay) > 1:
            removed = self._replay.popleft()
            self._replay_len -= len(removed)

    def get_replay(self) -> str:
        with self._lock:
            return "".join(self._replay)

    def write(self, data: str) -> bool:
        with self._lock:
            proc = self._proc
            if not self._running or proc is None:
                return False
        try:
            proc.write(data)
            return True
        except Exception:
            return False

    async def send_text_async(self, text: str, press_enter: bool = True) -> bool:
        """Write text to PTY; send CR after a brief pause so TUI agents reliably submit."""
        if not self.is_running:
            return False
        # Support special key tokens from LLM decisions
        normalized = text.strip().lower()
        special_map = {
            "<enter>": "\r",
            "enter": "\r",
            "<esc>": "\x1b",
            "<up>": "\x1b[A",
            "<down>": "\x1b[B",
            "<tab>": "\t",
            "<ctrl+c>": "\x03",
            "ctrl+c": "\x03",
        }
        if normalized in special_map:
            return self.write(special_map[normalized])

        if text:
            ok = self.write(text)
            if not ok:
                return False
        if press_enter:
            await asyncio.sleep(0.08)
            return self.write("\r")
        return True

    def send_interrupt(self) -> bool:
        return self.write("\x03")

    def resize(self, cols: int, rows: int) -> None:
        cols = max(20, min(300, int(cols)))
        rows = max(8, min(120, int(rows)))
        with self._lock:
            self._cols = cols
            self._rows = rows
            try:
                self._screen.resize(rows, cols)
            except Exception:
                pass
            proc = self._proc
            running = self._running
        if running and proc is not None:
            try:
                proc.setwinsize(rows, cols)
            except Exception:
                pass

    def get_screen_text(self) -> str:
        """Return the clean rendered terminal screen lines via pyte."""
        with self._lock:
            lines = [line.rstrip() for line in self._screen.display]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def get_screen_hash(self) -> str:
        text = self.get_screen_text()
        return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]

    def silence_seconds(self) -> float:
        with self._lock:
            if not self._running or self.last_output_at <= 0:
                return 0.0
            return max(0.0, time.monotonic() - self.last_output_at)

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._running = False
            self._generation += 1
        if proc is not None:
            try:
                if proc.isalive():
                    proc.terminate(force=True)
            except Exception:
                pass
