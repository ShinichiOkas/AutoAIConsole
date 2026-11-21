"""Check whether AutoAIConsole is currently running from the target venv."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv-dir", required=True)
    args = parser.parse_args()

    venv_dir = Path(args.venv_dir).resolve()
    if not venv_dir.exists():
        return 0

    try:
        import psutil  # type: ignore
    except ImportError:
        # System python may not have psutil on first install; that is fine.
        return 0

    venv_str = str(venv_dir).lower()
    me = os.getpid()
    found: list[str] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            exe = (proc.info.get("exe") or "").lower()
            if exe and exe.startswith(venv_str):
                cmd = " ".join(proc.info.get("cmdline") or [])
                found.append(f"PID {proc.info['pid']}: {cmd or exe}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if found:
        for line in found:
            print(f"  Running: {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
