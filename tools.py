"""
Desktop automation tools for Muno
Allows Muno to control the computer, open apps, type text, move mouse, etc.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import pyautogui
import pygetwindow as gw

import activity
import browser as browser_control
import capabilities
import email_api
import github_api
import journal
import recipes
import watcher


# Safety: Disable pyautogui fail-safe (can be re-enabled if needed)
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1  # Small delay between actions


def _resolve_chrome():
    """Locate the Chrome executable across common install locations."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return shutil.which("chrome")


def _launch_vscode():
    """Launch VS Code, accounting for the code.cmd shim on Windows."""
    executable = shutil.which("code") or shutil.which("code.cmd")
    if not executable:
        raise FileNotFoundError("VS Code ('code') was not found on PATH.")
    # code is a .cmd shim on Windows; run through the shell so it resolves.
    subprocess.Popen(["cmd", "/c", "code"])


# Convenient shortcuts with reliable launchers and friendly labels. Anything
# not listed here still launches through the generic resolver below, so Muno can
# open any application. Every launch is gated by an explicit user confirmation
# prompt in the app (see AIWorker.CONFIRMATION_REQUIRED), which is the control
# that prevents unattended or injected launches.
def _open_chrome():
    chrome = _resolve_chrome()
    if not chrome:
        raise FileNotFoundError("Chrome was not found in any known location.")
    subprocess.Popen([chrome])


KNOWN_APPS = {
    "chrome": (_open_chrome, "Chrome browser"),
    "browser": (_open_chrome, "Chrome browser"),
    "notepad": (lambda: subprocess.Popen(["notepad.exe"]), "Notepad"),
    "calculator": (lambda: subprocess.Popen(["calc.exe"]), "Calculator"),
    "calc": (lambda: subprocess.Popen(["calc.exe"]), "Calculator"),
    "explorer": (lambda: subprocess.Popen(["explorer.exe"]), "File Explorer"),
    "file explorer": (lambda: subprocess.Popen(["explorer.exe"]), "File Explorer"),
    "files": (lambda: subprocess.Popen(["explorer.exe"]), "File Explorer"),
    "paint": (lambda: subprocess.Popen(["mspaint.exe"]), "Paint"),
    "cmd": (lambda: subprocess.Popen(["cmd.exe"]), "Command Prompt"),
    "command prompt": (lambda: subprocess.Popen(["cmd.exe"]), "Command Prompt"),
    "vscode": (_launch_vscode, "VS Code"),
    "vs code": (_launch_vscode, "VS Code"),
    "code": (_launch_vscode, "VS Code"),
}


# Apps that have a perfectly good web version. When the desktop program is not
# installed, offering this is far more useful than asking the user for a path.
WEB_ALTERNATIVES = {
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "telegram": "https://web.telegram.org",
    "discord": "https://discord.com/app",
    "slack": "https://app.slack.com",
    "teams": "https://teams.microsoft.com",
    "outlook": "https://outlook.live.com",
    "word": "https://www.office.com/launch/word",
    "excel": "https://www.office.com/launch/excel",
    "powerpoint": "https://www.office.com/launch/powerpoint",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "youtube": "https://www.youtube.com",
    "netflix": "https://www.netflix.com",
    "figma": "https://www.figma.com",
    "notion": "https://www.notion.so",
    "trello": "https://trello.com",
}


def find_store_app(app_name):
    """Locate a Microsoft Store app by partial name.

    Store apps have no executable on PATH and no predictable install path, so
    neither ``which`` nor a file check finds them. They are launched through the
    shell's apps folder using their package family name.
    """
    query = str(app_name or "").strip()
    if not query:
        return None
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"Get-AppxPackage -Name '*{query}*' | "
        "Select-Object -First 1 -ExpandProperty PackageFamilyName"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return None
    family = (result.stdout or "").strip().splitlines()
    family = family[0].strip() if family else ""
    return family or None


