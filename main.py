#!/usr/bin/env python3
"""
Windows AI Agent - Self-hosted Autonomous System Controller
============================================================
Brain: Cloudflare Workers AI (@cf/openai/gpt-oss-120b)
Gateway: Telegram Bot with strict admin verification
Tools: execute_cmd, file_manager, software_installer

Author: Your Name
License: MIT
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============================================================================
# CONFIGURATION - EDIT THESE VALUES
# =============================================================================

# Cloudflare Workers AI Configuration
CLOUDFLARE_ACCOUNT_ID = "YOUR_CLOUDFLARE_ACCOUNT_ID"
CLOUDFLARE_API_TOKEN = "YOUR_CLOUDFLARE_API_TOKEN"
CLOUDFLARE_MODEL = "@cf/openai/gpt-oss-120b"

# Telegram Configuration  
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_ADMIN_ID = 123456789  # Your Telegram user ID (integer)

# Agent Configuration
LOG_FILE = Path(__file__).parent / "agent.log"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("WindowsAIAgent")

# =============================================================================
# GLOBAL STATE
# =============================================================================

# Reasoning effort: "medium" for daily tasks, "high" for complex debugging
reasoning_effort = "medium"

# Pending confirmations for high-risk commands
pending_confirmations: dict[str, dict[str, Any]] = {}

# Conversation history per chat
conversation_history: dict[int, list[dict]] = {}

# =============================================================================
# HIGH-RISK COMMAND DETECTION
# =============================================================================

HIGH_RISK_PATTERNS = [
    r"\bdel\b",
    r"\bdelete\b",
    r"\bremove-item\b",
    r"\brm\b",
    r"\brmdir\b",
    r"\brd\b",
    r"\bformat\b",
    r"\breg\s+delete\b",
    r"\bremove-itemproperty\b",
    r"\bclear-content\b",
    r"\bstop-process\b",
    r"\bkill\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bdiskpart\b",
    r"\bnet\s+user\b.*\s+/delete",
    r"\bnet\s+localgroup\b.*\s+/delete",
]


def is_high_risk_command(command: str) -> bool:
    """Check if a command matches any high-risk pattern."""
    command_lower = command.lower()
    for pattern in HIGH_RISK_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return True
    return False


# =============================================================================
# TOOL DEFINITIONS FOR CLOUDFLARE AI
# =============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_cmd",
            "description": "Execute a PowerShell or CMD command on the Windows system and return the output. Use this for system administration, file operations, process management, and any command-line tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The PowerShell or CMD command to execute. For PowerShell, prefix with 'powershell -Command' if needed.",
                    },
                    "use_powershell": {
                        "type": "boolean",
                        "description": "If true, run the command in PowerShell. If false, run in CMD. Default is true.",
                        "default": True,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_manager",
            "description": "Manage files on the system: download files from URLs, move/copy files, read file contents, or write to log files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["download", "move", "copy", "read", "write_log"],
                        "description": "The file operation to perform.",
                    },
                    "source": {
                        "type": "string",
                        "description": "For download: the URL. For move/copy/read: the source file path.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "For download/move/copy: the destination path. For write_log: the log file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "For write_log: the content to write to the log file.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "software_installer",
            "description": "Download and silently install software from a direct download URL (.exe, .msi). Attempts common silent install flags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "download_url": {
                        "type": "string",
                        "description": "Direct URL to the installer file (.exe or .msi).",
                    },
                    "installer_name": {
                        "type": "string",
                        "description": "Friendly name for the installer file (e.g., 'chrome_installer.exe').",
                    },
                    "silent_flags": {
                        "type": "string",
                        "description": "Custom silent install flags. If not provided, common flags like /S, /silent, /quiet, /VERYSILENT will be tried.",
                    },
                },
                "required": ["download_url", "installer_name"],
            },
        },
    },
]

# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def execute_cmd(command: str, use_powershell: bool = True) -> str:
    """Execute a command and return the output."""
    try:
        if use_powershell:
            # Run command in PowerShell
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        else:
            # Run command in CMD
            result = subprocess.run(
                ["cmd", "/c", command],
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            return f"✅ Command executed successfully.\n\nOutput:\n```\n{output or '(no output)'}\n```"
        else:
            return f"⚠️ Command completed with return code {result.returncode}.\n\nOutput:\n```\n{output or '(no output)'}\n```\n\nErrors:\n```\n{error or '(no errors)'}\n```"

    except subprocess.TimeoutExpired:
        return "❌ Command timed out after 5 minutes."
    except Exception as e:
        return f"❌ Error executing command: {str(e)}"


async def file_manager(
    action: str,
    source: str | None = None,
    destination: str | None = None,
    content: str | None = None,
) -> str:
    """Perform file management operations."""
    try:
        if action == "download":
            if not source or not destination:
                return "❌ Download requires 'source' (URL) and 'destination' (path)."

            # Ensure download directory exists
            dest_path = Path(destination)
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()

                with open(dest_path, "wb") as f:
                    f.write(response.content)

            size_mb = dest_path.stat().st_size / (1024 * 1024)
            return f"✅ Downloaded successfully.\n📁 Path: `{dest_path}`\n📊 Size: {size_mb:.2f} MB"

        elif action == "move":
            if not source or not destination:
                return "❌ Move requires 'source' and 'destination' paths."

            src_path = Path(source)
            dest_path = Path(destination)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.rename(dest_path)
            return f"✅ Moved `{src_path}` to `{dest_path}`"

        elif action == "copy":
            if not source or not destination:
                return "❌ Copy requires 'source' and 'destination' paths."

            import shutil

            src_path = Path(source)
            dest_path = Path(destination)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            return f"✅ Copied `{src_path}` to `{dest_path}`"

        elif action == "read":
            if not source:
                return "❌ Read requires 'source' path."

            src_path = Path(source)
            if not src_path.exists():
                return f"❌ File not found: `{src_path}`"

            content = src_path.read_text(encoding="utf-8", errors="replace")
            # Truncate if too long
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"
            return f"📄 Contents of `{src_path}`:\n```\n{content}\n```"

        elif action == "write_log":
            if not destination or not content:
                return "❌ write_log requires 'destination' (path) and 'content'."

            log_path = Path(destination)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {content}\n"

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)

            return f"✅ Logged to `{log_path}`"

        else:
            return f"❌ Unknown action: {action}"

    except Exception as e:
        return f"❌ File operation failed: {str(e)}"


async def software_installer(
    download_url: str,
    installer_name: str,
    silent_flags: str | None = None,
) -> str:
    """Download and silently install software."""
    try:
        # Ensure downloads directory exists
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        installer_path = DOWNLOAD_DIR / installer_name

        # Download the installer
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            response = await client.get(download_url)
            response.raise_for_status()

            with open(installer_path, "wb") as f:
                f.write(response.content)

        size_mb = installer_path.stat().st_size / (1024 * 1024)
        result_msg = f"📥 Downloaded: `{installer_path}` ({size_mb:.2f} MB)\n\n"

        # Determine install command
        ext = installer_path.suffix.lower()

        if ext == ".msi":
            # MSI silent install
            cmd = f'msiexec /i "{installer_path}" /quiet /norestart'
        elif ext == ".exe":
            # Try common silent flags
            if silent_flags:
                flags = silent_flags
            else:
                # Common silent install flags
                flags = "/S /silent /quiet /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
            cmd = f'"{installer_path}" {flags}'
        else:
            return result_msg + f"⚠️ Unknown installer type: {ext}. Manual installation may be required."

        # Run installation
        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for installations
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        if process.returncode == 0:
            result_msg += "✅ Installation completed successfully!"
        elif process.returncode == 3010:
            result_msg += "✅ Installation completed. A reboot is required."
        else:
            result_msg += f"⚠️ Installation completed with code {process.returncode}.\n"
            if process.stderr:
                result_msg += f"Errors:\n```\n{process.stderr[:500]}\n```"

        return result_msg

    except subprocess.TimeoutExpired:
        return "❌ Installation timed out after 10 minutes."
    except Exception as e:
        return f"❌ Installation failed: {str(e)}"


# =============================================================================
# CLOUDFLARE WORKERS AI INTEGRATION
# =============================================================================


async def call_cloudflare_ai(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Call Cloudflare Workers AI API with function calling support."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"

    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messages": messages,
    }

    # Add tools if provided
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # Add reasoning effort
    global reasoning_effort
    if reasoning_effort == "high":
        payload["reasoning_effort"] = "high"
    else:
        payload["reasoning_effort"] = "medium"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def process_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Process tool calls from the AI and return results."""
    results = []

    for tool_call in tool_calls:
        tool_name = tool_call.get("function", {}).get("name", "")
        arguments_str = tool_call.get("function", {}).get("arguments", "{}")

        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError:
            arguments = {}

        tool_id = tool_call.get("id", f"call_{tool_name}")

        logger.info(f"Executing tool: {tool_name} with args: {arguments}")

        # Execute the appropriate tool
        if tool_name == "execute_cmd":
            result = await execute_cmd(
                command=arguments.get("command", ""),
                use_powershell=arguments.get("use_powershell", True),
            )
        elif tool_name == "file_manager":
            result = await file_manager(
                action=arguments.get("action", ""),
                source=arguments.get("source"),
                destination=arguments.get("destination"),
                content=arguments.get("content"),
            )
        elif tool_name == "software_installer":
            result = await software_installer(
                download_url=arguments.get("download_url", ""),
                installer_name=arguments.get("installer_name", "installer.exe"),
                silent_flags=arguments.get("silent_flags"),
            )
        else:
            result = f"❌ Unknown tool: {tool_name}"

        results.append({
            "role": "tool",
            "tool_call_id": tool_id,
            "content": result,
        })

    return results


