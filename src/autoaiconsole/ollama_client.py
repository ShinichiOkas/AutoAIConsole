"""Async Ollama client for user chat and autonomous terminal screen evaluation."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from autoaiconsole import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


async def fetch_ollama_models(ollama_url: str = DEFAULT_OLLAMA_URL) -> dict[str, Any]:
    fallback = [DEFAULT_OLLAMA_MODEL, "gemma4:12b", "qwen3.5:9b"]
    base = ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
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
            "note": f"Ollama 接続エラー ({type(exc).__name__})",
        }


async def chat_with_user(
    *,
    ollama_url: str,
    model: str,
    mission: dict[str, Any],
    chat_history: list[dict[str, Any]],
    screen_snapshot: str,
    pty_running: bool,
    user_message: str,
) -> dict[str, Any]:
    """Respond to user chat and optionally emit a terminal command/prompt to send."""
    system_prompt = f"""あなたは AutoAIConsole の自律的ピア AI（ユーザーの相棒）です。
右側のターミナルで動作している CLI 型 Coding Agent（Claude Code / Gemini CLI / agy / PowerShell 等）をユーザーの代わりに操作・監視し、目的を達成します。

## 現在のミッション
- 目的 (Goal): {mission.get("goal", "")}
- 自律方針 (Policy): {mission.get("policy", "")}
- 停止境界 (Boundaries): {", ".join(mission.get("boundaries", []))}

## 現在のターミナル状態
- プロセス稼働中: {"はい" if pty_running else "いいえ（未起動）"}
- 現在のターミナル画面スナップショット:
```
{screen_snapshot[-4000:] if screen_snapshot else "(出力なし)"}
```

## 出力形式（必ず JSON オブジェクトのみ返す）
{{
  "reply": "ユーザーへの簡潔で率直な日本語の返答",
  "send_to_terminal": null または "右ターミナルの CLI Agent へ今すぐ送信すべきプロンプトやコマンド文字列",
  "press_enter": true,
  "update_goal": null または "ユーザーが新しい目的・タスクを指示した場合の更新後 Goal 文字列"
}}