def _launch_generic(app_name):
    """Launch an arbitrary application by name or path.

    Tries, in order: an executable on PATH, an existing file path, a Microsoft
    Store app, then the Windows shell, which resolves App Paths registry entries
    such as winword.
    """
    resolved = shutil.which(app_name)
    if resolved:
        subprocess.Popen([resolved])
        return

    if os.path.isfile(app_name):
        subprocess.Popen([app_name])
        return

    family = find_store_app(app_name)
    if family:
        subprocess.Popen(
            ["cmd", "/c", "start", "", f"shell:AppsFolder\\{family}!App"]
        )
        return

    # ``start`` needs an (empty) title argument first so a quoted target isn't
    # mistaken for the window title.
    result = subprocess.run(
        ["cmd", "/c", "start", "", app_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or f"Windows could not find '{app_name}'.")


def open_app(app_name):
    """
    Launches an application by name. Known shortcuts (chrome, notepad, etc.)
    use tailored launchers; anything else is resolved through the generic
    launcher, so Muno can open any installed application.

    Launches are confirmed by the user before they run, so opening arbitrary
    apps stays under explicit human control.

    Args:
        app_name: Name or path of the application to open.

    Returns:
        Success/failure message
    """
    raw = str(app_name or "").strip()
    if not raw:
        return "❌ No application name was provided."

    key = raw.lower()

    # Prefer an exact known shortcut, then a known keyword contained in the request.
    entry = KNOWN_APPS.get(key)
    if entry is None:
        for name, known in KNOWN_APPS.items():
            if name in key:
                entry = known
                break

    if entry is not None:
        launch, label = entry
        try:
            launch()
            return f"✅ Opened {label}"
        except Exception as e:
            return f"❌ Failed to open {label}: {str(e)}"

    # Generic path: launch any other application the user names.
    try:
        _launch_generic(raw)
        return f"✅ Opened {raw}"
    except Exception as e:
        # Not being installed is the common case, and it is far more useful to
        # say so and offer the web version than to ask the user for a file path
        # they are unlikely to know.
        for label, url in WEB_ALTERNATIVES.items():
            if label in key:
                try:
                    where = _open_in_browser(url)
                    return (
                        f"✅ {raw.title()} isn't installed on this computer, so I opened "
                        f"the web version in {where} instead."
                    )
                except Exception:
                    return (
                        f"❌ {raw.title()} isn't installed here. You can use it at {url}."
                    )
        return (
            f"❌ I couldn't find '{raw}' on this computer. It may not be installed. "
            f"({str(e)[:80]})"
        )


# ---------------------------------------------------------------------------
# Web navigation via URLs and deep links
#
# Navigating by URL is deterministic: it needs no screenshots, no pixel
# hunting, and no coordinate guessing, so it is the most reliable way for Muno
# to drive web apps.
# ---------------------------------------------------------------------------

# Only web schemes are accepted. Windows resolves many other URI schemes to
# local program handlers, so allowing arbitrary schemes here would effectively
# be another way to launch applications without review.
ALLOWED_URL_SCHEMES = {"http", "https"}

CONTACTS_FILE = os.path.join(os.path.expanduser("~/.codex_pet"), "contacts.json")


def _normalize_url(url):
    """Validate a URL and return it with an explicit web scheme.

    Raises ValueError when the scheme is not http/https.
    """
    candidate = str(url or "").strip()
    if not candidate:
        raise ValueError("No URL was provided.")

    # A bare domain like "youtube.com" has no scheme; default it to https
    # rather than letting urlparse treat the host as a scheme.
    if "://" not in candidate:
        if candidate.lower().startswith("mailto:") or ":" in candidate.split("/")[0]:
            scheme = candidate.split(":", 1)[0].lower()
            if scheme not in ALLOWED_URL_SCHEMES:
                raise ValueError(
                    f"The '{scheme}' scheme is not allowed. Only http and https URLs can be opened."
                )
        candidate = f"https://{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"The '{parsed.scheme}' scheme is not allowed. Only http and https URLs can be opened."
        )
    if not parsed.netloc:
        raise ValueError("That does not look like a valid web address.")
    return candidate


def _open_in_browser(url):
    """Open a validated URL, preferring Chrome so the user's session is reused."""
    chrome = _resolve_chrome()
    if chrome:
        subprocess.Popen([chrome, url])
        return "Chrome"
    webbrowser.open(url)
    return "your default browser"


def build_search_url(query):
    """Build a Google search URL for a plain-text query."""
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(str(query or "").strip())


def build_youtube_search_url(query):
    """Build a YouTube search-results URL for a plain-text query."""
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(
        str(query or "").strip()
    )


def open_url(url):
    """
    Opens a web address in the browser.

    Args:
        url: The http/https address to open.

    Returns:
        Success/failure message
    """
    try:
        target = _normalize_url(url)
    except ValueError as e:
        return f"❌ {e}"
    try:
        where = _open_in_browser(target)
        return f"✅ Opened {target} in {where}"
    except Exception as e:
        return f"❌ Failed to open the page: {str(e)}"


def web_search(query):
    """
    Searches the web (Google) for a query and opens the results.

    Args:
        query: What to search for.

    Returns:
        Success/failure message
    """
    text = str(query or "").strip()
    if not text:
        return "❌ No search terms were provided."
    try:
        where = _open_in_browser(build_search_url(text))
        return f"✅ Searched the web for '{text}' in {where}"
    except Exception as e:
        return f"❌ Failed to run the search: {str(e)}"


