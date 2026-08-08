"""
Desktop automation tools for Muno
Allows Muno to control the computer, open apps, type text, move mouse, etc.
"""
import subprocess
import pyautogui
import pygetwindow as gw
import time
import os
from pathlib import Path


# Safety: Disable pyautogui fail-safe (can be re-enabled if needed)
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1  # Small delay between actions


def open_app(app_name):
    """
    Launches an application natively on Windows.
    
    Args:
        app_name: Name of the application (chrome, notepad, calculator, etc.)
    
    Returns:
        Success/failure message
    """
    app_name_lower = app_name.lower()
    
    try:
        if "chrome" in app_name_lower or "browser" in app_name_lower:
            subprocess.Popen(["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"])
            return "✅ Opened Chrome browser"
        
        elif "notepad" in app_name_lower:
            subprocess.Popen(["notepad.exe"])
            return "✅ Opened Notepad"
        
        elif "calculator" in app_name_lower or "calc" in app_name_lower:
            subprocess.Popen(["calc.exe"])
            return "✅ Opened Calculator"
        
        elif "explorer" in app_name_lower or "file" in app_name_lower:
            subprocess.Popen(["explorer.exe"])
            return "✅ Opened File Explorer"
        
        elif "paint" in app_name_lower:
            subprocess.Popen(["mspaint.exe"])
            return "✅ Opened Paint"
        
        elif "cmd" in app_name_lower or "command" in app_name_lower:
            subprocess.Popen(["cmd.exe"])
            return "✅ Opened Command Prompt"
        
        elif "vscode" in app_name_lower or "code" in app_name_lower:
            subprocess.Popen(["code"])
            return "✅ Opened VS Code"
        
        else:
            # Try to launch as-is (works for apps in PATH)
            subprocess.Popen([app_name])
            return f"✅ Attempted to open {app_name}"
            
    except Exception as e:
        return f"❌ Failed to open {app_name}: {str(e)}"


def type_text(text_to_type, interval=0.05):
    """
    Types out text into the currently active window.
    
    Args:
        text_to_type: The text string to type
        interval: Delay between keystrokes (default 0.05 seconds)
    
    Returns:
        Success message
    """
    try:
        time.sleep(0.5)  # Give user a moment to focus the window
        pyautogui.write(text_to_type, interval=interval)
        return f"✅ Typed: {text_to_type[:50]}..."
    except Exception as e:
        return f"❌ Failed to type text: {str(e)}"


def move_mouse(x, y, duration=0.5):
    """
    Moves the mouse cursor to specified coordinates.
    
    Args:
        x: X coordinate (horizontal)
        y: Y coordinate (vertical)
        duration: How long the movement takes (smooth animation)
    
    Returns:
        Success message
    """
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return f"✅ Moved mouse to ({x}, {y})"
    except Exception as e:
        return f"❌ Failed to move mouse: {str(e)}"


def click_mouse(button='left', clicks=1):
    """
    Clicks the mouse at current position.
    
    Args:
        button: 'left', 'right', or 'middle'
        clicks: Number of clicks (1 for single, 2 for double)
    
    Returns:
        Success message
    """
    try:
        pyautogui.click(button=button, clicks=clicks)
        return f"✅ {button.capitalize()} clicked {clicks} time(s)"
    except Exception as e:
        return f"❌ Failed to click: {str(e)}"


def press_key(key_name):
    """
    Presses a keyboard key.
    
    Args:
        key_name: Name of key ('enter', 'tab', 'esc', 'ctrl', 'alt', etc.)
    
    Returns:
        Success message
    """
    try:
        pyautogui.press(key_name)
        return f"✅ Pressed {key_name}"
    except Exception as e:
        return f"❌ Failed to press key: {str(e)}"


def hotkey(*keys):
    """
    Presses a combination of keys (like Ctrl+C).
    
    Args:
        *keys: Variable number of key names
    
    Returns:
        Success message
    """
    try:
        pyautogui.hotkey(*keys)
        return f"✅ Pressed {'+'.join(keys)}"
    except Exception as e:
        return f"❌ Failed to press hotkey: {str(e)}"


def get_mouse_position():
    """
    Gets current mouse cursor position.
    
    Returns:
        Tuple of (x, y) coordinates
    """
    pos = pyautogui.position()
    return f"📍 Mouse position: ({pos.x}, {pos.y})"


def get_screen_size():
    """
    Gets the screen resolution.
    
    Returns:
        Tuple of (width, height)
    """
    size = pyautogui.size()
    return f"📐 Screen size: {size.width}x{size.height}"


def list_windows():
    """
    Lists all open windows.
    
    Returns:
        List of window titles
    """
    try:
        windows = gw.getAllTitles()
        # Filter out empty titles
        windows = [w for w in windows if w.strip()]
        return f"🪟 Open windows ({len(windows)}):\n" + "\n".join(f"  • {w}" for w in windows[:10])
    except Exception as e:
        return f"❌ Failed to list windows: {str(e)}"


