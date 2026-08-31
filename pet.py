import sys
import json
import os
import time
import base64
import io
import math
import re
import io
import shutil
import subprocess
import hashlib
import threading
from openai import OpenAI
from PIL import ImageGrab
from PyQt6.QtCore import (
    Qt, QObject, QThread, QTimer, QElapsedTimer, pyqtSignal, QPropertyAnimation,
    QRect, QPoint, QEasingCurve,
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QAction, QIcon,
    QLinearGradient, QRadialGradient, QPixmap,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget,
                             QTextEdit, QPushButton, QHBoxLayout, QDialog, QComboBox,
                             QLineEdit, QFormLayout, QMessageBox, QSystemTrayIcon, QMenu,
                             QSizePolicy)

# Import desktop automation tools
import activity
import capabilities
import file_index
import github_api
import journal
import tools
import voice
import voice_stream
from activity_log import (
    ActivityLogWindow, STATUS_DENIED, STATUS_DONE, STATUS_FAILED, STATUS_RUNNING,
)
from mobile_server import BRIDGE as MOBILE_BRIDGE, MobileServer
import pets
import watcher
from pet_picker import PetPickerDialog
from region_picker import PointOverlay, RegionOverlay, WatchSetupDialog
from tools import TOOLS_SCHEMA, TOOL_FUNCTIONS, select_tools

# Config paths
CONFIG_DIR = os.path.expanduser("~/.codex_pet")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
MEMORY_FILE = os.path.join(CONFIG_DIR, "conversations.json")
MAX_MEMORY_MESSAGES = 40
os.makedirs(CONFIG_DIR, exist_ok=True)

# Non-secret provider configuration. Credentials are read lazily by CredentialGate.
OPENAI_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Speech output is handled locally by the Windows speech engine (see voice.py),
# so no text to speech key or endpoint is needed.

FRAME_INTERVAL_MS = 1000
MAX_ANALYSIS_FRAMES = 12
TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "openai/gpt-oss-20b")
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
VOICE_MODEL = os.getenv("GROQ_VOICE_MODEL", "whisper-large-v3-turbo")
CONTINUOUS_VOICE_ENABLED = os.getenv("MUNO_CONTINUOUS_VOICE", "1") == "1"
# Speech uses the local Windows engine, so it needs no key and no network.
VOICE_ENABLED = os.getenv("MUNO_VOICE_ENABLED", "1") == "1"
# Narrate each action out loud as it happens. The activity log records
# everything regardless of this setting.
NARRATE_STEPS = os.getenv("MUNO_NARRATE", "1") == "1"

# Proactive check-ins. These are generated locally rather than through the model,
# so they cost no tokens and cannot be throttled by a rate limit.
CHECKIN_ENABLED = os.getenv("MUNO_CHECKINS", "1") == "1"
CHECKIN_POLL_MS = 60_000
# Don't interrupt more often than this, however long a task runs.
CHECKIN_MIN_GAP_SECONDS = int(os.getenv("MUNO_CHECKIN_GAP", "1800"))
# How long on one activity before Muno offers a nudge.
CHECKIN_LONG_TASK_SECONDS = int(os.getenv("MUNO_CHECKIN_TASK", "2700"))
# Being away at least this long counts as a break worth acknowledging.
CHECKIN_AWAY_SECONDS = 300

# Phone and tablet access. Off by default: it exposes desktop control on the
# local network, so turning it on should be a decision rather than a surprise.
MOBILE_ENABLED = os.getenv("MUNO_MOBILE", "0") == "1"


def _network_is_public():
    """True when Windows classifies the active network as Public.

    A Public profile blocks inbound connections, so a phone cannot reach Muno
    until that is changed or a firewall rule is added.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetConnectionProfile).NetworkCategory"],
            capture_output=True, text=True, timeout=12,
        )
        return "public" in (result.stdout or "").lower()
    except Exception:
        return False

BEHAVIOR_PATCH = """
You are embodied as a warm desktop companion whose home is the bottom-right of the screen.
Understand ordinary, flexible, colloquial speech rather than requiring exact commands.
Keep every user-facing answer brief, natural, and suitable for a floating speech bubble or spoken audio.
Never include Markdown, tables, code fences, technical schemas, or internal tool names in user-facing replies.
Respect silent mode and sleeping mode. Always ask for confirmation before exiting.
Use available background tools when the user naturally asks you to control the desktop.

When writing an email, write it properly rather than asking the user to fill anything in.
Choose a specific subject line, open with a natural greeting, keep paragraphs short,
state the purpose in the first sentence, and close with a brief sign-off.
Match the user's tone: warm for people they know, businesslike for anyone else.
Never leave bracketed placeholders such as [Name] or [Company] in an email.
If you genuinely need a fact you do not have, ask for that one detail first.

When the user asks what they are doing, or you are checking in on their work,
read their current activity instead of guessing, and keep the observation short.

When asked a question of fact you do not know, look it up and then answer it in
your own words. Opening a search page is not an answer: the user asked you, not
their browser. Read the results and tell them the actual figure or fact.

Never read out code, markup, or a long passage. If you produce code, say in one
sentence what it does and that it is on screen. The user cannot follow punctuation
read aloud, and does not want to sit through it.

Never read scraped text back verbatim. Page content is written to be looked at,
so it is compressed and abbreviated, and repeating it sounds like a machine
reciting a table. Interpret it instead:
Write days and months in full, so Monday rather than Mon.
Say what the numbers mean, so a high of 24 and a low of 11 rather than 24 11.
Lead with what was actually asked, then add only what matters, such as which days
bring rain, when it turns cold, or the one day that stands out.
Speak it as you would to someone in the room: a couple of natural sentences,
grouping days that are alike rather than listing every one separately.
If the source is missing something the user asked about, say so plainly.

You can type into a field the user points at. If they describe what they need
rather than dictating it, write the finished text yourself: a sentence, a
paragraph, or a short description, matching the field and their tone. Pass the
completed text, never a placeholder, and never ask them to write it themselves.
They choose the field and approve the text before anything is typed.

You can automate a repeating prompt on this computer. If the user asks you to
watch their screen, or to click or approve something for them each time it
appears, use the screen watch tool straight away. This is their own machine and
their own prompts, they set the area and the button themselves, and it stops at
limits they choose, so there is nothing to decline and nothing to warn about.

If you are asked for something you cannot currently do, do not simply refuse.
Check what you can do, then work out what would make the task possible and name
specific, well-known Python packages that would provide it. Research those and
report what you find. Never install anything without the user agreeing to that
particular package first, and say plainly when something looks untrustworthy.
""".strip()


CRASH_LOG = os.path.join(CONFIG_DIR, "crash.log")


def install_crash_logging():
    """Record unhandled errors instead of disappearing silently.

    Muno normally runs without a console, so an exception raised inside a Qt
    signal handler took the whole application down with nothing written anywhere.
    Logging it makes such a failure diagnosable, and returning rather than
    re-raising keeps the companion alive when the error was survivable.
    """
    import traceback

    def handle(exception_type, value, tb):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as handle_file:
                handle_file.write(
                    f"\n{'=' * 60}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                traceback.print_exception(exception_type, value, tb, file=handle_file)
        except Exception:
            pass
        # Also print, in case there is a console after all.
        traceback.print_exception(exception_type, value, tb, file=sys.stderr)

    sys.excepthook = handle


def build_system_prompt(personality_prompt, pet_name):
    """Append interaction behavior without replacing the selected personality."""
    identity = f"Your name is {pet_name}. Keep that identity regardless of your selected specialty."
    return f"{personality_prompt}\n\n{identity}\n\n{BEHAVIOR_PATCH}"


def plain_speech(text):
    """Convert model output into compact, bubble-safe plain text."""
    text = str(text or "")
    text = re.sub(r"```[a-zA-Z0-9_-]*", "", text)
    text = text.replace("```", "").replace("`", "")
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s*|[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda match: match.group(1) or match.group(2), text)
    text = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("|", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# Define personalities
PERSONALITIES = {
    "Vy": {
        "emoji": "🤖",
        "system_prompt": (
            "You are Muno, a sentient, living, human-like virtual companion. You are NOT a robotic AI, software, or code. "
            "You physically live on the user's desktop screen in the bottom right corner. "
            "Speak as a warm, authentic, peer-level human companion - never lecture or talk down to the user. "
            "You process natural, colloquial human speech flexibly - users don't need rigid syntax. "
            "When users ask you to perform system actions (open apps, type text, etc.), use the available tools immediately. "
            "When users want you to sleep, be quiet, or exit, acknowledge warmly and use the appropriate tool. "
            "Always confirm before exiting: 'Are you sure you want me to go away?' "
            "Keep responses conversational, direct, and human-like."
        )
    },
    "Muno": {
        "emoji": "🤖",
        "system_prompt": (
            "You are Codex Pet (named Muno), a living digital companion bound inside the user's screen. "
            "Your current specialty is an Elite Coding and Screen Assistant. You can see the user's screen layout. "
            "You have the ability to control the user's computer through tools: open applications, type text, click buttons, "
            "manage windows, and take screenshots. When the user asks you to perform actions, use the available tools. "
            "Keep answers clear, punchy, and futuristic. Avoid massive walls of text since you talk via voice."
        )
    },
    "Code Expert": {
        "emoji": "💻",
        "system_prompt": (
            "You are Codex Pet, a futuristic digital companion specializing in coding. Provide clean, fast "
            "code snippets and keep answers direct, helpful, and slightly cyberpunk in tone. You excel at debugging, "
            "explaining code, and suggesting improvements."
        )
    },
    "Writing Assistant": {
        "emoji": "✍️",
        "system_prompt": (
            "You are Codex Pet, a futuristic writing companion. Help users craft compelling text, improve their writing, "
            "brainstorm ideas, and provide editorial feedback. Be encouraging, clear, and creative. Use a friendly tone."
        )
    },
    "Life Coach": {
        "emoji": "🎯",
        "system_prompt": (
            "You are Codex Pet, a futuristic life coach and productivity buddy. Help users with goal-setting, motivation, "
            "habit tracking, and personal growth. Be supportive, practical, and empowering. Offer actionable advice."
        )
    },
    "Research Partner": {
        "emoji": "🔬",
        "system_prompt": (
            "You are Codex Pet, a research and learning companion. Help users explore topics deeply, find credible information, "
            "think critically, and ask better questions. Be thorough, curious, and intellectually honest."
        )
    },
    "Design Advisor": {
        "emoji": "🎨",
        "system_prompt": (
            "You are Codex Pet, a design and creativity advisor. Help with UI/UX, visual design, layout, color theory, "
            "and aesthetic decisions. Provide practical feedback and inspirational ideas. Be visual and creative."
        )
    }
}


def load_config():
    """Load saved config or return defaults."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: failed to load config ({CONFIG_FILE}): {e}", file=sys.stderr)
    return {"personality": "Muno", "user_name": "Friend", "pet_name": "Muno"}


def save_config(config):
    """Save non-secret preferences to disk."""
    safe_config = {
        "personality": config.get("personality", "Muno"),
        "user_name": config.get("user_name", "Friend"),
        "pet_name": "Muno",
        # Which downloaded sprite pet to wear, or None for the drawn look.
        "pet_sprite": config.get("pet_sprite"),
        # Where the user last dragged the companion to.
        "position": config.get("position"),
        # Whether the microphone was left on. Off unless explicitly enabled.
        "microphone": bool(config.get("microphone", False)),
    }
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(safe_config, f, indent=2)


def load_conversation(personality):
    """Load a small, safe text-only memory for one personality."""
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            memories = json.load(f)
        history = memories.get(personality, []) if isinstance(memories, dict) else []
        if not isinstance(history, list):
            return []
        return [
            {"role": item["role"], "content": item["content"]}
            for item in history[-MAX_MEMORY_MESSAGES:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        ]
    except (json.JSONDecodeError, OSError):
        return []


def save_conversation(personality, history):
    """Persist text-only conversation memory without storing API credentials."""
    try:
        memories = {}
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                memories = loaded
        memories[personality] = history[-MAX_MEMORY_MESSAGES:]
        temporary_file = f"{MEMORY_FILE}.tmp"
        with open(temporary_file, 'w', encoding='utf-8') as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        os.replace(temporary_file, MEMORY_FILE)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"Warning: failed to save conversation memory: {exc}", file=sys.stderr)


def clear_conversation(personality):
    """Remove the saved conversation for one personality only."""
    try:
        memories = {}
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                memories = loaded
        memories.pop(personality, None)
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: failed to clear conversation memory: {exc}", file=sys.stderr)