# =============================================================================
# TELEGRAM BOT HANDLERS
# =============================================================================


def is_admin(user_id: int) -> bool:
    """Check if the user is the authorized admin."""
    return user_id == TELEGRAM_ADMIN_ID


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_admin(update.effective_user.id):
        return  # Silently ignore non-admin users

    await update.message.reply_text(
        "🤖 *Windows AI Agent Online*\n\n"
        "I'm your autonomous Windows system controller. I can:\n"
        "• Execute PowerShell/CMD commands\n"
        "• Manage files (download, move, read, write)\n"
        "• Install software silently\n\n"
        "*Commands:*\n"
        "/high - Switch to high reasoning mode (complex tasks)\n"
        "/normal - Switch to normal reasoning mode (daily tasks)\n"
        "/status - Show current status\n"
        "/clear - Clear conversation history\n\n"
        "Just tell me what you need!",
        parse_mode="Markdown",
    )


async def high_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch to high reasoning effort."""
    if not is_admin(update.effective_user.id):
        return

    global reasoning_effort
    reasoning_effort = "high"
    await update.message.reply_text(
        "🧠 Switched to *HIGH* reasoning mode.\n"
        "Best for complex system debugging and multi-step tasks.",
        parse_mode="Markdown",
    )


async def normal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch to normal/medium reasoning effort."""
    if not is_admin(update.effective_user.id):
        return

    global reasoning_effort
    reasoning_effort = "medium"
    await update.message.reply_text(
        "⚡ Switched to *NORMAL* reasoning mode.\n"
        "Optimized for quick daily tasks.",
        parse_mode="Markdown",
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current agent status."""
    if not is_admin(update.effective_user.id):
        return

    chat_id = update.effective_chat.id
    history_count = len(conversation_history.get(chat_id, []))

    await update.message.reply_text(
        f"📊 *Agent Status*\n\n"
        f"🧠 Reasoning Mode: `{reasoning_effort}`\n"
        f"💬 Conversation History: `{history_count}` messages\n"
        f"⏳ Pending Confirmations: `{len(pending_confirmations)}`\n"
        f"📁 Download Directory: `{DOWNLOAD_DIR}`",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation history."""
    if not is_admin(update.effective_user.id):
        return

    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await update.message.reply_text("🗑️ Conversation history cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages from admin."""
    if not is_admin(update.effective_user.id):
        logger.warning(f"Unauthorized access attempt from user {update.effective_user.id}")
        return

    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Initialize conversation history if needed
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []

    # System prompt
    system_message = {
        "role": "system",
        "content": (
            "You are a powerful Windows AI Agent with full system access. "
            "You help the admin manage their Windows system through natural language commands. "
            "You have access to these tools:\n"
            "1. execute_cmd - Run PowerShell or CMD commands\n"
            "2. file_manager - Download, move, copy, read files, write logs\n"
            "3. software_installer - Download and silently install .exe/.msi files\n\n"
            "Always explain what you're about to do before using tools. "
            "Be helpful, thorough, and proactive in solving problems. "
            "If a task requires multiple steps, execute them in sequence. "
            "Report results clearly and suggest follow-up actions when appropriate."
        ),
    }

    # Add user message to history
    conversation_history[chat_id].append({"role": "user", "content": user_message})

    # Build messages for API call
    messages = [system_message] + conversation_history[chat_id][-20:]  # Keep last 20 messages

    # Send typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # Call Cloudflare AI
        response = await call_cloudflare_ai(messages, TOOLS)

        # Extract the response
        result = response.get("result", {})
        response_content = result.get("response", "")
        tool_calls = result.get("tool_calls", [])

        # Handle tool calls
        if tool_calls:
            # Check for high-risk commands
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                arguments_str = tool_call.get("function", {}).get("arguments", "{}")
                
                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                except json.JSONDecodeError:
                    arguments = {}

                # Check if this is a high-risk command
                if tool_name == "execute_cmd":
                    command = arguments.get("command", "")
                    if is_high_risk_command(command):
                        # Request confirmation
                        confirm_id = f"confirm_{chat_id}_{hash(command)}"
                        pending_confirmations[confirm_id] = {
                            "tool_calls": tool_calls,
                            "messages": messages,
                            "chat_id": chat_id,
                            "command": command,
                        }

                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("✅ Execute", callback_data=f"exec_{confirm_id}"),
                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{confirm_id}"),
                            ]
                        ])

                        await update.message.reply_text(
                            f"⚠️ *High-Risk Command Detected*\n\n"
                            f"```\n{command}\n```\n\n"
                            "Do you want to execute this command?",
                            parse_mode="Markdown",
                            reply_markup=keyboard,
                        )
                        return

            # No high-risk commands, execute tools
            assistant_message = {
                "role": "assistant",
                "content": response_content,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)

            # Process tool calls
            tool_results = await process_tool_calls(tool_calls)

            # Add tool results to messages
            messages.extend(tool_results)

            # Get final response from AI
            final_response = await call_cloudflare_ai(messages, TOOLS)
            final_content = final_response.get("result", {}).get("response", "")

            # Check if there are more tool calls
            more_tool_calls = final_response.get("result", {}).get("tool_calls", [])
            
            if more_tool_calls:
                # Execute additional tool calls (for multi-step tasks)
                more_results = await process_tool_calls(more_tool_calls)
                messages.extend(more_results)
                
                # Get final-final response
                final_final = await call_cloudflare_ai(messages, TOOLS)
                final_content = final_final.get("result", {}).get("response", "")

            # Send response
            if final_content:
                # Split long messages
                if len(final_content) > 4000:
                    chunks = [final_content[i:i+4000] for i in range(0, len(final_content), 4000)]
                    for chunk in chunks:
                        await update.message.reply_text(chunk)
                else:
                    await update.message.reply_text(final_content)

                # Update conversation history
                conversation_history[chat_id].append({"role": "assistant", "content": final_content})
            else:
                # Send tool results directly
                for result in tool_results:
                    content = result.get("content", "")
                    if content:
                        await update.message.reply_text(content)

        else:
            # No tool calls, just respond
            if response_content:
                if len(response_content) > 4000:
                    chunks = [response_content[i:i+4000] for i in range(0, len(response_content), 4000)]
                    for chunk in chunks:
                        await update.message.reply_text(chunk)
                else:
                    await update.message.reply_text(response_content)

                conversation_history[chat_id].append({"role": "assistant", "content": response_content})
            else:
                await update.message.reply_text("🤔 I didn't get a response. Please try again.")

    except httpx.HTTPStatusError as e:
        logger.error(f"Cloudflare API error: {e}")
        await update.message.reply_text(
            f"❌ *API Error*\n\nStatus: {e.response.status_code}\n"
            f"Please check your Cloudflare credentials.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards."""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    data = query.data

    if data.startswith("exec_confirm_"):
        confirm_id = data[5:]  # Remove "exec_" prefix
        
        if confirm_id in pending_confirmations:
            pending = pending_confirmations.pop(confirm_id)
            tool_calls = pending["tool_calls"]
            messages = pending["messages"]
            chat_id = pending["chat_id"]
            command = pending["command"]

            await query.edit_message_text(f"⏳ Executing command...\n```\n{command}\n```", parse_mode="Markdown")

            # Execute the tool calls
            try:
                results = await process_tool_calls(tool_calls)
                
                for result in results:
                    content = result.get("content", "")
                    if content:
                        await context.bot.send_message(chat_id=chat_id, text=content)
                        
            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {str(e)}")

    elif data.startswith("cancel_confirm_"):
        confirm_id = data[7:]  # Remove "cancel_" prefix
        
        if confirm_id in pending_confirmations:
            pending_confirmations.pop(confirm_id)
            await query.edit_message_text("❌ Command cancelled.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Error: {context.error}", exc_info=context.error)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


def main() -> None:
    """Start the bot."""
    # Validate configuration
    if CLOUDFLARE_ACCOUNT_ID == "YOUR_CLOUDFLARE_ACCOUNT_ID":
        logger.error("Please configure CLOUDFLARE_ACCOUNT_ID in main.py")
        sys.exit(1)
    if CLOUDFLARE_API_TOKEN == "YOUR_CLOUDFLARE_API_TOKEN":
        logger.error("Please configure CLOUDFLARE_API_TOKEN in main.py")
        sys.exit(1)
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("Please configure TELEGRAM_BOT_TOKEN in main.py")
        sys.exit(1)
    if TELEGRAM_ADMIN_ID == 123456789:
        logger.error("Please configure TELEGRAM_ADMIN_ID in main.py")
        sys.exit(1)

    # Create downloads directory
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Starting Windows AI Agent...")
    logger.info(f"Reasoning effort: {reasoning_effort}")
    logger.info(f"Admin ID: {TELEGRAM_ADMIN_ID}")

    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("high", high_command))
    application.add_handler(CommandHandler("normal", normal_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Add error handler
    application.add_error_handler(error_handler)

    # Start polling
    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