ルール:
- ユーザーが作業依頼・追加指示・コマンド実行を頼んでいてターミナル稼働中なら、send_to_terminal に CLI Coding Agent へ送る具体的指示を入れ、reply に「送信しました」等と簡潔に伝える。
- ユーザーが質問・進捗確認・相談だけをしている場合は send_to_terminal を null にする。
- ターミナル未起動のときに作業依頼されたら、send_to_terminal は null にし、「まず右上で Coding Agent を起動してください」と案内する。
- へりくだらず、相棒として簡潔に日本語で答える。"""

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in chat_history[-12:]:
        kind = item.get("kind")
        if kind == "user":
            messages.append({"role": "user", "content": str(item.get("text", ""))})
        elif kind == "ai":
            messages.append({"role": "assistant", "content": str(item.get("text", ""))})
        elif kind == "auto_action":
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[自律操作ログ] 検知: {item.get('prompt_text')} -> 送信: {item.get('sent_text')} (理由: {item.get('reasoning')})",
                }
            )
    messages.append({"role": "user", "content": user_message})

    base = ollama_url.rstrip("/")
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            },
        )
        res.raise_for_status()
        data = res.json()
        raw_content = data.get("message", {}).get("content", "")

    parsed = _extract_json(raw_content)
    if not parsed:
        return {
            "reply": raw_content.strip() or "(Ollama から空応答が返りました)",
            "send_to_terminal": None,
            "press_enter": True,
            "update_goal": None,
        }
    return {
        "reply": str(parsed.get("reply") or "").strip() or "(応答なし)",
        "send_to_terminal": parsed.get("send_to_terminal") or None,
        "press_enter": bool(parsed.get("press_enter", True)),
        "update_goal": parsed.get("update_goal") or None,
    }


async def evaluate_terminal_screen(
    *,
    ollama_url: str,
    model: str,
    mission: dict[str, Any],
    chat_history: list[dict[str, Any]],
    screen_snapshot: str,
    recent_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Inspect rendered terminal screen and decide: wait / respond / boundary / goal_done."""
    recent_summary = "\n".join(
        f"- 検知={a.get('prompt_text')!r} -> 送信={a.get('sent_text')!r}"
        for a in recent_actions[-5:]
    ) or "(まだなし)"

    recent_chat = "\n".join(
        f"- {m.get('kind')}: {str(m.get('text', ''))[:160]}"
        for m in chat_history[-6:]
        if m.get("kind") in ("user", "ai")
    ) or "(なし)"

    system_prompt = f"""あなたは AutoAIConsole の自律ターミナル監視エンジンです。
右側のターミナルで動作している CLI Coding Agent の画面を読み取り、ユーザーに代わって自律的に応答すべきかを判定します。

## ミッション
- 目的 (Goal): {mission.get("goal", "")}
- 自律方針 (Policy): {mission.get("policy", "")}
- 停止境界 (Boundaries): {", ".join(mission.get("boundaries", []))}

## 直近のチャット文脈
{recent_chat}

## 直近の自律送信履歴（同じ回答を無限連打しないこと）
{recent_summary}

## 判定ルール（自律ファースト・承認過多にしない）
1. **wait**:
   - Agent が思考中・読み込み中・コマンド実行中（例: `Loading...`, `Thinking`, スピナー `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`, `Running`, 出力途中）。
   - まだ何もタスクを投入していない初期プロンプトや、判断材料が無い場合。
2. **respond**:
   - Agent が通常の確認・許可・選択・質問で入力待ちになっている（例: `(Y/n)`, `[y/N]`, `Allow ...?`, `Approve`, `Continue?`, 選択肢 `1/2/3`, 実装方針の軽い確認、エラー修正の継続確認）。
   - ミッションの目的に照らして、最も自然で前進する回答を `input_text` に指定する。
   - TUI で選択肢をそのまま確定するだけなら `input_text: ""`, `press_enter: true` または `"y"` 等、画面のプロンプト形式に正確に合わせる。
3. **boundary**:
   - 停止境界に該当する不可逆・破壊的操作（`git push --force`, 大量削除 `rm -rf`, 本番DB/外部デプロイ変更, 機密送信）や、ユーザー固有の価値判断が必要な大幅分岐を Agent が提案している場合のみ。
   - この場合は絶対に自動送信せず、ユーザーへ相談する情報（`boundary_title`, `risk_reason`, `recommendation`, `recommended_input`）を返す。
4. **goal_done**:
   - ミッションの目的がすでに完了し、Agent が最終報告を終えて次のユーザー指示待ちプロンプト（`> ` 等）で静止している場合。

## 出力形式（必ず JSON オブジェクトのみ返す）
{{
  "decision": "wait" | "respond" | "boundary" | "goal_done",
  "confidence": 0から100の整数,
  "detected_prompt": "画面上で見えた問いかけ・状態の一行抜粋",
  "reason": "この判定を下した簡潔な日本語の理由",
  "input_text": "respond 時に PTY へ送る文字列（不要なら空文字）",
  "press_enter": true,
  "boundary_title": "boundary 時の短い見出し（それ以外は空文字）",
  "risk_reason": "boundary 時のリスク説明（それ以外は空文字）",
  "recommendation": "boundary 時の推奨案（それ以外は空文字）",
  "recommended_input": "boundary 時に推奨案を採用した場合に送る文字列（それ以外は空文字）"
}}"""

    user_prompt = f"以下が現在のターミナル画面全体です。判定 JSON を返してください。\n\n```\n{screen_snapshot[-5500:]}\n```"

    base = ollama_url.rstrip("/")
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            },
        )
        res.raise_for_status()
        data = res.json()
        raw_content = data.get("message", {}).get("content", "")

    parsed = _extract_json(raw_content)
    if not parsed:
        return {
            "decision": "wait",
            "confidence": 0,
            "detected_prompt": "",
            "reason": "モデル出力の JSON 解析に失敗したため安全に待機します。",
            "input_text": "",
            "press_enter": True,
            "boundary_title": "",
            "risk_reason": "",
            "recommendation": "",
            "recommended_input": "",
        }

    decision = str(parsed.get("decision", "wait")).strip().lower()
    if decision not in ("wait", "respond", "boundary", "goal_done"):
        decision = "wait"
    try:
        confidence = int(parsed.get("confidence", 50))
    except (TypeError, ValueError):
        confidence = 50

    return {
        "decision": decision,
        "confidence": max(0, min(100, confidence)),
        "detected_prompt": str(parsed.get("detected_prompt") or "").strip(),
        "reason": str(parsed.get("reason") or "").strip(),
        "input_text": str(parsed.get("input_text") or ""),
        "press_enter": bool(parsed.get("press_enter", True)),
        "boundary_title": str(parsed.get("boundary_title") or "").strip(),
        "risk_reason": str(parsed.get("risk_reason") or "").strip(),
        "recommendation": str(parsed.get("recommendation") or "").strip(),
        "recommended_input": str(parsed.get("recommended_input") or ""),
    }
