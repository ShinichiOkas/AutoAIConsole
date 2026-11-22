<#
.SYNOPSIS
  Build autoaiconsole.exe with PyInstaller and install it onto PATH.

.DESCRIPTION
  Install directory priority:
    1. -InstallDir <path>
    2. $env:AGENT_INSTALL_DIR   (machine-specific; never commit it)
    3. $env:USERPROFILE\.local\bin
  Uses the venv created by install.bat (venv\Scripts\python.exe). Never uses system python.
  After install, runs a smoke test (--server-only, /api/health) unless -SkipVerify is given.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -InstallDir C:\bin -SkipVerify
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$SkipVerify,
    [int]$VerifyPort = 18799,
    [int]$VerifyTimeoutSec = 60
)

# "Continue", not "Stop": Windows PowerShell 5.1 turns native stderr (pip / PyInstaller logs)
# into terminating errors under "Stop". Native steps are checked via $LASTEXITCODE instead,
# and cmdlets that must not fail silently use -ErrorAction Stop.
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..") -ErrorAction Stop).Path
Set-Location $RepoRoot -ErrorAction Stop
$Steps = 6

function Fail([string]$msg) {
    Write-Host "  [ERROR] $msg" -ForegroundColor Red
    exit 1
}

Write-Host "=== AutoAIConsole build_exe ==="

# ---------------------------------------------------------------- [1/6]
Write-Host "[1/$Steps] Locating venv python..."
$Py = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Fail "venv not found ($Py). Run install.bat first."
}
Write-Host "  python: $Py"

# ---------------------------------------------------------------- [2/6]
Write-Host "[2/$Steps] Ensuring PyInstaller is installed in venv..."
& $Py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller missing -> pip install -e .[build]"
    & $Py -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { Fail "pip install -e .[build] failed." }
}
$PyiVersion = (& $Py -m PyInstaller --version) | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller still not importable after install." }
Write-Host "  PyInstaller $PyiVersion"

# ---------------------------------------------------------------- [3/6]
Write-Host "[3/$Steps] Running PyInstaller (onefile, console)..."
$Spec = Join-Path $RepoRoot "autoaiconsole.spec"
$DistDir = Join-Path $RepoRoot "dist"
$WorkDir = Join-Path $RepoRoot "build"
& $Py -m PyInstaller --noconfirm --clean --log-level WARN --distpath $DistDir --workpath $WorkDir $Spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }
$BuiltExe = Join-Path $DistDir "autoaiconsole.exe"
if (-not (Test-Path $BuiltExe)) { Fail "Build output not found: $BuiltExe" }
$SizeMB = [math]::Round((Get-Item $BuiltExe).Length / 1MB, 1)
Write-Host "  built: $BuiltExe ($SizeMB MB)"

# ---------------------------------------------------------------- [4/6]
Write-Host "[4/$Steps] Resolving install directory..."
if ($InstallDir) {
    $Target = $InstallDir; $Source = "-InstallDir argument"
} elseif ($env:AGENT_INSTALL_DIR) {
    $Target = $env:AGENT_INSTALL_DIR; $Source = "AGENT_INSTALL_DIR env var"
} else {
    $Target = Join-Path $env:USERPROFILE ".local\bin"; $Source = "default (USERPROFILE\.local\bin)"
}
try {
    if (-not (Test-Path $Target)) {
        New-Item -ItemType Directory -Force -Path $Target -ErrorAction Stop | Out-Null
    }
    $Target = (Resolve-Path $Target -ErrorAction Stop).Path
} catch {
    Fail "Cannot create install dir ${Target}: $($_.Exception.Message)"
}
Write-Host "  install dir: $Target  [from $Source]"

# ---------------------------------------------------------------- [5/6]
Write-Host "[5/$Steps] Installing autoaiconsole.exe..."
$InstalledExe = Join-Path $Target "autoaiconsole.exe"
try {
    Copy-Item -Path $BuiltExe -Destination $InstalledExe -Force -ErrorAction Stop
} catch {
    Fail ("Could not overwrite $InstalledExe (is autoaiconsole.exe running? close it and retry; " +
          "this script will not terminate it). Detail: " + $_.Exception.Message)
}
Write-Host "  installed: $InstalledExe"

$PathDirs = @()
$PathDirs += ($env:PATH -split ";")
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath) { $PathDirs += ($UserPath -split ";") }
$OnPath = $PathDirs | Where-Object { $_ -and ($_.TrimEnd("\") -ieq $Target.TrimEnd("\")) }
if (-not $OnPath) {
    Write-Host "  [WARN] $Target is not on PATH. Add it to your user PATH to run 'autoaiconsole' anywhere." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- [6/6]
if ($SkipVerify) {
    Write-Host "[6/$Steps] Smoke test skipped (-SkipVerify)."
    Write-Host "=== build_exe completed ==="
    exit 0
}

Write-Host "[6/$Steps] Smoke test: $InstalledExe --server-only --port $VerifyPort"
$listener = $null
try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $VerifyPort)
    $listener.Start()
} catch {
    Fail "Port $VerifyPort is already in use. Pass -VerifyPort <free port> or -SkipVerify."
} finally {
    if ($listener) { $listener.Stop() }
}

$VerifyCwd = Join-Path ([System.IO.Path]::GetTempPath()) "autoaiconsole-smoke"
New-Item -ItemType Directory -Force -Path $VerifyCwd | Out-Null
$LogOut = Join-Path $VerifyCwd "stdout.log"
$LogErr = Join-Path $VerifyCwd "stderr.log"
$proc = Start-Process -FilePath $InstalledExe -ArgumentList @("--server-only", "--port", "$VerifyPort") `
    -WorkingDirectory $VerifyCwd -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr `
    -WindowStyle Hidden -PassThru

$Base = "http://127.0.0.1:$VerifyPort"
$failures = @()
try {
    $health = $null
    $deadline = (Get-Date).AddSeconds($VerifyTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { break }
        try {
            $health = Invoke-RestMethod -Uri "$Base/api/health" -TimeoutSec 3
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $health) {
        $failures += "/api/health did not respond within ${VerifyTimeoutSec}s (exited=$($proc.HasExited))"
    } elseif ($health.ok -ne $true) {
        $failures += "/api/health returned ok=$($health.ok)"
    } else {
        Write-Host "  /api/health ok=true version=$($health.version) mode=$($health.mode)"
        foreach ($p in @("/", "/static/app.js", "/static/vendor/xterm/xterm.js")) {
            try {
                $r = Invoke-WebRequest -Uri "$Base$p" -UseBasicParsing -TimeoutSec 5
                Write-Host "  GET $p -> $($r.StatusCode) ($($r.RawContentLength) bytes)"
                if ($r.StatusCode -ne 200) { $failures += "GET $p -> $($r.StatusCode)" }
            } catch {
                $failures += "GET $p failed: $($_.Exception.Message)"
            }
        }
    }
} finally {
    # Onefile = bootloader parent + extracted child: kill the whole tree we started.
    if (-not $proc.HasExited) {
        & taskkill.exe /T /F /PID $proc.Id *> $null
    }
}

if ($failures.Count -gt 0) {
    foreach ($f in $failures) { Write-Host "  [FAIL] $f" -ForegroundColor Red }
    if (Test-Path $LogErr) {
        Write-Host "  --- stderr (tail) ---"
        Get-Content $LogErr -Tail 30 | ForEach-Object { Write-Host "  $_" }
    }
    Fail "Smoke test failed."
}

Write-Host "=== build_exe completed: $InstalledExe ==="
exit 0
