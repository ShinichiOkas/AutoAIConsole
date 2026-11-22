"""Live session coordinator: PTY, Ollama AI, autonomous monitor loop, and WS fan-out."""
from __future__ import annotations

import asyncio
import datetime
import re
import time
import uuid
from pathlib import Path
from typing import Any

from autoaiconsole import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from autoaiconsole.ollama_client import chat_with_user, evaluate_terminal_screen
from autoaiconsole.pty_manager import PtyManager, detect_agent_presets

PROMPT_HINTS = re.compile(
    r"(\(y/n\)|\[y/n\]|\[Y/n\]|\[y/N\]|Allow\b|Approve\b|Continue\?|Proceed\?|"
    r"Press Enter|confirm|y/n\?|Do you want to|Select an option|❯\s*$|^\s*>\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

BUSY_HINTS = re.compile(
    r"(Loading\.\.\.|Thinking\.\.\.|Generating|Compiling|Running\b|[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏])",
    re.IGNORECASE,
)

# Automatic terminal query responses sent by xterm.js (CPR, DA1/DA2, OSC color, focus)
# Must still be forwarded to ConPTY, but must NOT trigger "user keyboard intervention".
TERMINAL_PROTOCOL_REPLY = re.compile(
    r"^(?:\x1b\[\d+;\d+R|\x1b\[\?[0-9;]+c|\x1b\[>[0-9;]+c|\x1b\]1[012];[^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[IO])+$"
)


def _now_hms() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


class ConsoleSession:
    """Single active AutoAIConsole session binding ConPTY + Ollama + Monitor."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop
        self.mode: str = "autonomous"         # autonomous | paused | manual
        self.engine_state: str = "idle"       # idle | watching | thinking | boundary | user | paused | manual
        self.model: str = DEFAULT_OLLAMA_MODEL
        self.ollama_url: str = DEFAULT_OLLAMA_URL
        self.idle_threshold_sec: float = 2.5

        self.mission: dict[str, Any] = {
            "goal": "右ターミナルの CLI Coding Agent を操作し、ユーザーの指示した開発タスクを完了させる",
            "policy": "通常の確認 (Y/n)・ファイル編集許可・テスト実行は自律回答して前へ進める",
            "boundaries": ["force push", "ファイル大量削除", "本番DB変更", "仕様の大幅分岐"],
        }

        self.auto_reply_count: int = 0
        self.last_trigger: str = "(待機中)"
        self.last_eval_note: str = "ターミナルを起動すると自律監視が始まります"
        self.confidence: int = 0
        self.last_activity_at: float = time.time()

        self._last_evaluated_hash: str = ""
        self._last_auto_sent_at: float = 0.0
        self._eval_lock = asyncio.Lock()

        self.chat_timeline: list[dict[str, Any]] = [
            {
                "id": uuid.uuid4().hex[:8],
                "kind": "ai",
                "time": _now_hms(),
                "model": self.model,
                "text": (
                    "AutoAIConsole を起動しました。\n"
                    "右上で CLI Coding Agent（Claude Code / Gemini CLI / agy / PowerShell 等）を起動し、"
                    "達成したい目的をこのチャットに伝えてください。\n"
                    "ターミナルを常時監視し、Agent の問いかけに自律的に回答して進めます。"
                ),
            }
        ]
        self.pending_boundary: dict[str, Any] | None = None

        self._pty_subscribers: set[asyncio.Queue[str]] = set()
        self._event_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._monitor_task: asyncio.Task[None] | None = None

        self.pty = PtyManager(
            on_output=self._on_pty_output_threadsafe,
            on_exit=self._on_pty_exit_threadsafe,
        )
        self.pty.cwd = str(Path.cwd())

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = loop.create_task(self._monitor_loop())

    def shutdown(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self.pty.stop()

    # ---------- Subscribers & Broadcast ----------
    def subscribe_pty(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._pty_subscribers.add(q)
        return q

    def unsubscribe_pty(self, q: asyncio.Queue[str]) -> None:
        self._pty_subscribers.discard(q)

    def subscribe_events(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=300)
        self._event_subscribers.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._event_subscribers.discard(q)

    def _broadcast_event(self, payload: dict[str, Any]) -> None:
        for q in list(self._event_subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _on_pty_output_threadsafe(self, chunk: str) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._handle_pty_output, chunk)

    def _handle_pty_output(self, chunk: str) -> None:
        self.last_activity_at = time.time()
        for q in list(self._pty_subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def _on_pty_exit_threadsafe(self, exit_code: int | None) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._handle_pty_exit, exit_code)

    def _handle_pty_exit(self, exit_code: int | None) -> None:
        self.engine_state = "idle"
        self.last_eval_note = f"子プロセスが終了しました (exit_code={exit_code})"
        self._append_timeline(
            {
                "id": uuid.uuid4().hex[:8],
                "kind": "system",
                "time": _now_hms(),
                "text": f"ターミナルの子プロセスが終了しました (exit_code={exit_code})。必要であれば右上の「起動 / 再起動」から再開できます。",
            }
        )
        self._broadcast_state()

    def _append_timeline(self, item: dict[str, Any]) -> None:
        self.chat_timeline.append(item)
        if len(self.chat_timeline) > 200:
            self.chat_timeline = self.chat_timeline[-200:]
        self._broadcast_event({"type": "timeline_item", "item": item})

    def _broadcast_state(self) -> None:
        self._broadcast_event({"type": "status", "status": self.get_status_payload()})

    def get_status_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "engine_state": self.engine_state,
            "model": self.model,
            "ollama_url": self.ollama_url,
            "idle_threshold_sec": self.idle_threshold_sec,
            "pty_running": self.pty.is_running,
            "pty_command": self.pty.command_display,
            "pty_cwd": self.pty.cwd,
            "auto_reply_count": self.auto_reply_count,
            "silence_sec": round(self.pty.silence_seconds(), 1),
            "confidence": self.confidence,
            "last_trigger": self.last_trigger,
            "last_eval_note": self.last_eval_note,
            "elapsed_sec": max(0, int(time.time() - self.last_activity_at)),
            "has_pending_boundary": self.pending_boundary is not None,
        }

    def get_full_state(self) -> dict[str, Any]:
        return {
            "status": self.get_status_payload(),
            "mission": self.mission,
            "timeline": self.chat_timeline,
            "presets": detect_agent_presets(),
        }

    # ---------- Terminal Control ----------
    def start_terminal(self, preset: str, cwd: str, custom_command: str | None = None) -> tuple[bool, str]:
        ok, msg = self.pty.start(preset=preset, cwd=cwd, custom_command=custom_command)
        self._last_evaluated_hash = ""
        self.pending_boundary = None
        if ok:
            self.last_activity_at = time.time()
            self.engine_state = "watching" if self.mode == "autonomous" else self.mode
            self.last_eval_note = f"{self.pty.command_display} を監視中"
            self.last_trigger = f"起動: {self.pty.command_display}"
            self._broadcast_event({"type": "terminal_reset"})
            self._append_timeline(
                {
                    "id": uuid.uuid4().hex[:8],
                    "kind": "system",
                    "time": _now_hms(),
                    "text": f"ターミナルを起動しました: {self.pty.command_display} ({self.pty.cwd})",
                }
            )
            self._broadcast_state()
        return ok, msg

    def stop_terminal(self) -> None:
        self.pty.stop()
        self.engine_state = "idle"
        self.pending_boundary = None
        self.last_eval_note = "ターミナル停止済み"
        self._broadcast_state()

    def handle_user_terminal_input(self, data: str) -> bool:
        """Called when xterm.js emits input; ignores protocol replies for intervention detection."""
        if not self.pty.is_running:
            return False
        ok = self.pty.write(data)
        if ok and not TERMINAL_PROTOCOL_REPLY.match(data):
            self.last_activity_at = time.time()
            if self.mode == "autonomous" and self.engine_state in ("watching", "thinking"):
                self.engine_state = "user"
                self.last_eval_note = "ユーザー直接入力を優先中（AutoAI待機）"
                self._broadcast_state()
        return ok

    def send_interrupt(self) -> bool:
        ok = self.pty.send_interrupt()
        if ok:
            self.last_activity_at = time.time()
            if self.mode == "autonomous":
                self.engine_state = "user"
                self.last_eval_note = "Ctrl+C を送信しました（AutoAI待機）"
                self._broadcast_state()
        return ok

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if not self.pty.is_running:
            self.engine_state = "idle" if mode == "autonomous" else mode
        elif mode == "autonomous":
            self.engine_state = "boundary" if self.pending_boundary else "watching"
            self.last_eval_note = "自律監視を再開しました"
        else:
            self.engine_state = mode
            self.last_eval_note = "一時停止中" if mode == "paused" else "手動モード（自動回答OFF）"
        self._broadcast_state()

    def resume_autonomous(self) -> None:
        self.mode = "autonomous"
        self.engine_state = "watching" if self.pty.is_running else "idle"
        self._last_evaluated_hash = ""
        self.last_eval_note = "自律監視を再開しました"
        self._broadcast_state()

    def update_mission(self, goal: str, policy: str, boundaries: list[str]) -> None:
        self.mission = {
            "goal": goal.strip() or self.mission["goal"],
            "policy": policy.strip() or self.mission["policy"],
            "boundaries": [b.strip() for b in boundaries if b.strip()],
        }
        self._broadcast_event({"type": "mission", "mission": self.mission})

    def update_settings(self, ollama_url: str, model: str, idle_threshold_sec: float) -> None:
        if ollama_url.strip():
            self.ollama_url = ollama_url.strip()
        if model.strip():
            self.model = model.strip()
        self.idle_threshold_sec = max(0.5, min(15.0, float(idle_threshold_sec)))
        self._broadcast_state()

    # ---------- User Chat ----------
    async def handle_user_chat(self, message: str) -> dict[str, Any]:
        text = message.strip()
        if not text:
            return {"ok": False, "error": "空メッセージ"}

        self.last_activity_at = time.time()
        self._append_timeline(
            {
                "id": uuid.uuid4().hex[:8],
                "kind": "user",
                "time": _now_hms(),
                "text": text,
            }
        )

        # Quick resume intent
        if self.engine_state == "user" and any(k in text for k in ("続けて", "再開", "任せる", "お願い")):
            self.resume_autonomous()

        prev_state = self.engine_state
        self.engine_state = "thinking"
        self.last_eval_note = f"チャット応答を生成中 ({self.model})..."
        self._broadcast_state()

        screen = self.pty.get_screen_text() if self.pty.is_running else ""
        try:
            res = await chat_with_user(
                ollama_url=self.ollama_url,
                model=self.model,
                mission=self.mission,
                chat_history=self.chat_timeline,
                screen_snapshot=screen,
                pty_running=self.pty.is_running,
                user_message=text,
            )
        except Exception as exc:
            self.engine_state = prev_state
            self.last_eval_note = f"Ollama 通信エラー: {exc}"
            self._append_timeline(
                {
                    "id": uuid.uuid4().hex[:8],
                    "kind": "error",
                    "time": _now_hms(),
                    "text": f"Ollama ({self.model}) への接続に失敗しました: {exc}",
                }
            )
            self._broadcast_state()
            return {"ok": False, "error": str(exc)}

        if res.get("update_goal"):
            self.mission["goal"] = str(res["update_goal"]).strip()
            self._broadcast_event({"type": "mission", "mission": self.mission})

        self._append_timeline(
            {
                "id": uuid.uuid4().hex[:8],
                "kind": "ai",
                "time": _now_hms(),
                "model": self.model,
                "text": res["reply"],
            }
        )

        sent = res.get("send_to_terminal")
        if sent and self.pty.is_running:
            await self.pty.send_text_async(str(sent), press_enter=bool(res.get("press_enter", True)))
            self._last_auto_sent_at = time.monotonic()
            self._last_evaluated_hash = ""
            self.last_activity_at = time.time()
            self._append_timeline(
                {
                    "id": uuid.uuid4().hex[:8],
                    "kind": "auto_action",
                    "time": _now_hms(),
                    "prompt_text": "(ユーザーチャット指示からの投入)",
                    "reasoning": "ユーザーの依頼を右ターミナルの Coding Agent へ直接投入しました。",
                    "sent_text": str(sent),
                    "confidence": 100,
                }
            )

        if self.pty.is_running and self.mode == "autonomous":
            self.engine_state = "boundary" if self.pending_boundary else "watching"
        elif not self.pty.is_running:
            self.engine_state = "idle"
        else:
            self.engine_state = self.mode
        self.last_eval_note = "出力ストリーム監視中" if self.engine_state == "watching" else self.last_eval_note
        self._broadcast_state()
        return {"ok": True, "reply": res["reply"], "sent_to_terminal": sent}

    # ---------- Boundary Resolution ----------
    async def resolve_boundary(self, action: str, custom_text: str = "") -> dict[str, Any]:
        boundary = self.pending_boundary
        if not boundary:
            return {"ok": False, "error": "保留中の境界相談はありません"}

        card_id = boundary["id"]
        if action == "abort":
            self.pty.send_interrupt()
            self.pending_boundary = None
            self.mode = "paused"
            self.engine_state = "paused"
            resolved_label = "Ctrl+C で操作を中断・一時停止"
        elif action == "allow":
            await self.pty.send_text_async("y", press_enter=True)
            self.pending_boundary = None
            self.engine_state = "watching"
            self._last_auto_sent_at = time.monotonic()
            self._last_evaluated_hash = ""
            resolved_label = "明示許可 (y) を送信して再開"
        elif action == "custom":
            await self.pty.send_text_async(custom_text, press_enter=True)
            self.pending_boundary = None
            self.engine_state = "watching"
            self._last_auto_sent_at = time.monotonic()
            self._last_evaluated_hash = ""
            resolved_label = f"カスタム回答を送信 ({custom_text})"
        else:
            rec = boundary.get("recommended_input") or "n"
            await self.pty.send_text_async(rec, press_enter=True)
            self.pending_boundary = None
            self.engine_state = "watching"
            self.auto_reply_count += 1
            self._last_auto_sent_at = time.monotonic()
            self._last_evaluated_hash = ""
            resolved_label = f"推奨案を送信 ({rec})"

        # Update stored timeline card
        for item in self.chat_timeline:
            if item.get("id") == card_id:
                item["resolved"] = resolved_label
                break

        self._broadcast_event({"type": "boundary_resolved", "id": card_id, "label": resolved_label})
        self._broadcast_state()
        return {"ok": True, "resolved": resolved_label}

    # ---------- Autonomous Monitor Loop ----------
    def _estimate_prompt_confidence(self, screen_text: str, silence: float) -> tuple[int, str]:
        if not screen_text.strip():
            return 0, "(出力なし)"
        lines = [ln.strip() for ln in screen_text.splitlines() if ln.strip()]
        # Skip pure box-drawing / separator lines so last_trigger shows meaningful content
        meaningful = [ln for ln in lines if not re.fullmatch(r"[─━═\-_=·.\s]+", ln)]
        last_line = meaningful[-1] if meaningful else (lines[-1] if lines else "")
        tail = "\n".join(lines[-8:])

        if BUSY_HINTS.search(tail):
            return 12, last_line[:90]

        score = int(min(55, (silence / max(0.5, self.idle_threshold_sec)) * 45))
        m = PROMPT_HINTS.search(tail)
        if m:
            score = min(98, score + 48)
            return score, last_line[:90]
        return score, last_line[:90]

    async def evaluate_once(self, force: bool = False) -> dict[str, Any]:
        if not self.pty.is_running:
            return {"ok": False, "decision": "idle", "reason": "ターミナル未起動"}

        if self._eval_lock.locked():
            return {"ok": False, "decision": "busy", "reason": "判定処理中"}

        async with self._eval_lock:
            screen_text = self.pty.get_screen_text()
            if not screen_text.strip():
                return {"ok": True, "decision": "wait", "reason": "画面が空のため待機"}

            screen_hash = self.pty.get_screen_hash()
            if not force and screen_hash == self._last_evaluated_hash:
                return {"ok": True, "decision": "wait", "reason": "画面変化なし"}

            prev_state = self.engine_state
            self.engine_state = "thinking"
            self.last_eval_note = f"Ollama ({self.model}) で画面を解析中..."
            self._broadcast_state()

            recent_actions = [m for m in self.chat_timeline if m.get("kind") == "auto_action"]
            try:
                result = await evaluate_terminal_screen(
                    ollama_url=self.ollama_url,
                    model=self.model,
                    mission=self.mission,
                    chat_history=self.chat_timeline,
                    screen_snapshot=screen_text,
                    recent_actions=recent_actions,
                )
            except Exception as exc:
                self.engine_state = prev_state if prev_state != "thinking" else "watching"
                self.last_eval_note = f"監視評価エラー: {exc}"
                self._broadcast_state()
                return {"ok": False, "decision": "error", "reason": str(exc)}

            # Guard: if user took over while Ollama was thinking, do not override!
            if self.engine_state == "user" or (self.mode != "autonomous" and not force):
                self._broadcast_state()
                return {"ok": True, "decision": "yielded", "reason": "ユーザー介入または手動切替のため送信を見送り"}

            self._last_evaluated_hash = screen_hash
            decision = result["decision"]
            self.confidence = result["confidence"]
            if result["detected_prompt"]:
                self.last_trigger = result["detected_prompt"][:100]

            if decision == "respond":
                sent_text = result["input_text"]
                press_enter = result["press_enter"]
                await self.pty.send_text_async(sent_text, press_enter=press_enter)
                self.auto_reply_count += 1
                self._last_auto_sent_at = time.monotonic()
                self._last_evaluated_hash = ""
                self.last_activity_at = time.time()
                self.engine_state = "watching" if self.mode == "autonomous" else self.mode
                self.last_eval_note = f"自律回答を送信: {sent_text or '<Enter>'}"
                self._append_timeline(
                    {
                        "id": uuid.uuid4().hex[:8],
                        "kind": "auto_action",
                        "time": _now_hms(),
                        "prompt_text": result["detected_prompt"] or "(入力待ち検知)",
                        "reasoning": result["reason"],
                        "sent_text": sent_text if sent_text else "<Enter>",
                        "confidence": result["confidence"],
                    }
                )
            elif decision == "boundary":
                card = {
                    "id": uuid.uuid4().hex[:8],
                    "kind": "boundary",
                    "time": _now_hms(),
                    "title": result["boundary_title"] or "停止境界に該当する操作",
                    "detected_cmd": result["detected_prompt"] or "(ターミナル画面を確認してください)",
                    "risk_reason": result["risk_reason"] or result["reason"],
                    "recommendation": result["recommendation"] or "安全側に倒して拒否 (n) する",
                    "recommended_input": result["recommended_input"] or "n",
                    "resolved": None,
                }
                self.pending_boundary = card
                self.engine_state = "boundary"
                self.last_eval_note = f"境界停止: {card['title']}"
                self._append_timeline(card)
            elif decision == "goal_done":
                self.engine_state = "watching" if self.mode == "autonomous" else self.mode
                self.last_eval_note = "目的完了を検知・次の指示待ち"
                self._append_timeline(
                    {
                        "id": uuid.uuid4().hex[:8],
                        "kind": "ai",
                        "time": _now_hms(),
                        "model": self.model,
                        "text": f"【完了検知】{result['reason'] or 'ミッションの達成を確認しました。次の指示があればどうぞ。'}",
                    }
                )
            else:
                self.engine_state = "watching" if self.mode == "autonomous" else self.mode
                self.last_eval_note = result["reason"] or "処理継続中と判断・待機"

            self._broadcast_state()
            return {"ok": True, **result}

    async def _monitor_loop(self) -> None:
        last_tick_emit = 0.0
        while True:
            try:
                await asyncio.sleep(0.5)
                now = time.monotonic()

                if self.pty.is_running:
                    silence = self.pty.silence_seconds()
                    screen_text = self.pty.get_screen_text()
                    if self.engine_state == "watching":
                        conf, hint = self._estimate_prompt_confidence(screen_text, silence)
                        self.confidence = conf
                        if hint:
                            self.last_trigger = hint
                        run_sec = max(0, int(now - self.pty.started_at)) if self.pty.started_at else 0
                        if conf <= 15 and silence < self.idle_threshold_sec:
                            self.last_eval_note = f"{self.pty.command_display} 作業中 (稼働 {run_sec}s / 無音 {silence:.1f}s)"
                        elif silence < self.idle_threshold_sec:
                            self.last_eval_note = f"出力ストリーム監視中 (無音 {silence:.1f}s / 閾値 {self.idle_threshold_sec:.1f}s)"

                    # Live feedback tick every 1 second
                    if now - last_tick_emit >= 1.0:
                        last_tick_emit = now
                        self._broadcast_state()

                    # Autonomous trigger check
                    if (
                        self.mode == "autonomous"
                        and self.engine_state == "watching"
                        and not self._eval_lock.locked()
                        and silence >= self.idle_threshold_sec
                        and (now - self._last_auto_sent_at) >= 2.0
                        and screen_text.strip()
                    ):
                        screen_hash = self.pty.get_screen_hash()
                        if screen_hash != self._last_evaluated_hash:
                            # Skip busy spinner screens unless idle for a long time
                            if self.confidence >= 40 or silence >= (self.idle_threshold_sec + 2.5):
                                await self.evaluate_once(force=False)
                else:
                    if now - last_tick_emit >= 1.0:
                        last_tick_emit = now
                        self._broadcast_state()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_eval_note = f"監視ループ警告: {exc}"
                await asyncio.sleep(1.0)