def youtube_search(query):
    """
    Opens YouTube search results for a query.

    Args:
        query: What to search YouTube for.

    Returns:
        Success/failure message
    """
    text = str(query or "").strip()
    if not text:
        return "❌ No search terms were provided."
    try:
        where = _open_in_browser(build_youtube_search_url(text))
        return f"✅ Opened YouTube results for '{text}' in {where}"
    except Exception as e:
        return f"❌ Failed to open YouTube: {str(e)}"


# ---------------------------------------------------------------------------
# Contacts and WhatsApp deep links
# ---------------------------------------------------------------------------

def _load_contacts():
    """Load the local contact book as ``name -> {phone, email}``.

    Older files stored a bare phone-number string per name, so those are
    upgraded on read rather than discarded.
    """
    try:
        with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
    except (OSError, json.JSONDecodeError):
        return {}

    contacts = {}
    for name, value in data.items():
        key = str(name).strip().lower()
        if not key:
            continue
        if isinstance(value, dict):
            entry = {
                "phone": str(value.get("phone", "")).strip(),
                "email": str(value.get("email", "")).strip(),
            }
        else:
            # Legacy format: the value was the phone number itself.
            entry = {"phone": str(value).strip(), "email": ""}
        if entry["phone"] or entry["email"]:
            contacts[key] = entry
    return contacts


def _save_contacts(contacts):
    """Write the contact book atomically."""
    os.makedirs(os.path.dirname(CONTACTS_FILE), exist_ok=True)
    temporary_file = f"{CONTACTS_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as f:
        json.dump(contacts, f, ensure_ascii=False, indent=2)
    os.replace(temporary_file, CONTACTS_FILE)