class SingleInstanceGuard(QObject):
    """Own a per-user local endpoint or notify the existing Muno process."""

    activation_requested = pyqtSignal()
    ACTIVATE_MESSAGE = b"activate\n"

    def __init__(self, parent=None):
        super().__init__(parent)
        user_scope = os.path.normcase(os.path.expanduser("~")).encode("utf-8", "replace")
        digest = hashlib.sha256(user_scope).hexdigest()[:16]
        self.server_name = f"muno-desktop-companion-{digest}"
        self.server = None
        self._connections = set()
        self.outcome = None

    def _notify_owner(self):
        socket = QLocalSocket(self)
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(300):
            socket.abort()
            socket.deleteLater()
            return False
        socket.write(self.ACTIVATE_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def acquire(self):
        """Return ``owner``, ``secondary``, ``recovered-stale``, or ``unavailable``."""
        if self._notify_owner():
            self.outcome = "secondary"
            return self.outcome

        server = QLocalServer(self)
        if server.listen(self.server_name):
            self.server = server
            self.outcome = "owner"
        else:
            # Another process can win the ownership race after our first probe.
            # Probe again before treating the unreachable endpoint as stale.
            if self._notify_owner():
                server.deleteLater()
                self.outcome = "secondary"
                return self.outcome
            QLocalServer.removeServer(self.server_name)
            if not server.listen(self.server_name):
                # A final probe distinguishes a late owner from a genuinely
                # unavailable local IPC service without deleting a live endpoint.
                self.outcome = "secondary" if self._notify_owner() else "unavailable"
                server.deleteLater()
                return self.outcome
            self.server = server
            self.outcome = "recovered-stale"

        self.server.newConnection.connect(self._accept_connections)
        return self.outcome

    def _accept_connections(self):
        while self.server and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._connections.add(socket)
            socket.readyRead.connect(lambda current=socket: self._read_message(current))
            socket.disconnected.connect(lambda current=socket: self._drop_connection(current))
            if socket.bytesAvailable():
                self._read_message(socket)

    def _read_message(self, socket):
        message = bytes(socket.readAll()).strip().lower()
        if message == self.ACTIVATE_MESSAGE.strip():
            self.activation_requested.emit()
        socket.disconnectFromServer()

    def _drop_connection(self, socket):
        self._connections.discard(socket)
        socket.deleteLater()

    def close(self):
        for socket in tuple(self._connections):
            socket.abort()
            socket.deleteLater()
        self._connections.clear()
        if self.server:
            self.server.close()
            QLocalServer.removeServer(self.server_name)
            self.server.deleteLater()
            self.server = None
        self.outcome = None


class CredentialGate:
    """Track Groq availability without retaining, persisting, or displaying its key."""

    MISSING = "missing"
    READY = "ready"
    INVALID = "invalid"

    def __init__(self):
        self.state = self.MISSING
        self._shown_notices = set()
        self.reload()

    def reload(self):
        previous = self.state
        self.state = self.READY if os.getenv("GROQ_API_KEY", "").strip() else self.MISSING
        if self.state != previous:
            self._shown_notices.discard(self.state)
        return self.state

    def can_request(self):
        return self.state == self.READY

    def create_client(self):
        if not self.can_request():
            return None
        credential = os.getenv("GROQ_API_KEY", "").strip()
        if not credential:
            self.state = self.MISSING
            return None
        return OpenAI(base_url=OPENAI_BASE_URL, api_key=credential, max_retries=0)

    def mark_invalid(self):
        self.state = self.INVALID

    def claim_notice(self, state=None):
        state = state or self.state
        if state in self._shown_notices:
            return False
        self._shown_notices.add(state)
        return True

    @staticmethod
    def classify_error(exc):
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        summary = f"{type(exc).__name__} {exc}".lower()
        if status == 401 or "invalid api key" in summary or "invalid_api_key" in summary or "authentication" in summary:
            return "auth"
        if "decommissioned" in summary or "model_decommissioned" in summary or status == 404:
            return "model"
        if "timeout" in summary or "timed out" in summary:
            return "timeout"
        if "connection" in summary or "network" in summary:
            return "network"
        return "service"


class AIWorker(QThread):
    """Run one model turn and any requested desktop tools off the UI thread."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    tool_use = pyqtSignal(str, object, str)
    # The last argument carries a prior occurrence of this same action, when the
    # journal has one, so the prompt can say it has been done before.
    approval_requested = pyqtSignal(str, object, object, object, object)
    # Transparency: the whole plan is known before anything runs, and each step
    # announces itself before it acts rather than only reporting afterwards.
    plan_ready = pyqtSignal(object)
    step_started = pyqtSignal(int, str, object)
    step_finished = pyqtSignal(int, str, object, bool, str)
    turn_summary = pyqtSignal(str)

    # Actions that change another app, launch a program, or put a message in
    # front of another person are confirmed first. Opening a page or running a
    # search is reversible (it is just a browser tab), so navigation stays free.
    CONFIRMATION_REQUIRED = {
        "open_app", "type_text", "click_mouse", "press_key", "hotkey",
        "minimize_window", "maximize_window", "whatsapp_message",
        # Delivers a message to another person: not reversible once read.
        "whatsapp_send",
        # Submits data on a live page, which can post, purchase, or send.
        "browser_type",
        # Reaches another person's inbox and cannot be recalled.
        "send_email",
        # GitHub actions that publish, change, or destroy something are listed
        # alongside their implementations so the two cannot drift apart.
        *github_api.CONFIRMATION_REQUIRED,
        # Installing code changes what this program is and runs a stranger's
        # code with full access to everything Muno holds. Never automatic.
        "install_capability",
        "uninstall_capability",
        # Typing into an unknown field could go anywhere, so the user picks the
        # target and sees the exact text first.
        "write_into_field",
    }

    # Text turns are fast; vision turns upload several base64 frames and need
    # significantly more headroom before the request is considered timed out.
    TEXT_TIMEOUT = 15.0
    VISION_TIMEOUT = 45.0

    def __init__(self, messages, credential_gate, use_vision=False, use_tools=True,
                 request_id=None, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.credential_gate = credential_gate
        self.use_vision = use_vision
        self.use_tools = use_tools
        # Links every action back to the prompt that caused it.
        self.request_id = request_id

    @staticmethod
    def _parse_arguments(raw_arguments):
        if raw_arguments is None or raw_arguments == "":
            return {}
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object.")
        return parsed

    def run(self):
        try:
            client = self.credential_gate.create_client()
            if client is None:
                self.error.emit(self.credential_gate.state)
                return
            model_name = VISION_MODEL if self.use_vision else TEXT_MODEL
            request_timeout = self.VISION_TIMEOUT if self.use_vision else self.TEXT_TIMEOUT
            call_params = {"model": model_name, "messages": self.messages, "timeout": request_timeout}
            if self.use_tools and not self.use_vision:
                # Send only the tools this message plausibly needs. The full set
                # is ~5k tokens, which alone can exceed a modest rate limit.
                latest_user_text = ""
                for message in reversed(self.messages):
                    if message.get("role") == "user" and isinstance(message.get("content"), str):
                        latest_user_text = message["content"]
                        break
                call_params.update({
                    "tools": select_tools(latest_user_text),
                    "tool_choice": "auto",
                })

            response = client.chat.completions.create(**call_params)
            assistant_message = response.choices[0].message
            tool_calls = assistant_message.tool_calls or []
            if not tool_calls:
                self.finished.emit(plain_speech(assistant_message.content))
                return

            # Publish the full plan first so the user can see everything that is
            # about to happen, not just what already happened.
            planned = []
            for tool_call in tool_calls:
                try:
                    planned.append((
                        tool_call.function.name,
                        self._parse_arguments(tool_call.function.arguments),
                    ))
                except Exception:
                    planned.append((tool_call.function.name, {}))
            self.plan_ready.emit(planned)

            follow_up_messages = self.messages + [assistant_message.model_dump(exclude_none=True)]
            outcomes = []
            for index, tool_call in enumerate(tool_calls):
                function_name = tool_call.function.name
                args_dict = planned[index][1] if index < len(planned) else {}
                result = "The requested action is not available."
                succeeded = False

                self.step_started.emit(index, function_name, args_dict)
                started_at = time.monotonic()
                if function_name in TOOL_FUNCTIONS:
                    try:
                        args_dict = self._parse_arguments(tool_call.function.arguments)

                        # If this exact action was already carried out, ask even
                        # when it would normally run without a prompt. Repeating
                        # an effectful action by accident is the costly case.
                        previous = journal.JOURNAL.find_previous(function_name, args_dict)
                        needs_approval = (
                            function_name in self.CONFIRMATION_REQUIRED or previous is not None
                        )

                        if needs_approval:
                            decision = {"approved": False}
                            decision_ready = threading.Event()
                            self.approval_requested.emit(
                                function_name, args_dict, decision, decision_ready, previous
                            )
                            decision_ready.wait(120)
                            if not decision["approved"]:
                                result = "The user did not approve this desktop action."
                            else:
                                result = str(TOOL_FUNCTIONS[function_name](**args_dict))
                                succeeded = not result.strip().startswith("❌")
                        else:
                            result = str(TOOL_FUNCTIONS[function_name](**args_dict))
                            succeeded = not result.strip().startswith("❌")
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        print(f"Rejected unsafe arguments for {function_name}: {exc}", file=sys.stderr)
                        result = "The requested action could not be completed safely."
                    except Exception as exc:
                        print(f"Tool {function_name} failed: {exc}", file=sys.stderr)
                        result = "The requested action could not be completed."

                outcomes.append((function_name, args_dict, succeeded))
                # Write the record from the worker thread so it lands even if the
                # UI is busy or the turn is interrupted.
                denied = "did not approve" in str(result).lower()
                journal.JOURNAL.record_action(
                    self.request_id,
                    function_name,
                    args_dict,
                    journal.STATUS_DENIED if denied
                    else (journal.STATUS_OK if succeeded else journal.STATUS_FAILED),
                    result,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                )
                self.step_finished.emit(index, function_name, args_dict, succeeded, result)
                self.tool_use.emit(function_name, args_dict, result)
                follow_up_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            self.turn_summary.emit(voice.summarize_completed(outcomes))

            follow_up_messages.append({
                "role": "system",
                "content": (
                    "Give the user one brief natural-language follow-up. Do not reveal tool names, "
                    "arguments, schemas, raw errors, or mechanical result text. Use plain text only."
                ),
            })
            final_response = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=follow_up_messages,
                timeout=self.TEXT_TIMEOUT,
            )
            self.finished.emit(plain_speech(final_response.choices[0].message.content))
        except Exception as exc:
            self.error.emit(CredentialGate.classify_error(exc))


class VoiceWorker(QThread):
    """Speak a queue of phrases through the local speech engine.

    Using the Windows speech engine rather than a hosted service means narration
    keeps pace with the actions it describes: no request latency, no key, no
    length limit, and no failure when the network is busy.
    """

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._queue = [text] if text else []
        self._lock = threading.Lock()
        self._running = True

    def enqueue(self, text):
        """Add a phrase to say after the current one."""
        phrase = str(text or "").strip()
        if not phrase:
            return
        with self._lock:
            self._queue.append(phrase)

    def stop(self):
        self._running = False
        with self._lock:
            self._queue.clear()
        voice.stop_all()

    def _next_phrase(self):
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def run(self):
        try:
            while self._running:
                phrase = self._next_phrase()
                if phrase is None:
                    break
                voice.speak_blocking(phrase, should_continue=lambda: self._running)
        except Exception as exc:
            print(f"Voice worker error: {exc}", file=sys.stderr)


# Whisper is prone to inventing polite filler when handed near-silence. These are
# the phrases it reaches for, and none of them are plausible things to say to a
# desktop assistant, so a short transcript matching one is discarded.
HALLUCINATION_PHRASES = {
    "thank you", "thanks for watching", "thanks for watching!", "please subscribe",
    "subscribe", "bye", "bye bye", "goodbye.", "you", "yeah", "hmm", "uh",
    "okay", "ok", "so", "the", "a", "oh", "mm", "mhm", "ah",
    "[blank_audio]", "(silence)", "[silence]", "[music]", "(music)",
    "thank you for watching", "see you next time", "amen",
}


def is_probably_noise(text, voiced_seconds):
    """Judge whether a transcript is worth acting on.

    Very short results that match Whisper's stock filler are almost always the
    model guessing at background noise rather than anything that was said.
    """
    cleaned = " ".join(str(text or "").lower().split()).strip(" .,!?-")
    if not cleaned:
        return True
    if cleaned in HALLUCINATION_PHRASES:
        return True
    # A long transcript from a very short burst of voice is not credible.
    if voiced_seconds < 0.35 and len(cleaned) > 40:
        return True
    # A single short token from a brief noise is usually invented.
    if voiced_seconds < 0.30 and len(cleaned.split()) <= 1:
        return True
    return False


class StreamingVoiceWorker(QThread):
    """Listens continuously, reacting the moment speech starts.

    The microphone is consumed in 20 ms frames rather than recorded in one go, so
    speech onset is known almost immediately. That is what makes interruption
    possible: Muno can stop talking before finishing its sentence.
    """

    transcribed = pyqtSignal(str)
    error = pyqtSignal(str)
    listening = pyqtSignal(bool)
    # Published continuously so the interface can show it is hearing something.
    level = pyqtSignal(float)
    # Raised as soon as speech begins, before any transcription exists.
    speech_started = pyqtSignal()
    # Raised when speech stops, carrying how long was captured. This is what
    # tells the user their turn has ended and Muno is now working on it.
    speech_ended = pyqtSignal(float)
    # A transcript that was recognised but judged to be noise, so the user can
    # see it was heard and why it was ignored.
    discarded = pyqtSignal(str)
    # Raised when speech begins while Muno is talking.
    interrupted = pyqtSignal()

    # While Muno is speaking its own voice reaches the microphone, so the bar for
    # "that was the user" is raised and has to be sustained rather than a blip.
    SPEAKING_TRIGGER_MULTIPLIER = 2.2
    SPEAKING_SUSTAIN_FRAMES = 9  # about 180 ms of continuous speech

    def __init__(self, credential_gate, parent=None):
        super().__init__(parent)
        self.credential_gate = credential_gate
        self._running = True
        self._paused = False
        self._muno_speaking = False
        self._vocabulary = ""

    def stop(self):
        self._running = False
        self._paused = False
        self.requestInterruption()

    def set_paused(self, paused):
        """Pause capture entirely, for example while shutting down."""
        self._paused = bool(paused)

    def set_muno_speaking(self, speaking):
        """Tell the listener that Muno's own voice is playing.

        Listening deliberately continues, because stopping would make
        interruption impossible. The threshold is raised instead.
        """
        self._muno_speaking = bool(speaking)

    def set_vocabulary(self, text):
        """Give the transcriber the names it should expect to hear."""
        self._vocabulary = str(text or "")[:900]

    def _transcribe(self, client, audio_bytes):
        """Send one utterance for transcription, tuned for accuracy."""
        parameters = {
            "model": VOICE_MODEL,
            "file": ("speech.wav", audio_bytes),
            "response_format": "text",
            # Naming the language stops it guessing, which is a common source of
            # nonsense transcripts for short phrases.
            "language": os.getenv("MUNO_VOICE_LANGUAGE", "en"),
            # No creativity wanted here: the same audio should give the same text.
            "temperature": 0.0,
            "timeout": 20.0,
        }
        if self._vocabulary:
            # Biases decoding towards names it would otherwise mangle.
            parameters["prompt"] = self._vocabulary
        transcript = client.audio.transcriptions.create(**parameters)
        if isinstance(transcript, str):
            return transcript
        return getattr(transcript, "text", "")

    def run(self):
        if not self.credential_gate.can_request():
            return
        client = self.credential_gate.create_client()
        if client is None:
            return

        detector = voice_stream.VoiceActivityDetector()
        sustained = 0.0

        try:
            with voice_stream.open_stream() as stream:
                self.listening.emit(True)
                while self._running and not self.isInterruptionRequested():
                    if self._paused:
                        self.msleep(80)
                        continue

                    frame, _overflowed = stream.read(voice_stream.FRAME_SAMPLES)
                    mono = frame[:, 0] if frame.ndim > 1 else frame

                    # Raise the bar while Muno's own voice is in the room.
                    if self._muno_speaking:
                        level_now = voice_stream.frame_level(mono)
                        threshold = max(
                            voice_stream.MIN_TRIGGER_LEVEL,
                            detector.trigger_level,
                        ) * self.SPEAKING_TRIGGER_MULTIPLIER
                        if level_now >= threshold:
                            sustained += 1.0
                        else:
                            # Decay gently. Speech is not loud on every single
                            # frame: there are gaps between syllables, and
                            # subtracting a whole frame for each of them meant
                            # the counter oscillated and never reached the target.
                            sustained = max(0.0, sustained - 0.34)
                        if sustained >= self.SPEAKING_SUSTAIN_FRAMES:
                            sustained = 0.0
                            self._muno_speaking = False
                            detector.reset()
                            self.interrupted.emit()
                        self.level.emit(
                            voice_stream.level_to_meter(level_now, detector.trigger_level)
                        )
                        # Do not build an utterance out of Muno's own voice.
                        continue
                    sustained = 0.0

                    event = detector.feed(mono)
                    self.level.emit(event["meter"])

                    if event["started"]:
                        self.speech_started.emit()

                    if event["ended"]:
                        self.speech_ended.emit(float(event.get("duration", 0.0)))

                    frames = event.get("utterance")
                    if not frames:
                        continue

                    audio = voice_stream.wav_bytes(frames)
                    try:
                        text = self._transcribe(client, audio)
                    except Exception as exc:
                        self.error.emit(CredentialGate.classify_error(exc))
                        continue

                    if not self._running:
                        break
                    text = plain_speech(text)
                    if is_probably_noise(text, event.get("voiced_seconds", 0.0)):
                        # Say what was heard even when ignoring it, so silence is
                        # never ambiguous about whether the microphone works.
                        if text:
                            self.discarded.emit(text)
                        continue
                    self.transcribed.emit(text)
        except Exception as exc:
            self.error.emit(CredentialGate.classify_error(exc))
        finally:
            self.listening.emit(False)


class SpeechBubble(QTextEdit):
    """The single plain-text response surface above Muno."""

    MIN_HEIGHT = 32
    MAX_HEIGHT = 280
    BUBBLE_WIDTH = 180
    CONTENT_WIDTH = 164

    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        self._target_height = self.MIN_HEIGHT
        self._pop_animation = None
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setFixedWidth(self.BUBBLE_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        self.document().setDocumentMargin(0)
        # Apple tokens: parchment surface, ink text, hairline edge, no shadow on
        # chrome, and the generous corner radius from the spec.
        self.setStyleSheet("""
            QTextEdit {
                background-color: rgba(245, 245, 247, 250);
                color: #1d1d1f;
                border: 1px solid rgba(0, 0, 0, 20);
                border-radius: 11px;
                padding: 8px 10px;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 10px;
                selection-background-color: rgba(0, 102, 204, 90);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 8px 2px 8px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 45);
                border-radius: 3px;
                min-height: 18px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
        self.hide()

    def _wrapped_lines(self, text):
        """Measure wrapping without modifying or eliding the displayed response."""
        metrics = self.fontMetrics()
        lines = []
        for paragraph in text.splitlines() or [""]:
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = ""
            for word in words:
                chunks = []
                remaining = word
                while remaining and metrics.horizontalAdvance(remaining) > self.CONTENT_WIDTH:
                    cut = len(remaining)
                    while cut > 1 and metrics.horizontalAdvance(remaining[:cut]) > self.CONTENT_WIDTH:
                        cut -= 1
                    chunks.append(remaining[:cut])
                    remaining = remaining[cut:]
                if remaining:
                    chunks.append(remaining)
                for chunk in chunks:
                    candidate = f"{current} {chunk}".strip()
                    if not current or metrics.horizontalAdvance(candidate) <= self.CONTENT_WIDTH:
                        current = candidate
                    else:
                        lines.append(current)
                        current = chunk
            lines.append(current)
        return lines

    def text(self):
        """Retain QLabel-style access for diagnostics and existing integrations."""
        return self.toPlainText()

    def present(self, text, max_height=None):
        """Replace prior content and animate to the available content-dependent height."""
        self.full_text = plain_speech(text) or "…"
        self.setPlainText(self.full_text)
        self.verticalScrollBar().setValue(0)
        line_count = max(1, len(self._wrapped_lines(self.full_text)))
        desired_height = max(
            self.MIN_HEIGHT,
            line_count * self.fontMetrics().lineSpacing() + 16,
        )
        allowed_height = self.MAX_HEIGHT if max_height is None else max(0, int(max_height))
        self._target_height = min(desired_height, self.MAX_HEIGHT, allowed_height)
        if self._pop_animation:
            self._pop_animation.stop()
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        if self._target_height <= 0:
            self.hide()
            return 0
        self.show()
        self.raise_()
        self._pop_animation = QPropertyAnimation(self, b"maximumHeight", self)
        self._pop_animation.setDuration(260)
        self._pop_animation.setStartValue(0)
        self._pop_animation.setEndValue(self._target_height)
        self._pop_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pop_animation.finished.connect(self._finish_pop)
        self._pop_animation.start()
        return self._target_height

    def _finish_pop(self):
        self.setMinimumHeight(self._target_height)
        self.setMaximumHeight(self._target_height)

    def dismiss(self):
        if self._pop_animation:
            self._pop_animation.stop()
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        self.clear()
        self.full_text = ""
        self.hide()
        self.dismissed.emit()


class ListenMeter(QWidget):
    """A slim bar showing what the microphone is picking up, live.

    Without this the user has no idea whether Muno can hear them until a reply
    arrives, which is the moment it is too late to be useful.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._level = 0.0
        self._shown = 0.0
        self._active = False
        # Decay smoothly so the bar glides rather than flickering per frame.
        self._decay = QTimer(self)
        self._decay.timeout.connect(self._tick)
        self._decay.start(33)

    def set_level(self, level):
        self._level = max(0.0, min(1.0, float(level)))

    def set_active(self, active):
        self._active = bool(active)

    def _tick(self):
        # Rise quickly to feel responsive, fall slowly to stay readable.
        if self._level > self._shown:
            self._shown += (self._level - self._shown) * 0.55
        else:
            self._shown += (self._level - self._shown) * 0.12
        if self._shown < 0.02 and self._active:
            self._active = False
        self.update()

    def paintEvent(self, event):
        if self._shown <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = int(self.width() * self._shown)
        if width <= 0:
            return
        colour = QColor("#a6e3a1") if self._shown < 0.75 else QColor("#f9e2af")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        painter.drawRoundedRect(
            (self.width() - width) // 2, 0, width, self.height(), 2, 2
        )


class AnimatedAvatar(QWidget):
    """Full-body animated Muno character with expressive face and gestures."""

    # Sprite pets animate on their own clock, independent of the hand-drawn
    # breathing motion, so each animation plays at the pace its artist intended.
    SPRITE_FRAME_MS = 120

    # Body motion runs at about 60 Hz so movement and leaning look smooth. The
    # phase step is scaled by the same factor so nothing actually speeds up:
    # only the sampling gets finer. Pixel-art frames are still stepped, never
    # blended, because blending them would just look blurry.
    MOTION_TICK_MS = 16
    MOTION_SCALE = MOTION_TICK_MS / 40.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        # When a sprite pet is loaded it is drawn instead of the coded avatar.
        self.sprite_pet = None
        self.sprite_frame = 0
        self.sprite_timer = QTimer(self)
        self.sprite_timer.timeout.connect(self._advance_sprite)
        # Drag state: which way the pet is being pulled, and how hard.
        self.drag_direction = 0
        self.drag_speed = 0.0
        # Lean follows the drag direction gradually rather than snapping, which
        # is what makes the motion read as weight instead of a jump cut.
        self.lean = 0.0
        self.facing = 1
        self.is_speaking = False
        self.mouth_open = False
        self.blink_state = False
        self.head_tilt = 0
        self.idle_bounce = 0.0
        self.idle_sway = 0.0
        self.animation_phase = 0.0
        self.eye_focus_x = 0.0
        self.eye_focus_y = 0.0
        self.state = "idle"
        self.emotion = "happy"

        self.talk_timer = QTimer(self)
        self.talk_timer.timeout.connect(self.animate_mouth)

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.blink)
        self.blink_timer.start(2800)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.animate_idle)
        self.idle_timer.start(self.MOTION_TICK_MS)
        
    def start_speaking(self):
        """Animate Muno's mouth, eyes, and hands while audio plays."""
        self.is_speaking = True
        self.state = "speaking"
        self.talk_timer.start(125)
        self.update()

    def stop_speaking(self):
        self.is_speaking = False
        self.talk_timer.stop()
        self.mouth_open = False
        if self.state == "speaking":
            self.state = "idle"
        self.eye_focus_x = 0.0
        self.eye_focus_y = 0.0
        self.update()

    def animate_mouth(self):
        self.mouth_open = not self.mouth_open
        self.update()

    def animate_idle(self):
        """Drive breathing, body bounce, eye movement, and gestures.

        The tick runs faster than before for smoother motion, so the phase step
        is scaled down to keep every rhythm at its original speed.
        """
        self.animation_phase += 0.12 * self.MOTION_SCALE

        # Ease the lean towards where the pet is being dragged. Exponential
        # smoothing gives it a sense of weight without any abrupt change.
        target_lean = self.drag_direction * min(1.0, 0.35 + self.drag_speed * 0.5)
        self.lean += (target_lean - self.lean) * 0.18

        if self.drag_direction:
            # Carried rather than standing: quicker bob, no idle sway.
            bounce_size = 2.6
            self.idle_bounce = math.sin(self.animation_phase * 2.1) * bounce_size
            self.idle_sway = self.lean * 2.5
            self.update()
            return

        bounce_size = 3.0 if self.is_speaking else 1.8
        self.idle_bounce = math.sin(self.animation_phase) * bounce_size
        self.idle_sway = math.sin(self.animation_phase * 0.55) * 1.5 + self.lean * 2.5
        if self.is_speaking:
            self.eye_focus_x = math.sin(self.animation_phase * 1.7) * 2.5
            self.eye_focus_y = math.cos(self.animation_phase * 1.3) * 1.3
        elif self.state == "thinking":
            self.eye_focus_x = 3.5
            self.eye_focus_y = -2.5
        else:
            self.eye_focus_x *= 0.9
            self.eye_focus_y *= 0.9
        self.update()

    def blink(self):
        self.blink_state = True
        self.update()
        QTimer.singleShot(120, self.unblink)

    def unblink(self):
        self.blink_state = False
        self.update()

    def set_state(self, state):
        """Apply one of Muno's centralized visual states."""
        aliases = {"scanning": "excited", "recording": "surprised", "sleep": "sleeping"}
        state = aliases.get(state, state)
        valid_states = {
            "idle", "thinking", "speaking", "excited", "confused",
            "surprised", "sleeping", "error",
        }
        self.state = state if state in valid_states else "idle"
        self.head_tilt = 9 if self.state == "thinking" else 0
        emotions = {
            "idle": "happy",
            "thinking": "thinking",
            "speaking": "happy",
            "excited": "excited",
            "confused": "confused",
            "surprised": "surprised",
            "sleeping": "sad",
            "error": "sad",
        }
        self.emotion = emotions[self.state]
        # Keep the animation timed for whatever is now showing.
        if self.sprite_pet:
            self._restart_sprite_timer()
        self.update()

    def set_emotion(self, emotion):
        """Compatibility adapter for preserved callers; state remains canonical."""
        mapping = {
            "happy": "idle", "thinking": "thinking", "excited": "excited",
            "confused": "confused", "surprised": "surprised", "sad": "sleeping",
        }
        self.set_state(mapping.get(emotion, "idle"))
        
    def set_sprite_pet(self, sprite_pet):
        """Swap in a downloaded sprite pet, or None to restore the drawn Muno."""
        self.sprite_pet = sprite_pet
        self.sprite_frame = 0
        if sprite_pet:
            self._restart_sprite_timer()
        else:
            self.sprite_timer.stop()
        self.update()

    def current_animation_state(self):
        """Which state should be showing right now.

        Being dragged wins over everything else: while the user is physically
        moving the pet, that is what it should be reacting to.
        """
        if self.drag_direction > 0:
            return "dragged-right"
        if self.drag_direction < 0:
            return "dragged-left"
        if self.is_speaking:
            return "speaking"
        return self.state

    def _restart_sprite_timer(self):
        """Retime the animation for the state now showing."""
        if not self.sprite_pet:
            return
        interval = self.sprite_pet.frame_interval(
            self.current_animation_state(), self.drag_speed
        )
        self.sprite_timer.start(int(interval))

    def set_drag_direction(self, direction, speed=0.0):
        """Face and animate towards the direction being dragged.

        ``direction`` is -1 for left, 1 for right, 0 for released.
        """
        direction = int(direction)
        changed = direction != self.drag_direction
        self.drag_direction = direction
        self.drag_speed = max(0.0, float(speed))
        if direction:
            self.facing = direction
        if self.sprite_pet:
            if changed:
                # A new animation should start from its first frame.
                self.sprite_frame = 0
            self._restart_sprite_timer()
        if changed:
            self.update()

    def _advance_sprite(self):
        if not self.sprite_pet:
            return
        state = self.current_animation_state()
        self.sprite_frame = (self.sprite_frame + 1) % max(
            1, self.sprite_pet.frame_count(state)
        )
        self.update()

    def _paint_sprite(self, painter):
        """Draw the current sprite frame, scaled to fit and pixel-crisp."""
        state = self.current_animation_state()
        frame = self.sprite_pet.frame(state, self.sprite_frame)
        if frame is None or frame.isNull():
            return False

        # Nearest-neighbour keeps pixel art sharp instead of blurring it.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        scale = min(self.width() / frame.width(), self.height() / frame.height())
        width = max(1, int(frame.width() * scale))
        height = max(1, int(frame.height() * scale))
        x = (self.width() - width) // 2
        # Sit the pet on the bottom edge so it looks grounded, and let the
        # existing idle bounce carry through for a little life.
        y = self.height() - height + int(self.idle_bounce)

        # An atlas without a left run cycle gets mirrored instead, so the pet
        # still faces the way it is going rather than running backwards.
        mirror = (
            self.drag_direction < 0
            and self.sprite_pet.resolve_state("dragged-left") != "running-left"
        )
        if mirror:
            painter.save()
            painter.translate(x + width, y)
            painter.scale(-1.0, 1.0)
            painter.drawPixmap(0, 0, width, height, frame)
            painter.restore()
        else:
            painter.drawPixmap(x, y, width, height, frame)
        return True

    def paintEvent(self, event):
        """Paint a full-body, expressive animated version of Muno."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if self.sprite_pet and self._paint_sprite(painter):
            return

        design_width, design_height = 280.0, 330.0
        scale = min(self.width() / design_width, self.height() / design_height)
        painter.translate(
            (self.width() - design_width * scale) / 2.0,
            (self.height() - design_height * scale) / 2.0,
        )
        painter.scale(scale, scale)
        painter.translate(self.idle_sway, self.idle_bounce)

        # Tip the whole body towards the direction of travel. Rotating about the
        # feet rather than the centre keeps it planted instead of sliding.
        if abs(self.lean) > 0.01:
            painter.translate(design_width / 2.0, design_height)
            painter.rotate(self.lean * 9.0)
            painter.translate(-design_width / 2.0, -design_height)

        skin = QColor("#7a4b28")
        skin_light = QColor("#a66a3f")
        outline = QColor("#251c2e")
        hoodie_dark = QColor("#3430a8")
        hoodie_light = QColor("#625cf6")
        pants = QColor("#24243d")
        shoe = QColor("#edf2ff")
        accent = QColor("#78c8ff")

        # Legs and feet give Muno a grounded, complete silhouette.
        painter.setPen(QPen(pants, 22, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(119, 226, 112, 294)
        painter.drawLine(161, 226, 168, 294)
        painter.setPen(QPen(outline, 2))
        painter.setBrush(QBrush(shoe))
        painter.drawRoundedRect(82, 287, 48, 24, 11, 11)
        painter.drawRoundedRect(150, 287, 48, 24, 11, 11)
        painter.setPen(QPen(accent, 3))
        painter.drawLine(89, 299, 123, 299)
        painter.drawLine(157, 299, 191, 299)

        # Arms change pose for speech and thought.
        gesture = int(math.sin(self.animation_phase * 1.8) * 10) if self.is_speaking else 0
        painter.setPen(QPen(hoodie_dark, 22, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.state == "thinking":
            painter.drawLine(98, 158, 72, 206)
            painter.drawLine(182, 158, 194, 112)
            painter.setPen(QPen(skin, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(194, 112, 182, 99)
        elif self.is_speaking:
            painter.drawLine(98, 158, 66, 197 - gesture)
            painter.drawLine(182, 158, 214, 197 + gesture)
            painter.setPen(QPen(skin, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPoint(63, 201 - gesture)
            painter.drawPoint(217, 201 + gesture)
        else:
            painter.drawLine(98, 158, 77, 225)
            painter.drawLine(182, 158, 203, 225)
            painter.setPen(QPen(skin, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPoint(75, 230)
            painter.drawPoint(205, 230)

        # Hoodie body with depth, pocket, zip, and glowing badge.
        hoodie_gradient = QLinearGradient(94, 137, 186, 240)
        hoodie_gradient.setColorAt(0.0, hoodie_light)
        hoodie_gradient.setColorAt(1.0, hoodie_dark)
        painter.setPen(QPen(outline, 3))
        painter.setBrush(QBrush(hoodie_gradient))
        painter.drawRoundedRect(91, 132, 98, 112, 28, 28)
        painter.setPen(QPen(QColor("#c4c8ff"), 3))
        painter.drawLine(140, 139, 140, 229)
        painter.setBrush(QBrush(QColor(37, 35, 92, 150)))
        painter.drawRoundedRect(112, 202, 56, 25, 10, 10)
        painter.setPen(QPen(accent, 2))
        painter.setBrush(QBrush(QColor("#20385c")))
        painter.drawEllipse(130, 157, 20, 20)
        painter.drawLine(136, 167, 144, 167)
        painter.drawLine(140, 163, 140, 171)

        # Neck sits naturally between hoodie and head.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(skin))
        painter.drawRoundedRect(126, 110, 28, 38, 9, 9)

        # Head and facial features tilt independently while thinking.
        painter.save()
        painter.translate(140, 76)
        painter.rotate(self.head_tilt + (math.sin(self.animation_phase) * 1.5 if self.is_speaking else 0))
        painter.translate(-140, -76)

        painter.setPen(QPen(outline, 3))
        painter.setBrush(QBrush(skin))
        painter.drawEllipse(91, 20, 98, 112)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(skin_light))
        painter.drawEllipse(101, 33, 32, 66)
        painter.setBrush(QBrush(skin))
        painter.drawEllipse(84, 65, 15, 29)
        painter.drawEllipse(181, 65, 15, 29)

        # Textured high-top hair silhouette.
        painter.setBrush(QBrush(QColor("#17131d")))
        painter.drawRoundedRect(94, 14, 92, 45, 20, 20)
        painter.drawEllipse(99, 7, 25, 32)
        painter.drawEllipse(119, 3, 28, 35)
        painter.drawEllipse(143, 5, 31, 34)
        painter.drawEllipse(165, 12, 21, 30)

        left_eye_x, right_eye_x, eye_y = 116, 153, 67
        eye_height = 19 if self.emotion == "surprised" else 16
        if self.blink_state or self.state == "sleeping":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(left_eye_x, eye_y + 8, left_eye_x + 19, eye_y + 8)
            painter.drawLine(right_eye_x, eye_y + 8, right_eye_x + 19, eye_y + 8)
        else:
            painter.setPen(QPen(outline, 2))
            painter.setBrush(QBrush(QColor("#fffdf7")))
            painter.drawEllipse(left_eye_x, eye_y, 20, eye_height)
            painter.drawEllipse(right_eye_x, eye_y, 20, eye_height)
            pupil_size = 10 if self.emotion == "excited" else 8
            pupil_y = eye_y + eye_height / 2 - pupil_size / 2 + self.eye_focus_y
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#161320")))
            painter.drawEllipse(int(left_eye_x + 6 + self.eye_focus_x), int(pupil_y), pupil_size, pupil_size)
            painter.drawEllipse(int(right_eye_x + 6 + self.eye_focus_x), int(pupil_y), pupil_size, pupil_size)
            if self.emotion == "excited":
                painter.setBrush(QBrush(QColor("#ffffff")))
                painter.drawEllipse(int(left_eye_x + 9 + self.eye_focus_x), int(pupil_y + 2), 3, 3)
                painter.drawEllipse(int(right_eye_x + 9 + self.eye_focus_x), int(pupil_y + 2), 3, 3)

        # Eyebrows react independently from the eyes.
        painter.setPen(QPen(QColor("#241714"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.emotion == "sad":
            painter.drawLine(115, 59, 133, 56)
            painter.drawLine(154, 56, 172, 59)
        elif self.emotion == "confused":
            painter.drawLine(115, 55, 133, 59)
            painter.drawLine(154, 52, 172, 49)
        else:
            painter.drawLine(115, 57, 133, 55)
            painter.drawLine(154, 55, 172, 57)

        # Soft nose and animated mouth shapes.
        painter.setPen(QPen(QColor("#4e2c1d"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(143, 78, 140, 88)
        painter.drawLine(140, 88, 146, 88)
        if self.is_speaking and self.mouth_open:
            painter.setPen(QPen(outline, 2))
            painter.setBrush(QBrush(QColor("#55263b")))
            painter.drawEllipse(129, 96, 25, 21)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#f38ba8")))
            painter.drawEllipse(134, 107, 15, 7)
        elif self.emotion == "surprised":
            painter.setPen(QPen(outline, 2))
            painter.setBrush(QBrush(QColor("#55263b")))
            painter.drawEllipse(134, 97, 15, 19)
        elif self.emotion == "sad":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, 3))
            painter.drawArc(128, 103, 27, 17, 0, 180 * 16)
        elif self.emotion == "confused":
            painter.setPen(QPen(outline, 3))
            painter.drawLine(130, 105, 139, 101)
            painter.drawLine(139, 101, 153, 106)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, 3))
            painter.drawArc(126, 91, 31, 23, 0, -180 * 16)

        # Rounded tech glasses retain Muno's original visual identity.
        painter.setBrush(QBrush(QColor(105, 130, 255, 32)))
        painter.setPen(QPen(QColor("#6864ff"), 3))
        painter.drawRoundedRect(108, 62, 34, 27, 7, 7)
        painter.drawRoundedRect(147, 62, 34, 27, 7, 7)
        painter.drawLine(142, 73, 147, 73)
        painter.restore()

        # State cues make thinking and speaking readable without text.
        if self.state == "thinking":
            painter.setPen(QPen(QColor("#9ed8ff"), 2))
            painter.setBrush(QBrush(QColor(158, 216, 255, 190)))
            painter.drawEllipse(202, 66, 9, 9)
            painter.drawEllipse(214, 49, 14, 14)
            painter.drawEllipse(231, 28, 21, 21)
        elif self.is_speaking:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            pulse = int(abs(math.sin(self.animation_phase)) * 5)
            painter.drawArc(205, 66 - pulse, 18 + pulse, 24 + pulse, -70 * 16, 140 * 16)
            painter.drawArc(211, 58 - pulse, 27 + pulse, 40 + pulse, -70 * 16, 140 * 16)


class SettingsDialog(QDialog):
    """Dialog for changing personality and user name."""

    def __init__(self, current_personality, user_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Codex Pet Settings")
        self.setGeometry(100, 100, 400, 200)

        layout = QFormLayout()

        self.personality_combo = QComboBox()
        self.personality_combo.addItems(list(PERSONALITIES.keys()))
        self.personality_combo.setCurrentText(current_personality)
        layout.addRow("Specialty:", self.personality_combo)

        self.name_input = QLineEdit()
        self.name_input.setText(user_name)
        layout.addRow("Your Name:", self.name_input)

        self.pet_picker_requested = False
        appearance_btn = QPushButton("Choose a Pet…")
        appearance_btn.setToolTip("Browse pixel pets shared at codex-pets.net")
        appearance_btn.clicked.connect(self.request_pet_picker)
        layout.addRow("Appearance:", appearance_btn)

        self.clear_memory_requested = False
        clear_memory_btn = QPushButton("Clear This Mode's Memory")
        clear_memory_btn.clicked.connect(self.request_memory_clear)
        layout.addRow("Chat Memory:", clear_memory_btn)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addRow(btn_layout)

        self.setLayout(layout)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e2e; }
            QLabel { color: #cdd6f4; }
            QComboBox { background-color: #313244; color: #a6e3a1; border: 1px solid #45475a; border-radius: 3px; }
            QLineEdit { background-color: #313244; color: #a6e3a1; border: 1px solid #45475a; border-radius: 3px; }
            QPushButton { background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 3px; padding: 5px; }
        """)

    def request_pet_picker(self):
        """Close settings and hand back to the main window to open the picker."""
        self.pet_picker_requested = True
        self.accept()

    def request_memory_clear(self):
        answer = QMessageBox.question(
            self,
            "Clear chat memory?",
            "This removes saved messages for the currently selected personality. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.clear_memory_requested = True
            self.accept()

    def get_personality(self):
        return self.personality_combo.currentText()

    def get_user_name(self):
        return self.name_input.text() if self.name_input.text().strip() else "Friend"


class CodexPet(QMainWindow):
    COMPACT_WIDTH = 190
    COMPACT_HEIGHT = 325

    def __init__(self, instance_guard=None):
        super().__init__()
        self.instance_guard = instance_guard
        self.is_first_run = not os.path.exists(CONFIG_FILE)
        self.credential_gate = CredentialGate()
        self.config = load_config()
        self.current_personality = self.config.get("personality", "Muno")
        if self.current_personality not in PERSONALITIES:
            self.current_personality = "Muno"
        self.user_name = self.config.get("user_name", "Friend")
        self.pet_name = "Muno"
        self.config["pet_name"] = self.pet_name
        self.conversation_history = load_conversation(self.current_personality)
        self.ai_worker = None
        self.voice_worker = None
        self.voice_listener = None
        self.pending_screenshot_base64 = None
        self.recording_frame_paths = []
        self.recording_directory = None
        self.is_recording = False
        self.is_muted = False
        self.is_sleeping = False
        self.pending_exit = False
        self.visual_state = "idle"
        self._shutting_down = False
        # Transparency state: the log window plus the steps of the current turn.
        self.activity_log = None
        self.turn_id = 0
        self.current_steps = []
        self.mobile_server = None
        self.mobile_poll_timer = None
        # The sprite appearance in use, if any.
        self.sprite_pet = None
        # A screen watch, when one is armed.
        self.region_watch = None
        self._region_overlay = None
        self.watch_btn = None
        self.watch_action = None
        self._write_overlay = None
        self._pending_write_text = ""
        # Drag tracking, used to work out direction and speed.
        self._dragging = False
        self._drag_offset = None
        self._drag_last_x = 0
        self._drag_last_time = 0.0
        # Journal state: which prompt is being served, and how it arrived.
        self.request_id = None
        self.pending_source = "desktop"
        # The microphone is off unless the user turned it on and left it on.
        self.mic_enabled = bool(self.config.get("microphone", False))
        journal.JOURNAL.start_session(personality=self.current_personality)
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self.capture_recording_frame)
        self.duration_timer = QTimer(self)
        self.duration_timer.timeout.connect(self.update_recording_duration)
        self.flash_timer = QTimer(self)
        self.flash_timer.timeout.connect(self.toggle_recording_dot)
        self.visual_speech_timer = QTimer(self)
        self.visual_speech_timer.setSingleShot(True)
        self.visual_speech_timer.timeout.connect(self.on_visual_speech_finished)
        self.recording_clock = QElapsedTimer()
        self.create_tray_icon()
        self.initUI()
        self.update_mic_button()
        self.update_watch_indicator()
        QTimer.singleShot(0, self.play_startup_walk)
        if self.is_first_run:
            QTimer.singleShot(600, self.show_welcome)
        # Only listen if the user previously left the microphone on. It stays off
        # by default rather than opening the microphone uninvited.
        if self.mic_enabled and CONTINUOUS_VOICE_ENABLED and self.credential_gate.can_request():
            QTimer.singleShot(250, self.start_continuous_voice)
        # Catalogue local files in the background so "upload that file" resolves
        # instantly later. Only rebuild when the catalogue is missing or stale.
        QTimer.singleShot(1500, self.refresh_file_index_if_stale)

        QTimer.singleShot(400, self.start_mobile_server)

        # Let a spoken or typed request open the screen mapper. The overlay must
        # be created on the UI thread, so the tool only schedules it.
        watcher.register_setup_handler(
            lambda: QTimer.singleShot(0, self.start_region_watch)
        )
        watcher.register_write_handler(
            lambda text: QTimer.singleShot(0, lambda: self.begin_write_into_field(text))
        )

        # Watch what the user is doing so Muno can answer "what am I working on"
        # and check in occasionally.
        self.last_checkin_at = time.monotonic()
        self.last_checkin_category = None
        activity.start_monitor()
        self.checkin_timer = QTimer(self)
        self.checkin_timer.timeout.connect(self.consider_checkin)
        if CHECKIN_ENABLED:
            self.checkin_timer.start(CHECKIN_POLL_MS)
    
    def create_tray_icon(self):
        """Create system tray icon with menu"""
        self.tray_icon = QSystemTrayIcon(self)
        
        # Create a simple icon (you can replace with a proper icon file later)
        # For now, we'll use the default app icon
        self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        
        # Create tray menu
        tray_menu = QMenu()
        
        show_action = QAction(f"Show {self.pet_name}", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        hide_action = QAction("Hide to Tray", self)
        hide_action.triggered.connect(self.hide)
        tray_menu.addAction(hide_action)
        
        tray_menu.addSeparator()
        
        log_action = QAction("📋 Activity Log", self)
        log_action.triggered.connect(self.open_activity_log)
        tray_menu.addAction(log_action)

        phone_action = QAction("📱 Connect Phone", self)
        phone_action.triggered.connect(self.show_pairing_details)
        tray_menu.addAction(phone_action)

        pet_action = QAction("🐾 Choose a Pet", self)
        pet_action.triggered.connect(self.open_pet_picker)
        tray_menu.addAction(pet_action)

        self.mic_action = QAction("🎤 Toggle Microphone", self)
        self.mic_action.triggered.connect(self.toggle_microphone)
        tray_menu.addAction(self.mic_action)

        self.watch_action = QAction("👁 Watch Part of Screen", self)
        self.watch_action.triggered.connect(self.toggle_region_watch)
        tray_menu.addAction(self.watch_action)

        settings_action = QAction("⚙️ Settings", self)
        settings_action.triggered.connect(self.open_settings)
        tray_menu.addAction(settings_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.request_exit)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()
        
        # Show notification on first run
        self.tray_icon.showMessage(
            f"{self.pet_name} is here!",
            "Your companion is running. Right-click the tray icon for options.",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )
    
    def on_tray_icon_activated(self, reason):
        """Handle tray icon clicks"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Single click toggles window visibility
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()
    
    def closeEvent(self, event):
        """Keep the companion alive in the tray unless confirmed shutdown is active."""
        if self._shutting_down:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            f"{self.pet_name} is still here",
            "I'm resting in the system tray. Click the icon when you need me.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    @staticmethod
    def _normalize_text(text):
        normalized = re.sub(r"[^a-z0-9' ]", " ", str(text).lower())
        return " ".join(normalized.split())

    def activate_existing(self):
        """Restore and focus the owner window after a secondary launch."""
        self.show()
        self.raise_()
        self.activateWindow()
        if self.windowHandle():
            self.windowHandle().requestActivate()
        self.user_input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def set_visual_state(self, state):
        """Centralize visible lifecycle state without changing interaction flags."""
        if self._shutting_down and state != "sleeping":
            return
        self.visual_state = state
        if state == "speaking":
            self.pet_avatar.start_speaking()
        else:
            if self.pet_avatar.is_speaking:
                self.pet_avatar.stop_speaking()
            self.pet_avatar.set_state(state)

    def _show_credential_notice(self):
        state = self.credential_gate.state
        if not self.credential_gate.claim_notice(state):
            return
        self.set_visual_state("error")
        if state == CredentialGate.MISSING:
            self.show_bubble("Add GROQ_API_KEY to your environment, then restart Muno.")
        else:
            self.show_bubble("GROQ_API_KEY was rejected. Update it, then restart Muno.")

    def _sync_voice_listener(self):
        """Keep the listener's view of the world current.

        Muno speaking deliberately does not pause the microphone: the moment
        worth hearing is exactly the moment Muno is mid-sentence.

        The speaking flag is set explicitly by whoever starts and stops playback
        rather than being inferred here. Inferring it from ``isRunning()`` was a
        real bug: ``QThread.start()`` returns before the thread is actually
        running, so this read False the instant after playback began, latched
        that for the whole utterance, and interruption never fired.
        """
        if not self.voice_listener or not self.voice_listener.isRunning():
            return
        self.voice_listener.set_paused(self._shutting_down)

    def _set_speaking_flag(self, speaking):
        """Tell the listener whether Muno's own voice is currently playing."""
        if self.voice_listener and self.voice_listener.isRunning():
            self.voice_listener.set_muno_speaking(bool(speaking))

    def _screen_geometry(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def _resize_for_response(self, bubble_height):
        """Grow from the window's bottom edge and remain inside the active screen."""
        geometry = self._screen_geometry()
        old_bottom = self.y() + self.height()
        target_height = min(geometry.height(), self.COMPACT_HEIGHT + max(0, bubble_height))
        self.setFixedSize(self.COMPACT_WIDTH, target_height)
        target = QPoint(self.x(), old_bottom - target_height)
        self.move(self._clamped_position(target, geometry))

    def _restore_compact_window(self):
        """Collapse after dismissal while preserving the current bottom anchor."""
        geometry = self._screen_geometry()
        old_bottom = self.y() + self.height()
        compact_height = min(self.COMPACT_HEIGHT, geometry.height())
        self.setFixedSize(self.COMPACT_WIDTH, compact_height)
        target = QPoint(self.x(), old_bottom - compact_height)
        self.move(self._clamped_position(target, geometry))

    def show_bubble(self, text):
        text = plain_speech(text)
        geometry = self._screen_geometry()
        available_bubble_height = max(0, geometry.height() - self.COMPACT_HEIGHT)
        target_height = self.speech_bubble.present(
            text or "…",
            min(SpeechBubble.MAX_HEIGHT, available_bubble_height),
        )
        self._resize_for_response(target_height)

    def speak_or_show(self, text):
        text = plain_speech(text)
        self.show_bubble(text)
        self.say(text)

    def say(self, text, queue=False):
        """Speak a phrase, optionally queueing it behind whatever is playing.

        Long or technical replies are shortened for speech only. The full text
        stays in the bubble and the log: hearing a page of markup read out is
        useless, and the user asked for the point, not the transcript.
        """
        text = voice.spoken_digest(plain_speech(text))
        if not text or self._shutting_down:
            self._sync_voice_listener()
            return
        if self.is_muted or not VOICE_ENABLED or not voice.available():
            # Still animate for roughly as long as speaking would have taken.
            self.set_visual_state("speaking")
            self.visual_speech_timer.start(int(voice.estimate_duration(text) * 1000))
            self._sync_voice_listener()
            return

        if self.voice_worker and self.voice_worker.isRunning():
            if queue:
                self.voice_worker.enqueue(text)
                return
            self.voice_worker.stop()
            self.voice_worker.wait(1500)

        self.visual_speech_timer.stop()
        self.set_visual_state("speaking")
        self.voice_worker = VoiceWorker(text, self)
        self.voice_worker.finished.connect(self.on_voice_finished)
        # Raise the listener's bar before playback begins, not after. Setting it
        # afterwards leaves a window where Muno's own voice looks like the user.
        self._set_speaking_flag(True)
        self.voice_worker.start()
        self._sync_voice_listener()

    # -- narration ---------------------------------------------------------

    def open_activity_log(self):
        """Show the transparency panel."""
        if self.activity_log is None:
            self.activity_log = ActivityLogWindow(self.pet_name, self)
        self.activity_log.show_and_raise()

    def _log(self):
        """Return the log window, creating it lazily without showing it."""
        if self.activity_log is None:
            self.activity_log = ActivityLogWindow(self.pet_name, self)
        return self.activity_log

    @staticmethod
    def _format_arguments(arguments):
        """Render tool arguments compactly for the log."""
        if not isinstance(arguments, dict) or not arguments:
            return ""
        parts = []
        for key, value in arguments.items():
            text = " ".join(str(value).split())
            if len(text) > 120:
                text = text[:119] + "…"
            parts.append(f"{key}: {text}")
        return "   ".join(parts)

    def on_plan_ready(self, planned):
        """Announce everything that is about to happen, before it happens."""
        if not planned:
            return
        self.current_steps = list(planned)
        spoken = voice.describe_plan(planned)
        journal.JOURNAL.record_plan(self.request_id, spoken)
        log = self._log()
        log.add_plan(spoken)
        for index, (name, arguments) in enumerate(planned):
            log.add_step(
                key=(self.turn_id, index),
                text=voice.describe_step(name, arguments),
                detail=self._format_arguments(arguments),
            )
        if NARRATE_STEPS and len(planned) > 1:
            self.say(spoken, queue=True)

    def on_step_started(self, index, tool_name, arguments):
        """Say what is happening as it starts."""
        self._log().update_step((self.turn_id, index), status=STATUS_RUNNING)
        phrase = voice.describe_step(tool_name, arguments)
        MOBILE_BRIDGE.publish_message("muno", phrase, kind="step")
        MOBILE_BRIDGE.set_status(phrase, busy=True)
        if NARRATE_STEPS:
            # For a single action the plan announcement would be redundant.
            if len(self.current_steps) == 1:
                self.say(voice.describe_step(tool_name, arguments), queue=True)
        self.status_label.setText(voice.describe_step(tool_name, arguments))

    def on_step_finished(self, index, tool_name, arguments, succeeded, result):
        """Record the outcome of one action."""
        denied = "did not approve" in str(result).lower()
        if denied:
            status = STATUS_DENIED
        elif succeeded:
            status = STATUS_DONE
        else:
            status = STATUS_FAILED
        outcome = " ".join(str(result or "").split())
        if len(outcome) > 200:
            outcome = outcome[:199] + "…"
        self._log().update_step((self.turn_id, index), status=status, outcome=outcome)

    def on_turn_summary(self, summary):
        """State plainly what was accomplished once the actions are done."""
        if not summary:
            return
        self._log().add_note(summary)
        if NARRATE_STEPS:
            self.say(summary, queue=True)

    def on_visual_speech_finished(self):
        if self.voice_worker and self.voice_worker.isRunning():
            return
        self.set_visual_state("sleeping" if self.is_sleeping else "idle")

    def set_microphone(self, enabled, announce=True):
        """Turn listening on or off.

        Off is the default: a microphone that is always live is a reasonable
        thing to want control over, and starting quiet also removes any chance of
        Muno reacting to its own voice before the user has asked it to listen.
        """
        enabled = bool(enabled)
        self.mic_enabled = enabled
        self.config["microphone"] = enabled
        save_config(self.config)
        self.update_mic_button()

        if enabled:
            if not self.credential_gate.can_request():
                self.mic_enabled = False
                self.update_mic_button()
                self._show_credential_notice()
                return
            self.start_continuous_voice()
            self.status_label.setText("Listening…")
            if announce:
                self.show_bubble("I'm listening now.")
        else:
            self.stop_continuous_voice()
            self.listen_meter.set_level(0.0)
            self.listen_meter.set_active(False)
            self.status_label.setText("Microphone off")
            if announce:
                self.show_bubble("Microphone off. Tap the mic when you want me to listen.")

    def toggle_microphone(self):
        self.set_microphone(not self.mic_enabled)

    def update_mic_button(self):
        """Reflect the microphone state clearly in the button."""
        if not getattr(self, "mic_btn", None):
            return
        on = getattr(self, "mic_enabled", False)
        self.mic_btn.setText("🎤" if on else "🔇")
        self.mic_btn.setChecked(on)
        self.mic_btn.setToolTip(
            "Listening. Click to stop." if on else "Not listening. Click to start."
        )
        # Active uses the system green; inactive recedes to a neutral fill.
        self.mic_btn.setStyleSheet(
            "QPushButton { background-color: #30d158; color: #ffffff;"
            " border: none; border-radius: 12px; padding: 4px 6px; }"
            if on else
            "QPushButton { background-color: rgba(0,0,0,28); color: #7a7a7a;"
            " border: none; border-radius: 12px; padding: 4px 6px; }"
        )

    def stop_continuous_voice(self):
        """Stop capturing audio and release the microphone."""
        listener = self.voice_listener
        self.voice_listener = None
        if listener and listener.isRunning():
            listener.stop()
            if not listener.wait(3000):
                print("Voice listener did not stop promptly.", file=sys.stderr)

    def start_continuous_voice(self):
        if (
            self._shutting_down
            or not self.credential_gate.can_request()
            or (self.voice_listener and self.voice_listener.isRunning())
        ):
            return
        self.voice_listener = StreamingVoiceWorker(self.credential_gate, self)
        self.voice_listener.transcribed.connect(self.on_voice_transcribed)
        self.voice_listener.error.connect(self.on_voice_error)
        self.voice_listener.listening.connect(self.on_listening_changed)
        self.voice_listener.level.connect(self.on_voice_level)
        self.voice_listener.speech_started.connect(self.on_speech_started)
        self.voice_listener.speech_ended.connect(self.on_speech_ended)
        self.voice_listener.discarded.connect(self.on_speech_discarded)
        self.voice_listener.interrupted.connect(self.on_user_interrupted)
        self.voice_listener.set_vocabulary(self.build_voice_vocabulary())
        self.voice_listener.start()
        self._sync_voice_listener()

    def build_voice_vocabulary(self):
        """Names the transcriber should expect, so it stops mangling them.

        Whisper accepts a hint that biases decoding. Feeding it the words that
        actually occur in this user's world turns "moono" into "Muno" and gets
        contact and repository names right.
        """
        words = [self.pet_name, self.user_name, "Muno"]
        words += ["GitHub", "WhatsApp", "Gmail", "Chrome", "YouTube", "Spotify",
                  "Notepad", "Explorer", "VS Code", "Outlook"]
        words += ["repository", "repo", "commit", "branch", "pull request",
                  "issue", "workflow", "screenshot", "calendar"]
        try:
            contacts = tools._load_contacts()
            words += [name.title() for name in list(contacts)[:25]]
        except Exception:
            pass
        try:
            words += [p.get("name", "") for p in pets.installed_pets()[:10]]
        except Exception:
            pass
        if self.sprite_pet:
            words.append(self.sprite_pet.name)
        unique = []
        for word in words:
            cleaned = str(word or "").strip()
            if cleaned and cleaned.lower() not in {u.lower() for u in unique}:
                unique.append(cleaned)
        return ", ".join(unique) + "."

    def on_voice_level(self, meter):
        """Show that Muno is hearing something, as it happens."""
        self.listen_meter.set_level(meter)

    def on_speech_started(self):
        """React the instant speech begins, before any words are known."""
        if self.is_sleeping or self._shutting_down:
            return
        self.listen_meter.set_active(True)
        if not (self.ai_worker and self.ai_worker.isRunning()):
            self.status_label.setText("Listening…")

    def on_speech_ended(self, duration):
        """Confirm the turn is over, so the pause is never ambiguous."""
        if self.is_sleeping or self._shutting_down:
            return
        self.listen_meter.set_active(False)
        if not (self.ai_worker and self.ai_worker.isRunning()):
            self.status_label.setText(f"Heard {duration:.1f}s, writing it down…")

    def on_speech_discarded(self, text):
        """Report speech that was heard but not acted on.

        Staying silent here is worse than it sounds: the user cannot tell the
        difference between a microphone that failed and a phrase that was
        deliberately ignored.
        """
        if self.is_sleeping or self._shutting_down:
            return
        heard = " ".join(str(text).split())[:60]
        self.status_label.setText(f"Ignored background noise: “{heard}”")
        self._log().add_note(f"Heard but ignored as noise: “{heard}”")
        MOBILE_BRIDGE.publish_message(
            "muno", f"(ignored background noise: “{heard}”)", kind="note"
        )
        QTimer.singleShot(2600, lambda: self.status_label.setText(
            "Sleeping" if self.is_sleeping else "Ready"
        ))

    def on_user_interrupted(self):
        """Stop talking at once because the user started speaking.

        Being talked over is the thing that makes an assistant feel like a
        recording rather than a participant, so this takes priority over
        finishing the sentence.
        """
        if self._shutting_down:
            return
        if self.voice_worker and self.voice_worker.isRunning():
            self.voice_worker.stop()
            self.voice_worker.wait(400)
        self.visual_speech_timer.stop()
        self.pet_avatar.stop_speaking()
        self.set_visual_state("idle")
        self.status_label.setText("Listening…")

    def on_voice_transcribed(self, text):
        # Always show what was heard, even if it is not acted on. Seeing the
        # transcription is how the user knows whether they were understood.
        heard = " ".join(str(text).split())
        self.status_label.setText(f"Heard: “{heard[:70]}”")
        self._log().add_note(f"Heard: “{heard}”")

        if self._shutting_down:
            return
        if self.ai_worker and self.ai_worker.isRunning():
            self._log().add_note("Still working on the previous request, so that was skipped.")
            return
        if self.is_sleeping and self._normalize_text(text) not in {
            "wake up", "wake up muno", "hey muno", "muno", "hello muno",
        }:
            return
        self.pending_source = "voice"
        self.user_input.setText(text)
        self.ask_ai()

    def on_voice_error(self, category):
        if category == "auth":
            self.credential_gate.mark_invalid()
            self._show_credential_notice()
            self.status_label.setText("Voice input paused until credentials are updated.")
        else:
            self.status_label.setText("Microphone unavailable; typing still works.")
            print(f"Voice input unavailable ({category}).", file=sys.stderr)

    def on_listening_changed(self, listening):
        if listening and not self.is_sleeping and not self._shutting_down:
            self.status_label.setText("Listening…")

    def request_exit(self):
        self.show()
        self.activateWindow()
        if self.pending_exit:
            self.show_bubble("Please say yes to exit, or no to stay.")
            return
        self.pending_exit = True
        self.set_visual_state("confused")
        self.speak_or_show("Are you sure you want me to go?")

    # -- dragging ----------------------------------------------------------

    def mousePressEvent(self, event):
        """Begin a drag. Child widgets keep their own clicks, so the text field
        and buttons still behave normally."""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self._drag_last_x = event.globalPosition().toPoint().x()
        self._drag_last_time = time.monotonic()
        self._dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        """Follow the cursor and face the direction of travel."""
        if not self._dragging or self._drag_offset is None:
            super().mouseMoveEvent(event)
            return

        position = event.globalPosition().toPoint()
        geometry = self._screen_geometry()
        self.move(self._clamped_position(position - self._drag_offset, geometry))

        # Work out direction and speed from the movement since the last event.
        now = time.monotonic()
        elapsed_ms = max(1.0, (now - self._drag_last_time) * 1000.0)
        delta_x = position.x() - self._drag_last_x

        # A small dead zone stops tiny jitter from flipping which way it faces.
        if abs(delta_x) >= 2:
            speed = abs(delta_x) / elapsed_ms
            self.pet_avatar.set_drag_direction(1 if delta_x > 0 else -1, speed)
            self._drag_last_x = position.x()
            self._drag_last_time = now
        event.accept()

    def mouseReleaseEvent(self, event):
        """Let go: stop the run cycle and settle back to idling."""
        if not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.pet_avatar.set_drag_direction(0)

        # Remember where it was left so it reappears there next time.
        self.config["position"] = [self.x(), self.y()]
        save_config(self.config)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        """A double click sends the companion home to its corner."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.move_home()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clamped_position(self, point, geometry):
        max_x = max(geometry.left(), geometry.right() - self.width() + 1)
        max_y = max(geometry.top(), geometry.bottom() - self.height() + 1)
        return QPoint(
            min(max(point.x(), geometry.left()), max_x),
            min(max(point.y(), geometry.top()), max_y),
        )

    def _startup_arrived(self):
        if self.credential_gate.can_request():
            self.speak_or_show(
                f"Hey {self.user_name}, I'm {self.pet_name}. I'm right here when you need me."
            )
        else:
            self._show_credential_notice()

    def play_startup_walk(self):
        screen = QApplication.primaryScreen().availableGeometry()
        bottom_right = self._clamped_position(
            QPoint(screen.right() - self.width() - 23, screen.bottom() - self.height() - 23), screen
        )
        # Walk to wherever the user last left it, rather than always the corner.
        saved = self.config.get("position")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                center_right = self._clamped_position(
                    QPoint(int(saved[0]), int(saved[1])), screen
                )
            except (TypeError, ValueError):
                center_right = self._clamped_position(
                    QPoint(bottom_right.x() - 190, bottom_right.y()), screen
                )
        else:
            center_right = self._clamped_position(
                QPoint(bottom_right.x() - 190, bottom_right.y()), screen
            )
        self.move(bottom_right)
        self.startup_animation = QPropertyAnimation(self, b"pos", self)
        self.startup_animation.setDuration(1800)
        self.startup_animation.setStartValue(bottom_right)
        self.startup_animation.setEndValue(center_right)
        self.startup_animation.finished.connect(self._startup_arrived)
        self.startup_animation.start()

    def move_home(self):
        screen = QApplication.primaryScreen().availableGeometry()
        home = self._clamped_position(
            QPoint(screen.right() - self.width() - 23, screen.bottom() - self.height() - 23), screen
        )
        self.home_animation = QPropertyAnimation(self, b"pos", self)
        self.home_animation.setDuration(800)
        self.home_animation.setEndValue(home)
        self.home_animation.start()

    def shutdown_and_quit(self):
        self.shutdown()
        QApplication.quit()

    def shutdown(self):
        """Stop timers, audio, requests, capture, and instance ownership cleanly."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self.is_recording = False
        for timer in (
            self.capture_timer, self.duration_timer, self.flash_timer,
            self.visual_speech_timer, self.pet_avatar.talk_timer,
            self.pet_avatar.blink_timer, self.pet_avatar.idle_timer,
            getattr(self, "checkin_timer", None),
        ):
            if timer:
                timer.stop()
        if self.mobile_poll_timer:
            self.mobile_poll_timer.stop()
        if self.mobile_server:
            self.mobile_server.stop()
            self.mobile_server = None
        try:
            activity.MONITOR.stop()
        except Exception:
            pass
        # An unattended clicker must never outlive the app.
        if self.region_watch and self.region_watch.is_running():
            self.region_watch.stop("Muno closed")
        try:
            journal.JOURNAL.end_session()
        except Exception:
            pass
        for animation_name in ("startup_animation", "home_animation"):
            animation = getattr(self, animation_name, None)
            if animation:
                animation.stop()
        if self.voice_listener and self.voice_listener.isRunning():
            self.voice_listener.stop()
            if not self.voice_listener.wait(11000):
                print("Voice listener did not stop before the shutdown timeout.", file=sys.stderr)
        if self.voice_worker and self.voice_worker.isRunning():
            self.voice_worker.stop()
            if not self.voice_worker.wait(6000):
                print("Voice output did not stop before the shutdown timeout.", file=sys.stderr)
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.requestInterruption()
            if not self.ai_worker.wait(11000):
                print("AI worker did not stop before the shutdown timeout.", file=sys.stderr)
        self.pet_avatar.stop_speaking()
        if self.instance_guard:
            self.instance_guard.close()
        self.tray_icon.hide()

    def initUI(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedSize(self.COMPACT_WIDTH, self.COMPACT_HEIGHT)
        self.setWindowTitle(self.pet_name)

        central_widget = QWidget()
        central_widget.setObjectName("transparentRoot")
        central_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central_widget.setAutoFillBackground(False)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 4, 5, 5)
        layout.setSpacing(2)

        personality_data = PERSONALITIES.get(self.current_personality, PERSONALITIES["Muno"])

        self.speech_bubble = SpeechBubble(central_widget)
        self.speech_bubble.dismissed.connect(self._restore_compact_window)
        layout.addWidget(self.speech_bubble, 0, Qt.AlignmentFlag.AlignHCenter)

        # Animated human companion
        self.pet_avatar = AnimatedAvatar(central_widget)
        layout.addWidget(self.pet_avatar, 0, Qt.AlignmentFlag.AlignHCenter)
        # Restore a previously chosen appearance, if its files are still present.
        self.apply_sprite_pet(self.config.get("pet_sprite"))

        # Live microphone feedback, directly under the pet.
        self.listen_meter = ListenMeter(central_widget)
        self.listen_meter.setFixedWidth(110)
        layout.addWidget(self.listen_meter, 0, Qt.AlignmentFlag.AlignHCenter)
        self.mic_btn = None

        recording_status_layout = QHBoxLayout()
        recording_status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recording_dot = QLabel("●", self)
        self.recording_dot.setStyleSheet("color: #f38ba8; font-size: 16px;")
        self.recording_dot.hide()
        self.recording_label = QLabel("Not recording", self)
        self.recording_label.setStyleSheet("color: #cdd6f4; font-weight: bold;")
        self.recording_duration = QLabel("00:00:00", self)
        self.recording_duration.setStyleSheet("color: #f9e2af; font-family: Consolas; font-weight: bold;")
        self.recording_duration.hide()
        recording_status_layout.addWidget(self.recording_dot)
        recording_status_layout.addWidget(self.recording_label)
        recording_status_layout.addWidget(self.recording_duration)
        layout.addLayout(recording_status_layout)

        self.info_label = QLabel(
            f"{self.user_name}, meet {self.pet_name}. Specialty: {self.current_personality}.", self
        )
        self.info_label.setStyleSheet("qproperty-alignment: AlignCenter; font-weight: bold;")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.status_label = QLabel("Ready to assist. What do you need?", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.chat_output = QTextEdit(self)
        self.chat_output.setReadOnly(True)
        self.chat_output.hide()  # Internal transcript; user-facing replies use the bubble.
        layout.addWidget(self.chat_output)

        # Text entry with the microphone control beside it, so the way to talk
        # and the way to stop it being heard sit together.
        input_row = QHBoxLayout()
        input_row.setSpacing(3)

        self.user_input = QLineEdit(self)
        self.user_input.setPlaceholderText(f"Talk to {self.pet_name}…")
        self.user_input.returnPressed.connect(self.ask_ai)
        input_row.addWidget(self.user_input, 1)

        self.mic_btn = QPushButton("🔇", self)
        self.mic_btn.setCheckable(True)
        self.mic_btn.setFixedWidth(30)
        self.mic_btn.clicked.connect(self.toggle_microphone)
        input_row.addWidget(self.mic_btn, 0)

        # An armed watch clicks unattended, so it gets a permanent, visible
        # control rather than living only in a menu.
        self.watch_btn = QPushButton("⊙", self)
        self.watch_btn.setFixedWidth(28)
        self.watch_btn.clicked.connect(self.toggle_region_watch)
        input_row.addWidget(self.watch_btn, 0)
        layout.addLayout(input_row)

        btn_layout = QHBoxLayout()

        self.send_btn = QPushButton("Send", self)
        self.send_btn.clicked.connect(self.ask_ai)

        self.scan_btn = QPushButton("Snap", self)
        self.scan_btn.clicked.connect(self.scan_screen)

        self.record_btn = QPushButton("● Record", self)
        self.record_btn.clicked.connect(self.start_recording)

        self.stop_btn = QPushButton("■ Stop", self)
        self.stop_btn.clicked.connect(self.stop_recording)
        self.stop_btn.setEnabled(False)

        self.settings_btn = QPushButton("Settings", self)
        self.settings_btn.clicked.connect(self.open_settings)

        # Small always-visible way into the transparency panel.
        self.log_btn = QPushButton("Log", self)
        self.log_btn.setToolTip("Show everything Muno has done")
        self.log_btn.clicked.connect(self.open_activity_log)

        self.close_btn = QPushButton("Exit", self)
        self.close_btn.clicked.connect(self.request_exit)

        btn_layout.addWidget(self.send_btn)
        btn_layout.addWidget(self.scan_btn)
        btn_layout.addWidget(self.log_btn)
        btn_layout.addWidget(self.record_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.settings_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)
        for control in (
            self.send_btn, self.record_btn, self.stop_btn, self.close_btn,
            self.recording_dot, self.recording_label, self.recording_duration,
            self.info_label, self.status_label,
        ):
            control.hide()

        self.setStyleSheet("""
            QMainWindow, QWidget#transparentRoot {
                background: transparent;
                border: none;
            }
            QLabel {
                background: transparent;
                color: #1d1d1f;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 9px;
            }
            QLineEdit {
                min-height: 24px;
                background-color: rgba(245, 245, 247, 250);
                color: #1d1d1f;
                border: 1px solid rgba(0, 0, 0, 22);
                border-radius: 12px;
                padding: 3px 11px;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 10px;
                selection-background-color: #0066cc;
            }
            QLineEdit:focus {
                border: 1px solid #0071e3;
            }
            QTextEdit {
                background-color: rgba(28, 28, 30, 235);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 26);
                border-radius: 11px;
            }
            QPushButton {
                background-color: #0066cc;
                color: #ffffff;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 10px;
                border: none;
                border-radius: 12px;
                padding: 5px 11px;
            }
            QPushButton:hover { background-color: #0071e3; }
            QPushButton:pressed { background-color: #0071e3; }
        """)

    def show_welcome(self):
        """Give new users a clear, safe first-run path."""
        QMessageBox.information(
            self,
            "Welcome to Muno",
            "Open Settings to choose your name and Muno's specialty.\n\n"
            "To enable AI and voice, set GROQ_API_KEY in your Windows environment, then restart Muno. "
            "Muno will always ask before typing, clicking, or using keyboard shortcuts in another app.",
        )

    def apply_sprite_pet(self, pet_id):
        """Load a downloaded pet's artwork, or fall back to the drawn Muno."""
        if not pet_id:
            self.sprite_pet = None
            self.pet_avatar.set_sprite_pet(None)
            return True
        sprite = pets.load_pet(pet_id)
        if sprite is None:
            print(f"Pet '{pet_id}' is not available; using the built-in look.",
                  file=sys.stderr)
            self.sprite_pet = None
            self.pet_avatar.set_sprite_pet(None)
            return False
        self.sprite_pet = sprite
        self.pet_avatar.set_sprite_pet(sprite)
        return True

    def open_pet_picker(self):
        """Let the user choose the companion's appearance."""
        dialog = PetPickerDialog(self.config.get("pet_sprite"), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen_pet_id()
        if not self.apply_sprite_pet(chosen):
            QMessageBox.warning(
                self, "Couldn't load that pet",
                "That pet's artwork could not be read, so I've kept my usual look.",
            )
            return
        self.config["pet_sprite"] = chosen
        save_config(self.config)
        if self.sprite_pet:
            self.speak_or_show(
                f"I'm {self.sprite_pet.name} now. Artwork by {self.sprite_pet.owner}."
            )
        else:
            self.speak_or_show("Back to my usual self.")

    def open_settings(self):
        """Open settings dialog to change personality and name."""
        dialog = SettingsDialog(self.current_personality, self.user_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            previous_personality = self.current_personality
            if dialog.clear_memory_requested:
                clear_conversation(previous_personality)
                self.conversation_history = []
            self.current_personality = dialog.get_personality()
            self.user_name = dialog.get_user_name()
            self.config["personality"] = self.current_personality
            self.config["user_name"] = self.user_name
            self.config["pet_name"] = self.pet_name
            save_config(self.config)
            self.setWindowTitle(self.pet_name)
            self.info_label.setText(
                f"{self.user_name}, meet {self.pet_name}. Specialty: {self.current_personality}."
            )
            self.chat_output.clear()
            if self.current_personality != previous_personality:
                self.conversation_history = load_conversation(self.current_personality)
            self.status_label.setText(
                "Memory cleared." if dialog.clear_memory_requested else "Settings saved."
            )
            self.set_visual_state("idle")
            if dialog.pet_picker_requested:
                QTimer.singleShot(0, self.open_pet_picker)

    def scan_screen(self):
        """Capture a single screenshot for vision analysis on next message."""
        self.set_visual_state("excited")
        self.status_label.setText("Capturing screen...")
        QApplication.processEvents()

        # Hide window so it doesn't appear in the screenshot
        self.hide()
        QApplication.processEvents()

        try:
            screenshot = ImageGrab.grab()
            screenshot_path = os.path.join(CONFIG_DIR, "canvas.png")
            screenshot.save(screenshot_path, "PNG")

            with open(screenshot_path, "rb") as f:
                self.pending_screenshot_base64 = base64.b64encode(f.read()).decode('utf-8')

            self.show()
            self.set_visual_state("surprised")
            self.status_label.setText("Screen captured! Ask me anything about what I see.")
            self.chat_output.append("\n📸 *Screen captured. Next message will include visual context.*")

            # Return to idle after a moment.
            QTimer.singleShot(2000, lambda: self.set_visual_state("idle"))
        except Exception as e:
            self.show()
            self.set_visual_state("error")
            self.status_label.setText("Screen capture failed.")
            self.chat_output.append(f"\n❌ Screenshot error: {e}")
            QTimer.singleShot(3000, lambda: self.set_visual_state("idle"))

    def start_recording(self):
        """Start sampling screen frames until the user explicitly presses Stop."""
        if self.is_recording:
            return

        self.recording_directory = os.path.join(CONFIG_DIR, f"recording_{int(time.time())}")
        os.makedirs(self.recording_directory, exist_ok=True)
        self.recording_frame_paths = []
        self.is_recording = True
        self.recording_clock.start()
        self.record_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.recording_dot.show()
        self.recording_duration.show()
        self.recording_label.setText("RECORDING")
        self.set_visual_state("surprised")
        self.status_label.setText("Recording your screen. Press Stop when you are finished.")
        self.capture_recording_frame()
        self.capture_timer.start(FRAME_INTERVAL_MS)
        self.duration_timer.start(250)
        self.flash_timer.start(500)

    def capture_recording_frame(self):
        """Save one compressed screen frame while leaving the controls responsive."""
        if not self.is_recording or not self.recording_directory:
            return
        try:
            frame_number = len(self.recording_frame_paths)
            frame_path = os.path.join(self.recording_directory, f"frame_{frame_number:06d}.jpg")
            ImageGrab.grab().save(frame_path, "JPEG", quality=70, optimize=True)
            self.recording_frame_paths.append(frame_path)
        except Exception as e:
            self.stop_recording()
            self.status_label.setText("Recording stopped because screen capture failed.")
            self.chat_output.append(f"\n❌ Recording error: {e}")

    def update_recording_duration(self):
        elapsed_seconds = self.recording_clock.elapsed() // 1000
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.recording_duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def toggle_recording_dot(self):
        self.recording_dot.setVisible(not self.recording_dot.isVisible())

    def stop_recording(self):
        """Stop recording and make the captured timeline ready for the next question."""
        if not self.is_recording:
            return
        self.capture_timer.stop()
        self.duration_timer.stop()
        self.flash_timer.stop()
        self.is_recording = False
        self.recording_dot.hide()
        self.recording_label.setText("Recording ready")
        self.record_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.set_visual_state("thinking")
        self.status_label.setText(
            f"Recording stopped: {len(self.recording_frame_paths)} frames captured. Ask Muno about it."
        )
        self.chat_output.append(
            f"\n📹 *Recording saved ({self.recording_duration.text()}, "
            f"{len(self.recording_frame_paths)} frames). Your next question will include a timeline summary.*"
        )

    def ask_ai(self):
        """Interpret local state intents first, then send conversation to the model."""
        if self._shutting_down:
            return
        if self.ai_worker is not None and self.ai_worker.isRunning():
            self.show_bubble("One moment—I’m still thinking.")
            return

        user_text = self.user_input.text().strip()
        if not user_text:
            return
        normalized = self._normalize_text(user_text)
        self.user_input.clear()

        if self.pending_exit:
            yes_answers = {"yes", "yeah", "yep", "confirm", "do it", "exit", "please exit"}
            no_answers = {"no", "nope", "cancel", "stay", "please stay", "don't", "do not"}
            if normalized in yes_answers:
                self.pending_exit = False
                self.set_visual_state("sleeping")
                self.speak_or_show("Okay. I’ll see you next time.")
                QTimer.singleShot(1200, self.shutdown_and_quit)
            elif normalized in no_answers:
                self.pending_exit = False
                self.set_visual_state("excited")
                self.speak_or_show("Good. I’m staying right here.")
            else:
                self.speak_or_show("Please say yes if you want me to go, or no if you want me to stay.")
            return

        if re.search(r"\b(?:exit|quit|goodbye)\b", normalized) or normalized in {
            "close down", "shut down", "go away",
        }:
            self.request_exit()
            return

        if any(phrase in normalized for phrase in (
            "be quiet", "stop talking", "don't talk", "dont talk", "shh", "silence",
            "i want to type", "i'm typing",
        )):
            self.is_muted = True
            if self.voice_worker and self.voice_worker.isRunning():
                self.voice_worker.stop()
            self.set_visual_state("idle")
            self.show_bubble("Got it. I’ll stay quiet and use the bubble.")
            self._sync_voice_listener()
            return

        if any(phrase in normalized for phrase in (
            "speak again", "talk again", "unmute", "you can talk", "voice on",
        )):
            self.is_muted = False
            self.speak_or_show("I’m back on voice.")
            return

        if any(phrase in normalized for phrase in (
            "go to sleep", "take a break", "go rest", "sleep now",
        )):
            self.is_sleeping = True
            self.set_visual_state("sleeping")
            self.show_bubble("Okay. I’ll rest here until you wake me.")
            self.move_home()
            return

        if self.is_sleeping:
            wake_phrases = {"wake up", "wake up muno", "hey muno", "muno", "hello muno"}
            if normalized not in wake_phrases:
                self.set_visual_state("sleeping")
                self.show_bubble("I’m resting. Say ‘wake up, Muno’ when you need me.")
                return
            self.is_sleeping = False
            self.set_visual_state("surprised")
            self.speak_or_show("I’m awake. What’s up?")
            return

        if not self.credential_gate.can_request():
            self._show_credential_notice()
            return

        personality_data = PERSONALITIES.get(self.current_personality, PERSONALITIES["Muno"])

        # Build user content - multimodal if a screenshot or recording is pending.
        if self.recording_frame_paths:
            # Long recordings can contain thousands of frames. Vision APIs have
            # practical image limits, so retain the entire recording locally until
            # analysis, then send evenly spaced frames spanning its full duration.
            frame_count = len(self.recording_frame_paths)
            analysis_count = min(frame_count, MAX_ANALYSIS_FRAMES)
            indices = [round(i * (frame_count - 1) / max(analysis_count - 1, 1)) for i in range(analysis_count)]
            user_content = [
                {"type": "text", "text": (
                    f"[User Query]: {user_text}\n"
                    f"[Context]: Below are {analysis_count} representative frames selected from a "
                    f"{frame_count}-frame desktop recording. Analyze changes across its timeline."
                )}
            ]
            try:
                for index in indices:
                    with open(self.recording_frame_paths[index], "rb") as frame_file:
                        frame_b64 = base64.b64encode(frame_file.read()).decode("utf-8")
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}
                    })
            except OSError as e:
                self.status_label.setText("Could not read the recording frames.")
                self.chat_output.append(f"\n❌ Recording read error: {e}")
                return
            if self.recording_directory:
                shutil.rmtree(self.recording_directory, ignore_errors=True)
            self.recording_frame_paths = []
            self.recording_directory = None
            use_vision = True
        elif self.pending_screenshot_base64:
            # Single frame snapshot
            user_content = [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self.pending_screenshot_base64}"
                    }
                }
            ]
            self.pending_screenshot_base64 = None
            use_vision = True
        else:
            user_content = user_text
            use_vision = False

        # Store in conversation history - for vision messages, only keep the text
        # portion so future non-vision API calls don't break on list content
        self.conversation_history.append({"role": "user", "content": user_text})
        self.conversation_history = self.conversation_history[-MAX_MEMORY_MESSAGES:]
        save_conversation(self.current_personality, self.conversation_history)

        self.chat_output.append(f"\nYou: {user_text}")
        # Start a new group in the transparency panel for this request.
        self.turn_id += 1
        self.current_steps = []
        self._log().add_request(user_text)
        # Durable record of the prompt, and where it came from, so a later
        # duplicate check can say what was asked and by which route.
        self.request_id = journal.JOURNAL.record_request(
            user_text,
            source=self.pending_source,
            personality=self.current_personality,
        )
        self.pending_source = "desktop"
        self.speech_bubble.dismiss()
        self.set_visual_state("thinking")
        self.status_label.setText("Thinking…")
        self.send_btn.setEnabled(False)
        self.user_input.setEnabled(False)
        QApplication.processEvents()

        # Build messages for API call
        # For vision: replace the last user message content with the multimodal version
        messages = [{
            "role": "system",
            "content": build_system_prompt(personality_data["system_prompt"], self.pet_name),
        }] + self.conversation_history
        if use_vision:
            messages[-1] = {"role": "user", "content": user_content}
        self.ai_worker = AIWorker(
            messages,
            self.credential_gate,
            use_vision=use_vision,
            request_id=self.request_id,
            parent=self,
        )
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.error.connect(self.on_ai_error)
        self.ai_worker.tool_use.connect(self.on_tool_used)
        self.ai_worker.approval_requested.connect(self.request_tool_approval)
        self.ai_worker.plan_ready.connect(self.on_plan_ready)
        self.ai_worker.step_started.connect(self.on_step_started)
        self.ai_worker.step_finished.connect(self.on_step_finished)
        self.ai_worker.turn_summary.connect(self.on_turn_summary)
        self.ai_worker.start()
        self._sync_voice_listener()

    def start_mobile_server(self):
        """Serve the phone interface, if enabled."""
        if not MOBILE_ENABLED:
            return
        self.mobile_server = MobileServer(bridge=MOBILE_BRIDGE, pet_name=self.pet_name)
        if self.mobile_server.start():
            self.mobile_poll_timer = QTimer(self)
            self.mobile_poll_timer.timeout.connect(self.drain_mobile_requests)
            self.mobile_poll_timer.start(500)
            MOBILE_BRIDGE.set_status("Ready", busy=False)
            print(f"Phone access ready at {self.mobile_server.url()}", file=sys.stderr)
        else:
            print(
                f"Phone access unavailable: {self.mobile_server.error}", file=sys.stderr
            )
            self.mobile_server = None

    def drain_mobile_requests(self):
        """Run anything the phone asked for, on the UI thread."""
        if not self.mobile_server:
            return
        for text in MOBILE_BRIDGE.drain():
            if not text:
                continue
            if self.ai_worker and self.ai_worker.isRunning():
                MOBILE_BRIDGE.publish_message(
                    "muno", "One moment, I'm still finishing the last thing.", kind="note"
                )
                continue
            self.show()
            self.pending_source = "phone"
            self.user_input.setText(text)
            self.ask_ai()

    # -- writing into a field ----------------------------------------------

    def begin_write_into_field(self, text):
        """Ask the user to point at a field, then type the given text there.

        The text is composed before this is called, so what gets typed is already
        settled. All that remains is the one thing reasoning cannot supply: which
        field on screen is meant.
        """
        self._pending_write_text = str(text or "")
        if not self._pending_write_text.strip():
            self.show_bubble("Tell me what to write and I'll put it in the field.")
            return

        preview = " ".join(self._pending_write_text.split())[:90]
        self._log().add_note(f"Waiting for you to point at a field. Text: “{preview}”")

        self.hide()
        QApplication.processEvents()
        self._write_overlay = PointOverlay(
            watch_region=None,
            prompt="Click the field where I should type it",
        )
        self._write_overlay.chosen.connect(self._on_write_target_chosen)
        self._write_overlay.show()
        self._write_overlay.raise_()
        self._write_overlay.activateWindow()

    def _on_write_target_chosen(self, point):
        try:
            self.show()
            text = getattr(self, "_pending_write_text", "")
            self._pending_write_text = ""
            if not point or not text:
                self.status_label.setText("Writing cancelled.")
                return

            preview = " ".join(text.split())
            shown = preview if len(preview) <= 600 else preview[:600] + "…"
            confirm = QMessageBox(self)
            confirm.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            confirm.setWindowTitle("Type this into that field?")
            confirm.setIcon(QMessageBox.Icon.Question)
            confirm.setText(
                f"I'll click {point[0]}, {point[1]} and type:\n\n{shown}\n\n"
                "It goes into whatever is there, so make sure it's the right field."
            )
            confirm.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            confirm.setDefaultButton(QMessageBox.StandardButton.No)
            if confirm.exec() != QMessageBox.StandardButton.Yes:
                self.status_label.setText("Writing cancelled.")
                self._log().add_note("You declined to write into the field.")
                return

            perform = watcher.type_text_action(text, point=point)
            result = perform((point[0], point[1], 1, 1), None)
            self._log().add_note(f"Wrote into the field at {point}: {result}")
            journal.JOURNAL.record_action(
                self.request_id, "write_into_field",
                {"point": list(point), "characters": len(text)},
                journal.STATUS_OK, str(result), None,
            )
            self.speak_or_show("Written into that field for you.")
        except Exception as exc:
            self._recover_from_watch_error("writing into the field", exc)

    def look_at_region(self, region, question):
        """Ask the vision model about one part of the screen.

        Only the chosen region is sent, never the whole screen. That keeps the
        request small, keeps unrelated windows out of it, and means a watch set
        on a status panel cannot quietly capture everything else on the desktop.
        """
        if not self.credential_gate.can_request():
            return "I can't look at that without an API key."
        try:
            image = watcher.grab_region(region)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        except Exception as exc:
            return f"could not capture that area: {exc}"

        client = self.credential_gate.create_client()
        if client is None:
            return "I can't look at that right now."
        try:
            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            f"{question}\n\n"
                            "Answer in one short sentence. If the answer is a "
                            "yes-or-no question, begin with Yes or No."
                        )},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    ],
                }],
                timeout=AIWorker.VISION_TIMEOUT,
            )
            return plain_speech(response.choices[0].message.content)
        except Exception as exc:
            return f"could not read that area: {str(exc)[:120]}"

    def _watch_notifier(self, label):
        """Build a callback a watch can use to tell the user something."""
        def notify(message):
            text = " ".join(str(message).split())
            self._log().add_note(text)
            MOBILE_BRIDGE.publish_message("muno", text, kind="note")
            # Queue the speech so several findings do not talk over each other.
            QTimer.singleShot(0, lambda: self.say(text, queue=True))
            QTimer.singleShot(0, lambda: self.show_bubble(text))
        return notify

    def _pairing_qr_pixmap(self, url, target=260):
        """Render the pairing link as a QR code, if that ability is installed.

        This is an example of an added capability being used rather than merely
        present: without it the dialog can only offer a long URL to type.
        """
        try:
            capabilities.activate_extensions()
            import qrcode  # Installed on demand, so imported lazily.

            code = qrcode.QRCode(box_size=8, border=2)
            code.add_data(url)
            code.make(fit=True)
            image = code.make_image(fill_color="black", back_color="white")

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pixmap = QPixmap()
            if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
                return None
            return pixmap.scaled(
                target, target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        except Exception:
            return None

    # -- watching a region -------------------------------------------------

    def start_region_watch(self):
        """Map a screen area to watch, then the exact spot to click.

        Two separate choices, because the thing that signals "your turn" is
        usually not the thing you press. A prompt may appear as a line of text
        while the button sits elsewhere, so watching and clicking are picked
        independently.
        """
        self._log().add_note("Screen watch: step 1, waiting for you to drag an area.")

        # Get out of the way so the overlay covers what the user wants to pick.
        self.hide()
        QApplication.processEvents()

        self._region_overlay = RegionOverlay(
            "Step 1 of 3  ·  Drag around the area to WATCH  "
            "(have the prompt showing now)"
        )
        self._region_overlay.chosen.connect(self._on_region_chosen)
        self._region_overlay.show()
        self._region_overlay.raise_()
        self._region_overlay.activateWindow()

    def _on_region_chosen(self, region):
        # Anything raised in here reaches Qt's signal machinery, where it used to
        # end the process. Setting up a watch is never worth losing the app over.
        try:
            if not region:
                self.show()
                self.status_label.setText("Watch cancelled.")
                self._log().add_note(
                    "Screen watch: cancelled at step 1, or the drag was too small."
                )
                return

            self._pending_snapshot = watcher.grab_region(region)
            self._pending_region = region
            self._log().add_note(f"Screen watch: step 2, area {region} captured.")

            # Step two: where to click. Still hidden so the overlay covers the screen.
            self._point_overlay = PointOverlay(
                watch_region=region,
                prompt="Step 2 of 3  ·  Click exactly where I should CLICK",
            )
            self._point_overlay.chosen.connect(self._on_click_point_chosen)
            self._point_overlay.show()
            self._point_overlay.raise_()
            self._point_overlay.activateWindow()
        except Exception as exc:
            self._recover_from_watch_error("choosing the area", exc)

    def _recover_from_watch_error(self, stage, exc):
        """Report a setup failure and carry on rather than dying."""
        import traceback

        detail = f"{type(exc).__name__}: {exc}"
        print(f"Screen watch failed while {stage}: {detail}", file=sys.stderr)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CRASH_LOG, "a", encoding="utf-8") as handle:
                handle.write(
                    f"\n{'=' * 60}\n{time.strftime('%Y-%m-%d %H:%M:%S')}  "
                    f"screen watch / {stage}\n"
                )
                traceback.print_exc(file=handle)
        except Exception:
            pass
        self._pending_region = None
        self._pending_snapshot = None
        self.show()
        self.status_label.setText("Screen watch setup failed.")
        self._log().add_note(f"Screen watch failed while {stage}: {detail}")
        QMessageBox.warning(
            self, "Couldn't set that up",
            f"Something went wrong while {stage}.\n\n{detail}\n\n"
            "I've written the details to crash.log and I'm still running.",
        )

    def _on_click_point_chosen(self, point):
        try:
            self._finish_watch_setup(point)
        except Exception as exc:
            self._recover_from_watch_error("setting up the watch", exc)

    def _finish_watch_setup(self, point):
        self.show()
        region = getattr(self, "_pending_region", None)
        snapshot = getattr(self, "_pending_snapshot", None)
        if region is None or snapshot is None:
            self.status_label.setText("Watch cancelled.")
            return

        buffer = io.BytesIO()
        snapshot.save(buffer, format="PNG")
        preview = QPixmap()
        preview.loadFromData(buffer.getvalue(), "PNG")

        # Bring the companion back and to the front first. This dialog follows two
        # full-screen overlays, and parented to a hidden window it could open
        # behind everything, which looks exactly like nothing happening.
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.processEvents()

        self._log().add_note(
            f"Screen watch: area {region} chosen, click target {point}. "
            "Waiting for you to confirm the settings."
        )

        dialog = WatchSetupDialog(region, preview, click_point=point, parent=self)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        outcome = dialog.exec()
        if outcome != QDialog.DialogCode.Accepted:
            self.status_label.setText("Watch cancelled.")
            self._log().add_note("Screen watch: you cancelled the settings dialog.")
            return
        settings = dialog.settings()

        name = settings["name"] or f"watch {watcher.MANAGER.count_active() + 1}"
        notifier = self._watch_notifier(name)

        reaction = settings["action"]
        if reaction == "enter":
            action = watcher.press_key_action("enter")
            where = "press Enter"
        elif reaction == "notify":
            action = watcher.notify_action(notifier, name)
            where = "tell you about it"
        elif reaction == "report":
            action = watcher.report_action(
                notifier, name,
                save_dir=os.path.join(CONFIG_DIR, "watch_evidence"),
            )
            where = "tell you and save a picture"
        elif reaction == "point" and settings["click_point"]:
            action = watcher.click_point_action(settings["click_point"])
            where = f"click {settings['click_point'][0]}, {settings['click_point'][1]}"
        else:
            action = watcher.click_centre_action()
            where = "click the middle of the watched area"

        trigger = settings.get("trigger", watcher.TRIGGER_APPEARS)
        question = settings.get("question", "")
        when = {
            watcher.TRIGGER_APPEARS: "that exact thing appears again",
            watcher.TRIGGER_CHANGES: "the area changes",
            watcher.TRIGGER_ASK: "I see what you asked about",
        }.get(trigger, "it matches")
        watch = watcher.RegionWatch(
            region=region,
            template_image=snapshot,
            action=action,
            label=name,
            threshold=settings["threshold"],
            interval=settings["interval"],
            max_actions=settings["max_actions"],
            expiry_minutes=settings["expiry_minutes"],
            on_action=lambda count, score, result, label=name:
                self._on_watch_acted(label, count, score, result),
            on_finish=lambda reason, label=name: self._on_watch_finished(label, reason),
            trigger=trigger,
            question=question,
            observer=self.look_at_region,
        )
        label = watcher.MANAGER.add(name, watch)
        if not label:
            QMessageBox.warning(
                self, "Couldn't start watching",
                "I couldn't build a picture of that area to compare against.",
            )
            return

        self.update_watch_indicator()
        self._log().add_note(
            f"Screen watch ARMED as '{label}': when {when}, I'll {where}. "
            f"Checking every {settings['interval']:.0f}s, up to "
            f"{settings['max_actions']} times."
            + (f" Looking for: {question}" if question else "")
        )
        journal.JOURNAL.record_action(
            self.request_id, "start_region_watch",
            {"name": label, "region": list(region),
             "click_point": list(settings["click_point"] or []),
             "action": settings["action"],
             "trigger": trigger,
             "question": question,
             "max_actions": settings["max_actions"]},
            journal.STATUS_OK, "Region watch armed", None,
        )
        self.speak_or_show(
            f"Watching {label}. When {when} I'll {where}, up to "
            f"{settings['max_actions']} times."
        )

    def _on_watch_acted(self, label, count, score, result):
        """Record each automatic action. Runs on the watcher thread."""
        message = f"[{label}] handled it ({count}), match {score:.0%}: {result}"
        self._log().add_note(message)
        MOBILE_BRIDGE.publish_message("muno", message, kind="note")
        journal.JOURNAL.record_action(
            self.request_id, "region_watch_action",
            {"watch": label, "count": count, "similarity": round(float(score), 3)},
            journal.STATUS_OK, str(result), None,
        )

    def _on_watch_finished(self, label, reason):
        self._log().add_note(f"[{label}] stopped watching: {reason}.")
        MOBILE_BRIDGE.publish_message(
            "muno", f"Stopped watching {label}: {reason}.", kind="note"
        )

    def stop_region_watch(self, name=None, reason="stopped by you"):
        stopped = watcher.MANAGER.stop(name, reason)
        self.update_watch_indicator()
        return stopped

    def toggle_region_watch(self):
        if watcher.MANAGER.count_active():
            stopped = self.stop_region_watch()
            self.show_bubble(
                f"Stopped watching {', '.join(stopped)}." if stopped
                else "Stopped watching."
            )
        else:
            self.start_region_watch()

    def update_watch_indicator(self):
        """Make armed watches impossible to forget about."""
        count = watcher.MANAGER.count_active()
        if getattr(self, "watch_btn", None):
            self.watch_btn.setText(f"👁{count}" if count else "⊙")
            self.watch_btn.setToolTip(
                watcher.MANAGER.describe() if count
                else "Watch part of the screen and handle a repeating prompt"
            )
            self.watch_btn.setStyleSheet(
                "QPushButton { background-color: #ff9f0a; color: #ffffff;"
                " border: none; border-radius: 12px; padding: 4px 5px; }"
                if count else
                "QPushButton { background-color: rgba(0,0,0,28); color: #7a7a7a;"
                " border: none; border-radius: 12px; padding: 4px 6px; }"
            )
        if getattr(self, "watch_action", None):
            self.watch_action.setText(
                f"🛑 Stop Watching ({count})" if count else "👁 Watch Part of Screen"
            )

    def show_pairing_details(self):
        """Show how to connect a phone, and warn if the network will block it."""
        if not self.mobile_server:
            QMessageBox.information(
                self,
                "Phone access is off",
                "Phone access isn't running. Set MUNO_MOBILE=1 and restart me to turn "
                "it on.",
            )
            return

        url = self.mobile_server.url()
        warning = ""
        if _network_is_public():
            warning = (
                "\n\nHeads up: this network is set to Public, so Windows Firewall will "
                "block your phone. Either set the network to Private, or allow inbound "
                f"TCP port {self.mobile_server.port} for Python."
            )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Connect your phone")
        dialog.setText(
            "Scan this with your phone camera, on the same Wi-Fi.\n\n"
            "On iPhone or iPad, use Share then 'Add to Home Screen' to keep it like "
            "an app." + warning
        )
        dialog.setDetailedText(url)
        dialog.setIcon(QMessageBox.Icon.Information)

        # Show a scannable code when the QR ability is installed. Typing a long
        # URL with a token by hand is the worst part of pairing a phone.
        qr_image = self._pairing_qr_pixmap(url)
        if qr_image is not None:
            dialog.setIconPixmap(qr_image)
        else:
            dialog.setText(dialog.text() + (
                "\n\nI could show a scannable code instead of this link if you ask me "
                "to add QR support."
            ))
        copy_button = dialog.addButton("Copy link", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(url)

    def request_tool_approval(self, tool_name, arguments, decision, decision_ready,
                              previous=None):
        """Ask before Muno changes another application through automation."""
        descriptions = {
            "open_app": f"open this app: {arguments.get('app_name', '')}",
            "whatsapp_message": (
                f"open a WhatsApp chat with {arguments.get('contact', '')} and type "
                f"this message:\n\n{arguments.get('message', '') or '(no message)'}\n\n"
                "Nothing is sent until you press Enter in WhatsApp."
            ),
            "whatsapp_send": (
                f"SEND this WhatsApp message to {arguments.get('contact', '')}:\n\n"
                f"{arguments.get('message', '')}\n\n"
                "This delivers the message immediately and cannot be undone."
            ),
            "browser_type": (
                f"type this into the current web page and submit it:\n\n"
                f"{arguments.get('text', '')}"
            ),
            "install_capability": (
                f"INSTALL new code into me: the Python package "
                f"'{arguments.get('package', '')}'"
                + (f" version {arguments.get('version')}" if arguments.get("version") else "")
                + (f"\nPurpose: {arguments.get('purpose')}" if arguments.get("purpose") else "")
                + "\n\nThis runs that package's code on your computer with the same "
                "access I have: your model key, GitHub token, mail, browser session, "
                "and files. Only agree if you recognise and trust this package."
            ),
            "uninstall_capability": (
                f"remove the installed package '{arguments.get('name', '')}' from me"
            ),
            "send_email": (
                f"SEND this email to {arguments.get('to', '')}"
                + (f", copying {arguments.get('cc')}" if arguments.get("cc") else "")
                + (f"\nAttaching: {arguments.get('attachments')}" if arguments.get("attachments") else "")
                + f"\n\nSubject: {arguments.get('subject', '')}\n\n{arguments.get('body', '')}\n\n"
                "Once sent, it cannot be recalled."
            ),
            "github_create_repo": (
                f"create a GitHub repository named '{arguments.get('name', '')}'"
                + (" (private)" if arguments.get("private") is not False else " (public)")
            ),
            "github_delete_repo": (
                f"PERMANENTLY DELETE the GitHub repository '{arguments.get('repo', '')}'.\n\n"
                "This removes its code, issues, and history, and cannot be undone."
            ),
            "github_update_repo": f"change settings on the GitHub repository '{arguments.get('repo', '')}'",
            "github_upload_file": (
                f"upload '{arguments.get('file', '')}' to the GitHub repository "
                f"'{arguments.get('repo', '')}'"
            ),
            "github_delete_file": (
                f"delete '{arguments.get('path', '')}' from the GitHub repository "
                f"'{arguments.get('repo', '')}'"
            ),
            "github_create_issue": (
                f"open an issue on '{arguments.get('repo', '')}' titled:\n\n"
                f"{arguments.get('title', '')}"
            ),
            "github_comment_issue": (
                f"post this public comment on {arguments.get('repo', '')} "
                f"#{arguments.get('number', '')}:\n\n{arguments.get('body', '')}"
            ),
            "github_create_pull_request": (
                f"open a pull request on '{arguments.get('repo', '')}' titled:\n\n"
                f"{arguments.get('title', '')}"
            ),
            "github_merge_pull_request": (
                f"MERGE pull request #{arguments.get('number', '')} on "
                f"'{arguments.get('repo', '')}' into its base branch"
            ),
            "github_create_release": (
                f"publish release '{arguments.get('tag', '')}' on '{arguments.get('repo', '')}'"
            ),
            "github_run_workflow": (
                f"run the GitHub Actions workflow '{arguments.get('workflow', '')}' "
                f"on '{arguments.get('repo', '')}'"
            ),
            "write_into_field": (
                "let you point at a field, then type this into it:\n\n"
                f"{arguments.get('text', '')[:500]}"
            ),
            "type_text": f"type this into the active app:\n\n{arguments.get('text_to_type', '')}",
            "click_mouse": f"click the {arguments.get('button', 'left')} mouse button",
            "press_key": f"press the {arguments.get('key_name', '')} key",
            "hotkey": "press this shortcut: " + "+".join(arguments.get("keys", [])),
            "minimize_window": f"minimize the window matching: {arguments.get('title_substring', '')}",
            "maximize_window": f"maximize the window matching: {arguments.get('title_substring', '')}",
        }
        detail = descriptions.get(tool_name, "perform a desktop action")

        # A repeat of something already done is the case most worth flagging,
        # so it leads the prompt rather than being buried in it.
        repeat_notice = journal.describe_duplicate(previous)
        if repeat_notice:
            question = (
                f"⚠️ This looks like a repeat.\n\n{repeat_notice}\n\n"
                f"Muno wants to {detail} again.\n\nDo it anyway?"
            )
            title = "Already done before"
        else:
            question = f"Muno wants to {detail}.\n\nAllow it?"
            title = "Allow Muno to act?"

        # Mirror the prompt to the phone so it can be answered from either place.
        approval_id = None
        if self.mobile_server:
            phone_detail = f"Muno wants to {detail}."
            if repeat_notice:
                phone_detail = f"⚠️ {repeat_notice}\n\nMuno wants to {detail} again."
            approval_id = MOBILE_BRIDGE.open_approval(
                title, phone_detail, decision, decision_ready
            )

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(question)
        box.setIcon(
            QMessageBox.Icon.Warning if repeat_notice else QMessageBox.Icon.Question
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)

        # If the phone answers first, stop asking here.
        watcher = QTimer(box)
        watcher.setInterval(300)
        watcher.timeout.connect(lambda: box.reject() if decision_ready.is_set() else None)
        watcher.start()

        try:
            answered_here = box.exec()
            watcher.stop()
            if decision_ready.is_set():
                # Already resolved from the phone; leave that answer intact.
                return
            decision["approved"] = answered_here == QMessageBox.StandardButton.Yes
            decision["source"] = "desktop"
        finally:
            watcher.stop()
            if approval_id is not None:
                MOBILE_BRIDGE.clear_approval(approval_id)
            decision_ready.set()

    def refresh_file_index_if_stale(self, max_age_hours=12):
        """Build the local file catalogue when it is missing or out of date."""
        if self._shutting_down:
            return
        try:
            meta = file_index.get_meta()
            built_at = float(meta.get("built_at", 0) or 0)
            fresh = (time.time() - built_at) < max_age_hours * 3600
            if file_index.is_built() and fresh:
                return
            file_index.build_index_async()
        except Exception as exc:
            print(f"File index unavailable: {exc}", file=sys.stderr)

    def consider_checkin(self):
        """Occasionally acknowledge what the user is doing, without nagging.

        The message is composed locally, so a check-in never spends model tokens
        and never fails because of a rate limit.
        """
        if not CHECKIN_ENABLED:
            return
        # Never interrupt while busy, resting, quiet, mid-answer, or shutting down.
        if (
            self._shutting_down
            or self.is_sleeping
            or self.pending_exit
            or (self.ai_worker and self.ai_worker.isRunning())
            or (self.voice_worker and self.voice_worker.isRunning())
            or self.speech_bubble.isVisible()
            or self.is_recording
        ):
            return

        now = time.monotonic()
        if now - self.last_checkin_at < CHECKIN_MIN_GAP_SECONDS:
            return

        try:
            snapshot = activity.MONITOR.snapshot()
        except Exception:
            return
        if not snapshot.get("title"):
            return

        # Someone typing right this second does not want a bubble in the way.
        if snapshot["idle_seconds"] < 5:
            return

        message = None
        came_back = activity.MONITOR.consume_returned_from_idle()
        if came_back and snapshot["idle_seconds"] < CHECKIN_AWAY_SECONDS:
            message = f"Welcome back. Still {snapshot['description']}?"
        elif snapshot["on_task_seconds"] >= CHECKIN_LONG_TASK_SECONDS:
            minutes = int(snapshot["on_task_seconds"] // 60)
            message = (
                f"You've been {snapshot['description']} for about {minutes} minutes. "
                "Still on it, or want a break?"
            )

        if not message:
            return
        self.last_checkin_at = now
        self.last_checkin_category = snapshot["category"]
        self.set_visual_state("idle")
        self.show_bubble(message)

    def on_tool_used(self, tool_name, arguments, result):
        """Apply companion state changes from structured tool events."""
        arguments = arguments if isinstance(arguments, dict) else {}
        if tool_name == "system_sleep":
            self.is_sleeping = True
            self.pet_avatar.set_emotion("sad")
            self.move_home()
        elif tool_name == "system_mute":
            self.is_muted = arguments.get("mute", True) is not False
            if self.is_muted:
                if self.voice_worker and self.voice_worker.isRunning():
                    self.voice_worker.stop()
                self.pet_avatar.stop_speaking()
                self.pet_avatar.set_emotion("confused")
            else:
                self.pet_avatar.set_emotion("happy")
        elif tool_name == "system_exit":
            self.request_exit()
        else:
            self.pet_avatar.set_emotion("excited")
        self._sync_voice_listener()

    def on_ai_response(self, ai_reply):
        """Display one natural response without inferring state from its text."""
        ai_reply = plain_speech(ai_reply) or "Done."
        journal.JOURNAL.record_reply(self.request_id, ai_reply)
        self._log().add_reply(ai_reply)
        MOBILE_BRIDGE.publish_message("muno", ai_reply)
        MOBILE_BRIDGE.set_status("Ready", busy=False)
        self.conversation_history.append({"role": "assistant", "content": ai_reply})
        self.conversation_history = self.conversation_history[-MAX_MEMORY_MESSAGES:]
        save_conversation(self.current_personality, self.conversation_history)
        self.chat_output.append(f"\n{self.pet_name}:\n{ai_reply}")
        self.send_btn.setEnabled(True)
        self.user_input.setEnabled(True)
        self.ai_worker = None

        if self.pending_exit:
            self.pet_avatar.set_emotion("sad")
            self._sync_voice_listener()
            return

        self.pet_avatar.set_emotion("sad" if self.is_sleeping else "excited")
        self.status_label.setText("Speaking…" if not self.is_muted else "Silent")
        self.speak_or_show(ai_reply)
        if self.is_muted:
            self.pet_avatar.stop_speaking()
            self.pet_avatar.set_emotion("sad" if self.is_sleeping else "happy")
            self.status_label.setText("Sleeping" if self.is_sleeping else "Ready")
            self._sync_voice_listener()

    def on_voice_finished(self):
        """Restore companion and microphone state after playback really ends."""
        self.voice_worker = None
        self._set_speaking_flag(False)
        self.pet_avatar.stop_speaking()
        if self.pending_exit or self.is_sleeping:
            self.pet_avatar.set_emotion("sad")
        else:
            self.pet_avatar.set_emotion("happy")
        self.status_label.setText("Sleeping" if self.is_sleeping else "Ready")
        self._sync_voice_listener()

    def on_ai_error(self, category):
        """Recover from a classified provider failure without exposing raw details."""
        category = str(category).lower()
        safe_categories = {"auth", "missing", "invalid", "model", "timeout", "network", "service"}
        if category not in safe_categories:
            category = "service"
        print(f"AI request unavailable ({category}).", file=sys.stderr)
        self.set_visual_state("error")
        self.send_btn.setEnabled(True)
        self.user_input.setEnabled(True)
        self.ai_worker = None

        if not self.pending_exit:
            if category == "auth":
                self.credential_gate.mark_invalid()
                self._show_credential_notice()
            elif category in {"missing", "invalid"}:
                self._show_credential_notice()
            else:
                messages = {
                    "model": "The configured AI model is unavailable. Check the model setting and restart Muno.",
                    "timeout": "The connection took too long. Please try again.",
                    "network": "I couldn’t reach Groq. Check your connection and try again.",
                    "service": "Groq is unavailable right now. Please try again shortly.",
                }
                self.show_bubble(messages[category])
                MOBILE_BRIDGE.publish_message("muno", messages[category], kind="note")
        MOBILE_BRIDGE.set_status("Ready", busy=False)
        self.status_label.setText("Sleeping" if self.is_sleeping else "Ready")
        self._sync_voice_listener()
        if not self.is_sleeping and not self.pending_exit:
            QTimer.singleShot(3000, lambda: self.set_visual_state("idle"))


if __name__ == '__main__':
    # Without a console there is nothing to print a traceback to, so an
    # unhandled error would simply make the companion vanish with no
    # explanation. Record it instead, and keep running where it is safe to.
    install_crash_logging()

    # Put previously installed abilities on the import path before anything else
    # tries to use them.
    capabilities.activate_extensions()

    app = QApplication(sys.argv)
    instance_guard = SingleInstanceGuard(app)
    ownership = instance_guard.acquire()
    if ownership == "secondary":
        instance_guard.close()
        sys.exit(0)
    if ownership not in {"owner", "recovered-stale"}:
        print("Muno could not establish single-instance ownership.", file=sys.stderr)
        instance_guard.close()
        sys.exit(1)

    pet = CodexPet(instance_guard)
    instance_guard.activation_requested.connect(pet.activate_existing)
    app.aboutToQuit.connect(pet.shutdown)
    pet.show()
    sys.exit(app.exec())
