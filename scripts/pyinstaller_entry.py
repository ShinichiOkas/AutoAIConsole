"""PyInstaller entry point for autoaiconsole.exe (same CLI as `python -m autoaiconsole.gui`)."""
from autoaiconsole.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
