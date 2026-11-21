"""Self-check that venv python and editable package point to this checkout."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _is_within(child: Path, parent: Path) -> bool:
    c = os.path.normcase(str(child.resolve()))
    p = os.path.normcase(str(parent.resolve()))
    return c == p or c.startswith(p + os.sep)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-root", required=True)
    args = parser.parse_args()

    expected_root = Path(args.expected_root.strip('"')).resolve()
    exe_path = Path(sys.executable).resolve()

    if not _is_within(exe_path, expected_root):
        print(f"[ERROR] Python executable is outside project root: {exe_path} (root={expected_root})")
        return 1

    import autoaiconsole

    pkg_path = Path(autoaiconsole.__file__).resolve()
    if not _is_within(pkg_path, expected_root):
        print(f"[ERROR] autoaiconsole imported from wrong checkout: {pkg_path} (root={expected_root})")
        return 1

    try:
        import webview  # noqa: F401
        gui_shell = "pywebview available"
    except ImportError:
        gui_shell = "browser fallback (pywebview not installed)"

    print(f"  OK python : {exe_path}")
    print(f"  OK package: {pkg_path}")
    print(f"  OK shell  : {gui_shell}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