def lookup_contact_email(name):
    """Return a saved email address for a contact name, or None."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    contacts = _load_contacts()
    entry = contacts.get(key)
    if entry and entry.get("email"):
        return entry["email"]
    for contact_name, candidate in contacts.items():
        if (key in contact_name or contact_name in key) and candidate.get("email"):
            return candidate["email"]
    return None


def _normalize_phone(value):
    """Reduce a phone number to digits, as required by WhatsApp deep links."""
    return re.sub(r"\D", "", str(value or ""))


def resolve_contact(name):
    """Resolve a contact name or raw number to (digits, label).

    Returns (None, None) when the name is unknown.
    """
    raw = str(name or "").strip()
    if not raw:
        return None, None

    # A number typed directly needs no lookup.
    digits = _normalize_phone(raw)
    if digits and len(digits) >= 8 and not re.search(r"[a-zA-Z]", raw):
        return digits, raw

    contacts = _load_contacts()
    key = raw.lower()
    entry = contacts.get(key)
    if entry and entry.get("phone"):
        return _normalize_phone(entry["phone"]), raw
    for contact_name, candidate in contacts.items():
        if (key in contact_name or contact_name in key) and candidate.get("phone"):
            return _normalize_phone(candidate["phone"]), contact_name.title()
    return None, None


def build_whatsapp_uri(phone_digits, message=""):
    """Build a WhatsApp deep link that opens a chat with text pre-filled."""
    query = {"phone": phone_digits}
    if message:
        query["text"] = message
    return "whatsapp://send?" + urllib.parse.urlencode(query)


def build_whatsapp_web_url(phone_digits, message=""):
    """Build the wa.me fallback used when the desktop app is unavailable."""
    base = f"https://wa.me/{phone_digits}"
    if message:
        return base + "?" + urllib.parse.urlencode({"text": message})
    return base


def list_contacts():
    """
    Lists the saved contacts Muno can message.

    Returns:
        A readable list of contact names
    """
    contacts = _load_contacts()
    if not contacts:
        return (
            "No contacts are saved yet. Ask me to save one, for example: "
            "'save Sarah's number as +1 555 123 4567' or "
            "'save Sarah's email as sarah@example.com'."
        )
    lines = []
    for name in sorted(contacts):
        entry = contacts[name]
        details = []
        if entry.get("phone"):
            details.append("phone")
        if entry.get("email"):
            details.append(entry["email"])
        lines.append(f"- {name.title()} ({', '.join(details)})")
    return f"📇 Saved contacts ({len(contacts)}):\n" + "\n".join(lines)


def save_contact(name, phone_number=None, email=None):
    """
    Saves or updates a contact's phone number and/or email address locally.

    Args:
        name: The contact's name.
        phone_number: Their phone number, including country code.
        email: Their email address.

    Returns:
        Success/failure message
    """
    clean_name = str(name or "").strip()
    if not clean_name:
        return "❌ A contact name is required."

    digits = _normalize_phone(phone_number) if phone_number else ""
    address = str(email or "").strip()
    if phone_number and len(digits) < 8:
        return "❌ That number looks incomplete. Include the country code, e.g. +1 555 123 4567."
    if address and not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", address):
        return f"❌ '{address}' doesn't look like an email address."
    if not digits and not address:
        return "❌ Give me a phone number or an email address to save."

    contacts = _load_contacts()
    entry = contacts.get(clean_name.lower(), {"phone": "", "email": ""})
    if digits:
        entry["phone"] = digits
    if address:
        entry["email"] = address
    contacts[clean_name.lower()] = entry

    try:
        _save_contacts(contacts)
    except OSError as e:
        return f"❌ Failed to save the contact: {str(e)}"

    saved = []
    if digits:
        saved.append("phone number")
    if address:
        saved.append("email address")
    return f"✅ Saved {clean_name}'s {' and '.join(saved)}."


def whatsapp_message(contact, message=""):
    """
    Opens a WhatsApp chat with a contact and pre-fills a message.

    The message is typed into the chat but NOT sent. The user presses Enter to
    send it, so an outgoing message is never delivered without a human action.

    Args:
        contact: Contact name (from saved contacts) or a phone number.
        message: The message text to pre-fill.

    Returns:
        Success/failure message
    """
    digits, label = resolve_contact(contact)
    if not digits:
        return (
            f"❌ I don't have a number for '{contact}'. Save it first, for example: "
            f"'save {contact}'s number as +1 555 123 4567'."
        )

    text = str(message or "").strip()

    # Only use the desktop handler if the app is genuinely present. The
    # ``whatsapp:`` protocol often stays registered after an uninstall, so
    # trusting the registration alone opens nothing and reports success.
    if whatsapp_desktop_installed():
        try:
            os.startfile(build_whatsapp_uri(digits, text))
            if text:
                return (
                    f"✅ Opened a WhatsApp chat with {label} and typed the message. "
                    "Press Enter to send it."
                )
            return f"✅ Opened a WhatsApp chat with {label}"
        except Exception:
            pass  # Fall through to the web version.

    try:
        where = _open_in_browser(build_whatsapp_web_url(digits, text))
    except Exception as e:
        return f"❌ Failed to open WhatsApp: {str(e)}"
    if text:
        return (
            f"✅ WhatsApp isn't installed here, so I opened the chat with {label} in "
            f"{where} with the message typed in. Press Enter there to send it."
        )
    return f"✅ Opened a WhatsApp chat with {label} in {where}."


def whatsapp_desktop_installed():
    """True when the WhatsApp desktop application is actually present.

    Checked properly rather than relying on the ``whatsapp:`` protocol being
    registered, which survives an uninstall and would make a missing app look
    available.
    """
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "WhatsApp", "WhatsApp.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "WhatsApp", "WhatsApp.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "WhatsApp", "WhatsApp.exe"),
    ]
    if any(path and os.path.isfile(path) for path in candidates):
        return True
    return bool(find_store_app("WhatsApp"))


def whatsapp_send(contact, message):
    """
    Sends a WhatsApp message through WhatsApp Web in Muno's controllable browser.

    Unlike whatsapp_message, this actually delivers the message. It requires a
    one-time QR sign-in in Muno's browser profile.

    Args:
        contact: Contact name (from saved contacts) or a phone number.
        message: The message to send.

    Returns:
        Success/failure message
    """
    digits, label = resolve_contact(contact)
    if not digits:
        return (
            f"❌ I don't have a number for '{contact}'. Save it first, for example: "
            f"'save {contact}'s number as +1 555 123 4567'."
        )
    text = str(message or "").strip()
    if not text:
        return "❌ There is no message to send."

    result = browser_control.whatsapp_web_send(digits, text, confirm_send=True)
    # Report using the friendly name the user recognises.
    return result.replace(f"+{digits}", label) if label else result


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
            "description": "Opens any desktop application by name or path. Common apps like Chrome, Notepad, Calculator, File Explorer, Paint, Command Prompt, and VS Code have built-in shortcuts; any other installed program (e.g. 'spotify', 'winword', 'discord', or a full .exe path) is also supported. The user confirms each launch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name or path of the application to open (e.g., 'chrome', 'spotify', 'winword', or 'C:\\\\Tools\\\\app.exe')"
                    }
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Opens a specific web page or website in the browser. Use this when the user names a site or gives a link, e.g. 'open youtube', 'go to github.com'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The web address to open (e.g. 'youtube.com' or 'https://news.ycombinator.com')"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_and_read",
            "description": (
                "Searches the web AND reads the results back, so you can answer the "
                "question yourself. Use this for any question of fact: weather, prices, "
                "news, scores, opening hours, who or what something is. Prefer this over "
                "web_search, which only opens a tab and tells the user nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "Opens a specific web page and returns its text so you can answer questions about it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The page to read"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Opens a search results page in the user's browser for them to look at "
                "themselves. Does NOT read the results. Only use when the user explicitly "
                "wants to browse; for answering a question use search_and_read instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": "Opens YouTube search results for a query. Use when the user wants to find or watch a video, e.g. 'find lo-fi beats on youtube'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search YouTube for"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "whatsapp_message",
            "description": "Opens a WhatsApp chat with a contact and pre-fills a message for the user to send. Use when the user wants to message someone on WhatsApp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "string",
                        "description": "The contact's name (from saved contacts) or their phone number"
                    },
                    "message": {
                        "type": "string",
                        "description": "The message text to pre-fill in the chat"
                    }
                },
                "required": ["contact"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "whatsapp_send",
            "description": "Actually sends a WhatsApp message to a contact through WhatsApp Web. Use this when the user clearly wants the message delivered rather than just drafted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "string",
                        "description": "The contact's name (from saved contacts) or their phone number"
                    },
                    "message": {
                        "type": "string",
                        "description": "The message to send"
                    }
                },
                "required": ["contact", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_play",
            "description": "Searches YouTube and starts playing one of the results. Use when the user wants to watch or play something, e.g. 'play lo-fi beats' or 'play the second result'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search YouTube for"
                    },
                    "index": {
                        "type": "integer",
                        "description": "Which result to play, 1 for the first result (default 1)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Opens a web page in the browser Muno can control, so it can then read or click things on that page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The web address to open"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": "Reads the visible text of the page currently open in the controllable browser. Use this to answer questions about what is on screen in the browser.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_links",
            "description": "Lists the links on the current page, so the user can choose one by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many links to list (default 15)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Clicks a link or button on the current page by its visible text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The visible text of the link or button to click"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Types text into the main input or search box on the current page, and submits it by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to type"
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "Whether to press Enter after typing (default true)"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_status",
            "description": "Checks whether the browser Muno can control is running.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_contacts",
            "description": "Lists the saved contacts that Muno can message on WhatsApp.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_contact",
            "description": "Saves or updates a contact's phone number and/or email address so Muno can reach them later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The contact's name"
                    },
                    "phone_number": {
                        "type": "string",
                        "description": "Their phone number including country code, e.g. '+1 555 123 4567'"
                    },
                    "email": {
                        "type": "string",
                        "description": "Their email address"
                    }
                },
                "required": ["name"]
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
            "name": "maximize_window",
            "description": "Maximizes a window by matching part of its title.",
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

# GitHub and local file lookup schemas are generated from a single table in
# github_api, so the schema and the callables cannot drift apart.
TOOLS_SCHEMA.extend(github_api.TOOLS_SCHEMA)

TOOLS_SCHEMA.extend([
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Sends an email, optionally attaching files from this computer. Write the "
                "subject and body yourself: a clear specific subject, a natural greeting, "
                "short readable paragraphs, and a sign-off. Do not use placeholders like "
                "[Name]; use what you know or leave it out."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address or saved contact name; several may be separated by commas"
                    },
                    "subject": {"type": "string", "description": "The subject line"},
                    "body": {"type": "string", "description": "The full message text"},
                    "attachments": {
                        "type": "string",
                        "description": "Optional file names or paths to attach, separated by commas"
                    },
                    "cc": {"type": "string", "description": "Optional addresses to copy"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_email",
            "description": "Lists recent or unread messages in the user's mailbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many messages to list"},
                    "unread_only": {"type": "boolean", "description": "Only unread messages (default true)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "email_status",
            "description": "Checks how Muno will send email and what it is signed in with.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_email_identity",
            "description": "Sets which email address Muno sends from. Stores no password.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "The email address to send from"}
                },
                "required": ["address"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "capability_report",
            "description": (
                "Explains what Muno can and cannot currently do. Use this FIRST when asked "
                "for something that might not be possible yet, before saying no."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_reference_examples",
            "description": (
                "Looks up how working open-source AI apps accomplish a task, and which "
                "libraries they use for it. Use when you need to work out what would "
                "provide a missing ability. Reads text only and installs nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "need": {
                        "type": "string",
                        "description": "The ability, e.g. 'read PDF files' or 'scrape a website'"
                    }
                },
                "required": ["need"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_capability",
            "description": (
                "Researches what would give Muno a missing ability and how trustworthy each "
                "option is. Installs nothing. Use when asked to do something Muno cannot yet "
                "do, to work out what would make it possible. Suggest candidate Python "
                "package names yourself based on what the task needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "need": {
                        "type": "string",
                        "description": "The missing ability, e.g. 'read PDF files'"
                    },
                    "candidates": {
                        "type": "string",
                        "description": "Comma-separated Python package names that might provide it"
                    }
                },
                "required": ["need"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "install_capability",
            "description": (
                "Installs a vetted Python package so Muno gains a new ability. Only use after "
                "research_capability, and only when the user has agreed to that specific "
                "package. The user is asked to confirm before it runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "The PyPI package name"},
                    "version": {
                        "type": "string",
                        "description": "Exact version to pin; blank uses the newest"
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What this ability is for, recorded with it"
                    },
                    "module": {
                        "type": "string",
                        "description": "Import name if it differs from the package name"
                    }
                },
                "required": ["package"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_capabilities",
            "description": "Lists the extra abilities Muno has installed for itself.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "uninstall_capability",
            "description": "Removes an ability Muno previously installed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The package name to remove"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_history",
            "description": (
                "Shows what Muno has actually done in the past, with when it happened and "
                "the request that caused it. Use for 'what have you done', 'what did you do "
                "today', or 'show my history'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many actions to list"},
                    "days": {"type": "integer", "description": "Only the last N days"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_if_done",
            "description": (
                "Checks the record for whether something was already done before, to avoid "
                "repeating work. Use for 'did you already do X', 'have we done this'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What to look for, e.g. 'create repo my-report'"
                    }
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "journal_status",
            "description": "Reports how much task history Muno holds and which session this is.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "watch_screen_region",
            "description": (
                "Call this whenever the user asks you to watch their screen, watch an area, "
                "or click or approve something for them repeatedly. It opens an overlay on "
                "their own machine so they can point at the area to watch and the button to "
                "press. Call it immediately: do not describe the steps, do not ask them to "
                "move their mouse first, and do not say you are unable to. You cannot pick "
                "the coordinates yourself, which is exactly why this tool exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": "string",
                        "description": "What is being automated, e.g. 'Kiro approval prompts'"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_into_field",
            "description": (
                "Types text into a box, form field, or text area that the user points at. "
                "Use when they want something written somewhere on screen, whether they "
                "dictate the words or just describe what they need. YOU write the finished "
                "text and pass it in: if they say 'write a short description of this task' "
                "then compose that description yourself. Never pass placeholders, never ask "
                "them to type it. They point at the field and confirm before anything is typed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The complete, finished text to type into the field"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_screen_watches",
            "description": "Lists the screen areas being watched and what each has clicked.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_screen_watch",
            "description": "Stops watching a screen area, or all of them if no name is given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Which watch to stop; blank for all"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_activity",
            "description": (
                "Describes what the user is doing right now, based on the active window and "
                "how long they have been on it. Use when asked 'what am I doing', 'what am I "
                "working on', or when checking in on their task."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_open_apps",
            "description": "Lists the applications and windows currently open on the computer.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "activity_summary",
            "description": "Summarizes how the user has spent recent time across activities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "How far back to summarize (default 60)"}
                },
                "required": []
            }
        }
    },
])


# Map function names to actual functions. Every entry here must have a matching
# entry in TOOLS_SCHEMA so the exposed surface and the callable surface stay in
# sync. Read-only/mutating helpers that are not in the schema are intentionally
# omitted so the model cannot invoke them.
TOOL_FUNCTIONS = {
    "open_app": open_app,
    "open_url": open_url,
    "web_search": web_search,
    "youtube_search": youtube_search,
    "whatsapp_message": whatsapp_message,
    "whatsapp_send": whatsapp_send,
    "list_contacts": list_contacts,
    "save_contact": save_contact,
    "youtube_play": browser_control.youtube_play,
    "browser_open": browser_control.browser_open,
    "browser_read": browser_control.browser_read,
    "browser_links": browser_control.browser_links,
    "browser_click": browser_control.browser_click,
    "browser_type": browser_control.browser_type,
    "browser_status": browser_control.browser_status,
    "search_and_read": browser_control.search_and_read,
    "read_page": browser_control.read_page,
    "send_email": email_api.send_email,
    "check_email": email_api.check_email,
    "email_status": email_api.email_status,
    "set_email_identity": email_api.set_identity,
    "capability_report": capabilities.capability_report,
    "find_reference_examples": recipes.find_reference_examples,
    "research_capability": capabilities.research_capability,
    "install_capability": capabilities.install_capability,
    "list_capabilities": capabilities.list_capabilities,
    "uninstall_capability": capabilities.uninstall_capability,
    "task_history": journal.task_history,
    "check_if_done": journal.check_if_done,
    "journal_status": journal.journal_status,
    "watch_screen_region": watcher.watch_screen_region,
    "write_into_field": watcher.write_into_field,
    "list_screen_watches": watcher.list_screen_watches,
    "stop_screen_watch": watcher.stop_screen_watch,
    "check_activity": activity.check_activity,
    "list_open_apps": activity.list_open_apps,
    "activity_summary": activity.activity_summary,
    **github_api.TOOL_FUNCTIONS,
    "type_text": type_text,
    "click_mouse": click_mouse,
    "press_key": press_key,
    "hotkey": lambda keys: hotkey(*keys),
    "list_windows": list_windows,
    "focus_window": focus_window,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "take_screenshot": take_screenshot,
    "system_sleep": system_sleep,
    "system_mute": system_mute,
    "system_exit": system_exit
}


# ---------------------------------------------------------------------------
# Tool routing
#
# Sending every tool on every turn is expensive: the full schema is around 5,000
# tokens, which on a modest rate limit leaves little room for the actual
# conversation and triggers throttling after a request or two. Fewer, more
# relevant tools also means fewer distractors for the model to choose between.
#
# So each turn sends a core set plus the groups the message actually implies.
# ---------------------------------------------------------------------------

_SCHEMA_BY_NAME = {entry["function"]["name"]: entry for entry in TOOLS_SCHEMA}

# Always available: cheap, broadly useful, and needed for companion behaviour.
_CORE_TOOLS = [
    # search_and_read comes before web_search deliberately: answering a question
    # is almost always what is wanted, and opening a tab the user must read
    # themselves is the rarer case.
    "open_app", "search_and_read", "open_url", "web_search", "youtube_search",
    "system_sleep", "system_mute", "system_exit",
]

_TOOL_GROUPS = {
    # GitHub is split into narrower groups. Loading all 25 GitHub tools for a
    # simple "create a repo" both wastes tokens and gives the model two dozen
    # irrelevant options to weigh.
    "github_repos": (
        ("github", "repo", "repository", "repos", "readme", "fork", "clone",
         "version control", "upload", "push", "connect", "sign in", "link"),
        ["github_status", "github_connect", "github_create_repo", "github_list_repos",
         "github_repo_info",
         "github_update_repo", "github_delete_repo", "github_search_repos",
         "github_upload_file", "github_read_file", "github_list_files",
         "github_delete_file"],
    ),
    "github_issues": (
        # "report" is deliberately absent: it collides with ordinary phrases like
        # "email the report", which has nothing to do with issue tracking.
        ("issue", "issues", "bug", "ticket", "backlog"),
        ["github_create_issue", "github_list_issues", "github_comment_issue",
         "github_close_issue"],
    ),
    "github_code": (
        ("pull request", "pr ", "branch", "merge", "commit", "commits", "diff"),
        ["github_list_branches", "github_create_branch", "github_create_pull_request",
         "github_list_pull_requests", "github_merge_pull_request", "github_list_commits"],
    ),
    "github_actions": (
        ("workflow", "workflows", "actions", "release", "deploy", "ci ", "pipeline",
         "build passed", "build failed"),
        ["github_list_workflows", "github_run_workflow", "github_list_workflow_runs",
         "github_create_release"],
    ),
    "files": (
        ("file", "files", "folder", "directory", "pdf", "document", "docx", "spreadsheet",
         "find", "locate", "where is", "path", "index", "upload", "attach", "photo",
         "image", "download"),
        ["find_file", "refresh_file_index", "file_index_status"],
    ),
    "browser": (
        ("browser", "page", "website", "site", "link", "links", "click", "read",
         "youtube", "video", "watch", "play", "search", "tab", "scroll", "form"),
        ["browser_status", "browser_open", "browser_read", "browser_links",
         "browser_click", "browser_type", "youtube_play", "search_and_read",
         "read_page"],
    ),
    "facts": (
        ("weather", "forecast", "temperature", "rain", "price", "cost", "news",
         "score", "who is", "what is", "when is", "where is", "how much",
         "how many", "look up", "find out", "tell me about", "latest", "today",
         "tomorrow", "this week", "open at", "opening hours"),
        ["search_and_read", "read_page", "browser_open"],
    ),
    "whatsapp": (
        ("whatsapp", "message", "text ", "texting", "chat", "tell ",
         "contact", "contacts", "number", "reply"),
        ["whatsapp_message", "whatsapp_send", "list_contacts", "save_contact"],
    ),
    "email": (
        ("email", "e-mail", "mail", "inbox", "gmail", "outlook", "compose",
         "send a note", "cc", "subject line", "unread", "draft"),
        ["send_email", "check_email", "email_status", "set_email_identity",
         "list_contacts", "save_contact", "find_file"],
    ),
    "capabilities": (
        ("can you", "can't you", "cannot", "unable", "able to", "capability",
         "capable", "install", "extension", "plugin", "package", "library",
         "add the ability", "learn to", "teach yourself", "improve yourself",
         "upgrade yourself", "pdf", "spreadsheet", "calendar"),
        ["capability_report", "find_reference_examples", "research_capability",
         "install_capability", "list_capabilities", "uninstall_capability"],
    ),
    "journal": (
        ("history", "already", "again", "did you", "have you", "have we", "previously",
         "before", "last time", "record", "log", "journal", "repeat", "duplicate",
         "what did you do", "what have you done"),
        ["task_history", "check_if_done", "journal_status"],
    ),
    "screen_watch": (
        ("watch", "watching", "monitor", "approve", "approval", "approvals",
         "click for me", "auto click", "autoclick", "every time", "repeatedly",
         "keep clicking", "select part of the screen", "region", "area of the screen",
         "kiro", "prompt me", "prompts", "confirm for me", "do it for me"),
        ["watch_screen_region", "list_screen_watches", "stop_screen_watch"],
    ),
    "write_field": (
        ("write", "type", "fill in", "fill out", "put that in", "enter it",
         "compose", "draft", "description", "text field", "text area", "form",
         "textbox", "input", "caption", "comment", "reply", "answer it",
         "write it for me", "write something"),
        ["write_into_field", "type_text"],
    ),
    "activity": (
        ("what am i doing", "what i'm doing", "what im doing", "working on",
         "open apps", "open applications", "what's open", "whats open",
         "productive", "focus", "distracted", "how long", "time spent",
         "still working", "my task", "activity", "busy"),
        # list_windows is deliberately excluded: list_open_apps answers the same
        # question with process names and grouping, and offering both makes the
        # model pick arbitrarily between them.
        ["check_activity", "list_open_apps", "activity_summary"],
    ),
    "desktop": (
        ("type", "click", "press", "key", "shortcut", "window", "minimize",
         "maximize", "screenshot", "focus", "switch to"),
        ["type_text", "click_mouse", "press_key", "hotkey", "list_windows",
         "focus_window", "minimize_window", "maximize_window", "take_screenshot"],
    ),
}

# When nothing specific is implied, offer a useful spread without the long tail.
_DEFAULT_EXTRA = [
    "find_file", "browser_open", "browser_read", "youtube_play",
    "take_screenshot", "list_open_apps", "check_activity", "github_status",
]


def _compile_triggers(triggers):
    """Build a word-boundary matcher for a group's trigger phrases.

    Plain substring matching produces false positives that quietly cost tokens:
    "report" contains "repo", and "unread" contains "read", so an email request
    would drag in the GitHub and browser tool sets. Word boundaries avoid that
    while still matching multi-word phrases.
    """
    parts = sorted((t.strip() for t in triggers if t.strip()), key=len, reverse=True)
    pattern = "|".join(re.escape(part) for part in parts)
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)


_GROUP_MATCHERS = {
    group: (_compile_triggers(triggers), names)
    for group, (triggers, names) in _TOOL_GROUPS.items()
}


def select_tools(user_text, max_tools=32):
    """Return the schema subset worth sending for one message.

    Matched groups are interleaved rather than concatenated. Filling the budget
    in group order looks fine until a request mentions two things at once: a
    large group listed first would consume the whole allowance and a later
    group's tools would silently never be sent, so the model would insist it
    could not do something it can in fact do. Taking one tool from each matched
    group in turn guarantees every recognised intent is represented, and because
    each group lists its most important tool first, what survives is the part
    that matters.
    """
    text = str(user_text or "")

    matched = [
        list(names) for (matcher, names) in _GROUP_MATCHERS.values()
        if matcher.search(text)
    ]
    if not matched:
        matched = [list(_DEFAULT_EXTRA)]

    seen = set()
    ordered = []

    def take(name):
        if name in seen or name not in _SCHEMA_BY_NAME:
            return False
        seen.add(name)
        ordered.append(name)
        return True

    # The core set is always available.
    for name in _CORE_TOOLS:
        take(name)

    # Round-robin across the matched groups until the budget runs out.
    depth = 0
    longest = max(len(group) for group in matched)
    while depth < longest and len(ordered) < max_tools:
        for group in matched:
            if depth < len(group):
                take(group[depth])
                if len(ordered) >= max_tools:
                    break
        depth += 1

    return [_SCHEMA_BY_NAME[name] for name in ordered]