def focus_window(title_substring):
    """
    Brings a window to the front by partial title match.
    
    Args:
        title_substring: Part of the window title to match
    
    Returns:
        Success message
    """
    try:
        windows = gw.getWindowsWithTitle(title_substring)
        if windows:
            windows[0].activate()
            return f"✅ Focused window: {windows[0].title}"
        else:
            return f"❌ No window found matching '{title_substring}'"
    except Exception as e:
        return f"❌ Failed to focus window: {str(e)}"


def minimize_window(title_substring):
    """
    Minimizes a window by partial title match.
    
    Args:
        title_substring: Part of the window title to match
    
    Returns:
        Success message
    """
    try:
        windows = gw.getWindowsWithTitle(title_substring)
        if windows:
            windows[0].minimize()
            return f"✅ Minimized: {windows[0].title}"
        else:
            return f"❌ No window found matching '{title_substring}'"
    except Exception as e:
        return f"❌ Failed to minimize: {str(e)}"


def maximize_window(title_substring):
    """
    Maximizes a window by partial title match.
    
    Args:
        title_substring: Part of the window title to match
    
    Returns:
        Success message
    """
    try:
        windows = gw.getWindowsWithTitle(title_substring)
        if windows:
            windows[0].maximize()
            return f"✅ Maximized: {windows[0].title}"
        else:
            return f"❌ No window found matching '{title_substring}'"
    except Exception as e:
        return f"❌ Failed to maximize: {str(e)}"


def take_screenshot(filename="screenshot.png"):
    """
    Takes a screenshot and saves it.
    
    Args:
        filename: Name of the file to save
    
    Returns:
        Success message with file path
    """
    try:
        screenshot = pyautogui.screenshot()
        filepath = Path.home() / "Pictures" / filename
        screenshot.save(filepath)
        return f"📸 Screenshot saved to {filepath}"
    except Exception as e:
        return f"❌ Failed to take screenshot: {str(e)}"


def system_sleep():
    """
    Puts Muno into sleep/idle mode - stops active processing.
    
    Returns:
        Confirmation message
    """
    return "💤 Going to sleep now. Wake me when you need me!"


def system_mute(mute=True):
    """
    Toggles voice output on/off.
    
    Args:
        mute: True to disable voice, False to enable
    
    Returns:
        Confirmation message
    """
    if mute:
        return "🤐 Switching to silent mode. I'll use text bubbles only."
    else:
        return "🔊 Voice mode back on!"


def system_exit():
    """
    Initiates shutdown sequence (requires confirmation).
    
    Returns:
        Confirmation request
    """
    return "👋 Are you sure you want me to go away?"


# Tool schema for LLM function calling
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Opens a desktop application like Chrome, Notepad, Calculator, File Explorer, Paint, Command Prompt, or VS Code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application to open (e.g., 'chrome', 'notepad', 'calculator')"
                    }
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Types text into the currently active window or text field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text_to_type": {
                        "type": "string",
                        "description": "The text to type"
                    }
                },
                "required": ["text_to_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "click_mouse",
            "description": "Clicks the mouse at its current position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Which mouse button to click"
                    },
                    "clicks": {
                        "type": "integer",
                        "description": "Number of clicks (1 or 2)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Presses a keyboard key like Enter, Tab, Escape, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_name": {
                        "type": "string",
                        "description": "Name of the key to press (e.g., 'enter', 'tab', 'esc')"
                    }
                },
                "required": ["key_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": "Presses a keyboard shortcut combination like Ctrl+C, Ctrl+V, Alt+Tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of keys to press together (e.g., ['ctrl', 'c'])"
                    }
                },
                "required": ["keys"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "Lists all currently open windows and their titles.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Brings a specific window to the front by matching part of its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_substring": {
                        "type": "string",
                        "description": "Part of the window title to match"
                    }
                },
                "required": ["title_substring"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "minimize_window",
            "description": "Minimizes a window by matching part of its title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_substring": {
                        "type": "string",
                        "description": "Part of the window title to match"
                    }
                },
                "required": ["title_substring"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Takes a screenshot of the entire screen and saves it to Pictures folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Optional filename for the screenshot"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_sleep",
            "description": "Puts Muno into sleep/idle mode when user says 'go to sleep', 'take a break', 'rest', etc.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_mute",
            "description": "Toggles voice output when user says 'be quiet', 'shh', 'silence', 'mute', 'I'm typing', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mute": {
                        "type": "boolean",
                        "description": "True to mute voice, False to unmute"
                    }
                },
                "required": ["mute"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_exit",
            "description": "Initiates shutdown when user says 'exit', 'goodbye', 'close', 'quit', etc. Always asks for confirmation first.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]


# Map function names to actual functions
TOOL_FUNCTIONS = {
    "open_app": open_app,
    "type_text": type_text,
    "move_mouse": move_mouse,
    "click_mouse": click_mouse,
    "press_key": press_key,
    "hotkey": lambda keys: hotkey(*keys),
    "get_mouse_position": get_mouse_position,
    "get_screen_size": get_screen_size,
    "list_windows": list_windows,
    "focus_window": focus_window,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "take_screenshot": take_screenshot,
    "system_sleep": system_sleep,
    "system_mute": system_mute,
    "system_exit": system_exit
}
