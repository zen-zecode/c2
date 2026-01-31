<#
.SYNOPSIS
    Windows AI Agent - One-Liner Bootstrap Installer
    
.DESCRIPTION
    This script sets up the Windows AI Agent:
    1. Installs Python via winget if missing
    2. Creates a virtual environment
    3. Installs Python dependencies
    4. Registers the bot as a Windows Scheduled Task
    
.NOTES
    Run with: irm https://your-gist-url/install.ps1 | iex
    Or: powershell -ExecutionPolicy Bypass -File install.ps1
    
    The script is idempotent - safe to run multiple times.
#>

# Requires elevation for scheduled task
#Requires -RunAsAdministrator

param(
    [string]$InstallPath = "$env:USERPROFILE\WindowsAIAgent"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Colors for output
function Write-Step { param($msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warning { param($msg) Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Error { param($msg) Write-Host "[-] $msg" -ForegroundColor Red }

# Banner
Write-Host @"

 __        ___           _                      _    ___ 
 \ \      / (_)_ __   __| | _____      _____   / \  |_ _|
  \ \ /\ / /| | '_ \ / _` |/ _ \ \ /\ / / __| / _ \  | | 
   \ V  V / | | | | | (_| | (_) \ V  V /\__ \/ ___ \ | | 
    \_/\_/  |_|_| |_|\__,_|\___/ \_/\_/ |___/_/   \_\___|
                                                         
           Windows AI Agent Installer v1.0
           
"@ -ForegroundColor Magenta

Write-Host "Install Path: $InstallPath`n" -ForegroundColor Gray

# =============================================================================
# STEP 1: Check/Install Python
# =============================================================================

Write-Step "Checking Python installation..."

$pythonInstalled = $false
$pythonPath = $null

# Check if Python is already installed
try {
    $pythonVersion = & python --version 2>&1
    if ($pythonVersion -match "Python 3\.") {
        $pythonPath = (Get-Command python).Source
        Write-Success "Python found: $pythonVersion at $pythonPath"
        $pythonInstalled = $true
    }
} catch {
    # Python not in PATH
}

# Check common Python locations
if (-not $pythonInstalled) {
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:ProgramFiles\Python*\python.exe",
        "$env:ProgramFiles(x86)\Python*\python.exe"
    )
    
    foreach ($pattern in $commonPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $pythonPath = $found.FullName
            Write-Success "Python found at: $pythonPath"
            $pythonInstalled = $true
            break
        }
    }
}

# Install Python via winget if not found
if (-not $pythonInstalled) {
    Write-Step "Installing Python via winget..."
    
    # Check if winget is available
    try {
        $wingetVersion = & winget --version 2>&1
        Write-Success "winget found: $wingetVersion"
    } catch {
        Write-Error "winget not found. Please install Python 3.11+ manually."
        Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
        exit 1
    }
    
    # Install Python
    Write-Host "Installing Python 3.12..." -ForegroundColor Yellow
    & winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install Python. Please install manually."
        exit 1
    }
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    
    # Find Python again
    Start-Sleep -Seconds 2
    $pythonPath = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    
    if (-not (Test-Path $pythonPath)) {
        $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    }
    
    Write-Success "Python installed at: $pythonPath"
}

# Get pythonw.exe path (for background execution)
$pythonDir = Split-Path $pythonPath
$pythonwPath = Join-Path $pythonDir "pythonw.exe"

if (-not (Test-Path $pythonwPath)) {
    Write-Warning "pythonw.exe not found, will use python.exe instead"
    $pythonwPath = $pythonPath
}

# =============================================================================
# STEP 2: Create Project Directory
# =============================================================================

Write-Step "Setting up project directory..."

if (-not (Test-Path $InstallPath)) {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Success "Created directory: $InstallPath"
} else {
    Write-Success "Directory exists: $InstallPath"
}

# =============================================================================
# STEP 3: Download main.py if not present
# =============================================================================

$mainPyPath = Join-Path $InstallPath "main.py"

if (-not (Test-Path $mainPyPath)) {
    Write-Warning "main.py not found in $InstallPath"
    Write-Host "Please copy main.py to $InstallPath and run this installer again." -ForegroundColor Yellow
    Write-Host "Or download it from your repository." -ForegroundColor Yellow
    
    # Create a placeholder
    @"
# Windows AI Agent - Placeholder
# Please replace this with the actual main.py from your repository
print("Please configure main.py with your actual agent code.")
input("Press Enter to exit...")
"@ | Set-Content -Path $mainPyPath -Encoding UTF8
    
    Write-Success "Created placeholder main.py - please replace with actual code"
}

# =============================================================================
# STEP 4: Create Virtual Environment
# =============================================================================

Write-Step "Setting up Python virtual environment..."

$venvPath = Join-Path $InstallPath "venv"
$venvActivate = Join-Path $venvPath "Scripts\Activate.ps1"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPythonw = Join-Path $venvPath "Scripts\pythonw.exe"

if (-not (Test-Path $venvActivate)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    & $pythonPath -m venv $venvPath
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create virtual environment"
        exit 1
    }
    
    Write-Success "Virtual environment created"
} else {
    Write-Success "Virtual environment exists"
}

# =============================================================================
# STEP 5: Create requirements.txt
# =============================================================================

Write-Step "Creating requirements.txt..."

$requirementsPath = Join-Path $InstallPath "requirements.txt"

if (-not (Test-Path $requirementsPath)) {
    @"
python-dotenv>=1.0.0
httpx
python-telegram-bot
pynput
"@ | Set-Content -Path $requirementsPath -Encoding UTF8
    Write-Success "Created requirements.txt"
} else {
    Write-Success "requirements.txt already exists, skipping creation"
}

# =============================================================================
# STEP 6: Install Dependencies
# =============================================================================

Write-Step "Installing Python dependencies..."

# Activate venv and install
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r $requirementsPath --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install dependencies"
    exit 1
}

