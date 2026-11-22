# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for autoaiconsole.exe (onefile, console).
# Build with: scripts\build_exe.ps1  (or: venv\Scripts\python.exe -m PyInstaller autoaiconsole.spec)
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(SPECPATH)
SRC = os.path.join(ROOT, "src")

datas = [
    # Static UI (index.html, app.js, styles.css, vendor/xterm). server.py resolves
    # it as sys._MEIPASS/autoaiconsole/web when frozen.
    (os.path.join(SRC, "autoaiconsole", "web"), os.path.join("autoaiconsole", "web")),
]
binaries = []
hiddenimports = []

# pywinpty: the .pyd needs OpenConsole.exe / conpty.dll / winpty.dll / winpty-agent.exe beside it.
# pywebview: WebView2 interop DLLs under webview/lib plus pythonnet/clr_loader runtime files.
def _keep(name):
    # Drop test suites and the Android backend (not importable on Windows).
    return not (name.startswith("winpty.tests") or name.startswith("webview.platforms.android"))


for pkg in ("winpty", "webview", "clr_loader", "pythonnet"):
    d, b, h = collect_all(pkg, filter_submodules=_keep, exclude_datas=["**/*.jar", "**/tests/**"])
    datas += d
    binaries += b
    hiddenimports += h

# uvicorn picks loop / protocol / lifespan implementations by string at runtime.
for pkg in ("uvicorn", "websockets", "pyte"):
    hiddenimports += collect_submodules(pkg)

hiddenimports += ["autoaiconsole", "autoaiconsole.server", "autoaiconsole.gui", "clr"]

a = Analysis(
    [os.path.join(ROOT, "scripts", "pyinstaller_entry.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["winpty.tests", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="autoaiconsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