Write-Success "Dependencies installed"

# =============================================================================
# STEP 7: Register Scheduled Task
# =============================================================================

Write-Step "Registering Windows Scheduled Task..."

$taskName = "WindowsAIAgent"

# Remove existing task if present
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Success "Removed existing task"
}

# Create task action - use pythonw.exe for background execution
$action = New-ScheduledTaskAction `
    -Execute $venvPythonw `
    -Argument "`"$mainPyPath`"" `
    -WorkingDirectory $InstallPath

# Create trigger - at logon
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Create principal - run with highest privileges
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -RunLevel Highest `
    -LogonType Interactive

# Task settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Register the task
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Windows AI Agent - Autonomous Telegram Bot" | Out-Null

Write-Success "Scheduled task registered: $taskName"

# =============================================================================
# STEP 8: Create helper scripts
# =============================================================================

Write-Step "Creating helper scripts..."

# Start script
$startScript = Join-Path $InstallPath "start.ps1"
@"
# Start Windows AI Agent
`$venvPython = "`$PSScriptRoot\venv\Scripts\python.exe"
`$mainPy = "`$PSScriptRoot\main.py"
& `$venvPython `$mainPy
"@ | Set-Content -Path $startScript -Encoding UTF8

# Stop script
$stopScript = Join-Path $InstallPath "stop.ps1"
@"
# Stop Windows AI Agent
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue | `
    Where-Object { `$_.Path -like "*WindowsAIAgent*" } | `
    Stop-Process -Force
Write-Host "Windows AI Agent stopped."
"@ | Set-Content -Path $stopScript -Encoding UTF8

# Restart script
$restartScript = Join-Path $InstallPath "restart.ps1"
@"
# Restart Windows AI Agent
& "`$PSScriptRoot\stop.ps1"
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "WindowsAIAgent"
Write-Host "Windows AI Agent restarted."
"@ | Set-Content -Path $restartScript -Encoding UTF8

Write-Success "Created helper scripts (start.ps1, stop.ps1, restart.ps1)"

# =============================================================================
# COMPLETE
# =============================================================================

Write-Host "`n" + "="*60 -ForegroundColor Green
Write-Host "  INSTALLATION COMPLETE!" -ForegroundColor Green
Write-Host "="*60 -ForegroundColor Green

Write-Host @"

Location: $InstallPath

NEXT STEPS:
-----------
1. Edit main.py and configure your credentials:
   - CLOUDFLARE_ACCOUNT_ID
   - CLOUDFLARE_API_TOKEN  
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_ADMIN_ID

2. Start the agent:
   - Run: Start-ScheduledTask -TaskName "WindowsAIAgent"
   - Or: .\start.ps1 (foreground mode for testing)

3. The agent will auto-start on login

HELPER SCRIPTS:
---------------
  .\start.ps1   - Start in foreground (for testing)
  .\stop.ps1    - Stop the agent
  .\restart.ps1 - Restart the agent

"@ -ForegroundColor Cyan

# Ask to start now
$startNow = Read-Host "Start the agent now? (y/n)"
if ($startNow -eq "y" -or $startNow -eq "Y") {
    Write-Step "Starting Windows AI Agent..."
    Start-ScheduledTask -TaskName "WindowsAIAgent"
    Write-Success "Agent started! Check your Telegram bot."
}

Write-Host "`nDone!" -ForegroundColor Green
