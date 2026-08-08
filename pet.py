import sys
import json
import os
import time
import base64
import io
import math
import re
import shutil
import wave
import winsound
import hashlib
import threading
import requests
import numpy as np
import sounddevice as sd
from openai import OpenAI
from PIL import ImageGrab
from PyQt6.QtCore import (
    Qt, QObject, QThread, QTimer, QElapsedTimer, pyqtSignal, QPropertyAnimation,
    QRect, QPoint, QEasingCurve,
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QAction, QIcon,
    QLinearGradient, QRadialGradient,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget,
                             QTextEdit, QPushButton, QHBoxLayout, QDialog, QComboBox,
                             QLineEdit, QFormLayout, QMessageBox, QSystemTrayIcon, QMenu,
                             QSizePolicy)

# Import desktop automation tools
from tools import TOOLS_SCHEMA, TOOL_FUNCTIONS

# Config paths
CONFIG_DIR = os.path.expanduser("~/.codex_pet")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
MEMORY_FILE = os.path.join(CONFIG_DIR, "conversations.json")
MAX_MEMORY_MESSAGES = 40
os.makedirs(CONFIG_DIR, exist_ok=True)

# Non-secret provider configuration. Credentials are read lazily by CredentialGate.
OPENAI_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
HF_API_TOKEN = os.getenv("HF_API_TOKEN")

# Hugging Face TTS - facebook/mms-tts-eng is free and works on serverless inference
HF_TTS_URL = "https://api-inference.huggingface.co/models/facebook/mms-tts-eng"

FRAME_INTERVAL_MS = 1000
MAX_ANALYSIS_FRAMES = 12
TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "llama-3.1-8b-instant")
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
VOICE_MODEL = os.getenv("GROQ_VOICE_MODEL", "whisper-large-v3-turbo")
CONTINUOUS_VOICE_ENABLED = os.getenv("MUNO_CONTINUOUS_VOICE", "1") == "1"

BEHAVIOR_PATCH = """
You are embodied as a warm desktop companion whose home is the bottom-right of the screen.
Understand ordinary, flexible, colloquial speech rather than requiring exact commands.
Keep every user-facing answer brief, natural, and suitable for a floating speech bubble or spoken audio.
Never include Markdown, tables, code fences, technical schemas, or internal tool names in user-facing replies.
Respect silent mode and sleeping mode. Always ask for confirmation before exiting.
Use available background tools when the user naturally asks you to control the desktop.
""".strip()


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
    approval_requested = pyqtSignal(str, object, object, object)

    CONFIRMATION_REQUIRED = {"type_text", "click_mouse", "press_key", "hotkey", "minimize_window", "maximize_window"}

    def __init__(self, messages, credential_gate, use_vision=False, use_tools=True, parent=None):
        super().__init__(parent)
        self.messages = messages
        self.credential_gate = credential_gate
        self.use_vision = use_vision
        self.use_tools = use_tools

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
            call_params = {"model": model_name, "messages": self.messages, "timeout": 10.0}
            if self.use_tools and not self.use_vision:
                call_params.update({"tools": TOOLS_SCHEMA, "tool_choice": "auto"})

            response = client.chat.completions.create(**call_params)
            assistant_message = response.choices[0].message
            tool_calls = assistant_message.tool_calls or []
            if not tool_calls:
                self.finished.emit(plain_speech(assistant_message.content))
                return

            follow_up_messages = self.messages + [assistant_message.model_dump(exclude_none=True)]
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                args_dict = {}
                result = "The requested action is not available."
                if function_name in TOOL_FUNCTIONS:
                    try:
                        args_dict = self._parse_arguments(tool_call.function.arguments)
                        if function_name in self.CONFIRMATION_REQUIRED:
                            decision = {"approved": False}
                            decision_ready = threading.Event()
                            self.approval_requested.emit(function_name, args_dict, decision, decision_ready)
                            decision_ready.wait(60)
                            if not decision["approved"]:
                                result = "The user did not approve this desktop action."
                            else:
                                result = str(TOOL_FUNCTIONS[function_name](**args_dict))
                        else:
                            result = str(TOOL_FUNCTIONS[function_name](**args_dict))
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        print(f"Rejected unsafe arguments for {function_name}: {exc}", file=sys.stderr)
                        result = "The requested action could not be completed safely."
                    except Exception as exc:
                        print(f"Tool {function_name} failed: {exc}", file=sys.stderr)
                        result = "The requested action could not be completed."

                self.tool_use.emit(function_name, args_dict, result)
                follow_up_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

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
                timeout=10.0,
            )
            self.finished.emit(plain_speech(final_response.choices[0].message.content))
        except Exception as exc:
            self.error.emit(CredentialGate.classify_error(exc))


class VoiceWorker(QThread):
    """Fetch and play TTS audio while keeping the UI responsive."""

    def __init__(self, text, token, parent=None):
        super().__init__(parent)
        self.text = text
        self.token = token
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        if not self.token or not self._running:
            return
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            payload = {"inputs": self.text[:200]}
            response = requests.post(HF_TTS_URL, headers=headers, json=payload, timeout=5)

            if response.status_code == 200 and self._running:
                audio_path = os.path.join(CONFIG_DIR, "pet_voice.wav")
                with open(audio_path, "wb") as audio_file:
                    audio_file.write(response.content)
                with wave.open(audio_path, "rb") as wav_file:
                    duration = wav_file.getnframes() / max(wav_file.getframerate(), 1)
                winsound.PlaySound(audio_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                deadline = time.monotonic() + duration
                while self._running and time.monotonic() < deadline:
                    self.msleep(50)
                winsound.PlaySound(None, 0)
            elif response.status_code == 503:
                print("TTS model is loading; voice output was skipped.", file=sys.stderr)
            elif response.status_code != 200:
                print(f"TTS API returned status {response.status_code}.", file=sys.stderr)
        except Exception as exc:
            print(f"Voice worker error: {exc}", file=sys.stderr)


class ContinuousVoiceWorker(QThread):
    """Listen for utterances, stop at silence, and transcribe them with Groq."""
    transcribed = pyqtSignal(str)
    error = pyqtSignal(str)
    listening = pyqtSignal(bool)

    SAMPLE_RATE = 16000
    BLOCK_SIZE = 1600
    SPEECH_THRESHOLD = 650
    SILENCE_BLOCKS = 9
    MAX_BLOCKS = 120

    def __init__(self, credential_gate, parent=None):
        super().__init__(parent)
        self.credential_gate = credential_gate
        self._running = True
        self._paused = False

    def stop(self):
        self._running = False
        self._paused = False
        self.requestInterruption()

    def set_paused(self, paused):
        self._paused = paused

    def _to_wav(self, chunks):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.SAMPLE_RATE)
            wav_file.writeframes(b"".join(chunks))
        return buffer.getvalue()

    def run(self):
        if not self.credential_gate.can_request():
            return
        try:
            client = self.credential_gate.create_client()
            if client is None:
                return
            with sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=self.BLOCK_SIZE,
            ) as stream:
                self.listening.emit(True)
                while self._running:
                    if self._paused:
                        self.msleep(100)
                        continue

                    chunks = []
                    silence_blocks = 0
                    speech_started = False
                    while self._running and not self._paused and len(chunks) < self.MAX_BLOCKS:
                        audio, overflowed = stream.read(self.BLOCK_SIZE)
                        if overflowed:
                            continue
                        level = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
                        if level >= self.SPEECH_THRESHOLD:
                            speech_started = True
                            silence_blocks = 0
                        elif speech_started:
                            silence_blocks += 1
                        if speech_started:
                            chunks.append(audio.tobytes())
                        if speech_started and silence_blocks >= self.SILENCE_BLOCKS:
                            break

                    if not chunks or self._paused:
                        continue
                    transcript = client.audio.transcriptions.create(
                        model=VOICE_MODEL,
                        file=("speech.wav", self._to_wav(chunks)),
                        response_format="text",
                        timeout=10.0,
                    )
                    if not self._running:
                        break
                    text = transcript if isinstance(transcript, str) else getattr(transcript, "text", "")
                    text = plain_speech(text)
                    if text:
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
        self.setStyleSheet("""
            QTextEdit {
                background-color: rgba(248, 246, 255, 246);
                color: #202033;
                border: 1px solid rgba(137, 180, 250, 235);
                border-radius: 10px;
                padding: 6px 7px;
                font-family: Segoe UI;
                font-size: 9px;
                selection-background-color: rgba(104, 100, 255, 150);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 7px;
                margin: 8px 1px 8px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(104, 100, 255, 150);
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


class AnimatedAvatar(QWidget):
    """Full-body animated Muno character with expressive face and gestures."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
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
        self.idle_timer.start(40)
        
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
        """Drive breathing, body bounce, eye movement, and gestures."""
        self.animation_phase += 0.12
        bounce_size = 3.0 if self.is_speaking else 1.8
        self.idle_bounce = math.sin(self.animation_phase) * bounce_size
        self.idle_sway = math.sin(self.animation_phase * 0.55) * 1.5
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
        self.update()

    def set_emotion(self, emotion):
        """Compatibility adapter for preserved callers; state remains canonical."""
        mapping = {
            "happy": "idle", "thinking": "thinking", "excited": "excited",
            "confused": "confused", "surprised": "surprised", "sad": "sleeping",
        }
        self.set_state(mapping.get(emotion, "idle"))
        
    def paintEvent(self, event):
        """Paint a full-body, expressive animated version of Muno."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        design_width, design_height = 280.0, 330.0
        scale = min(self.width() / design_width, self.height() / design_height)
        painter.translate(
            (self.width() - design_width * scale) / 2.0,
            (self.height() - design_height * scale) / 2.0,
        )
        painter.scale(scale, scale)
        painter.translate(self.idle_sway, self.idle_bounce)

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
        QTimer.singleShot(0, self.play_startup_walk)
        if self.is_first_run:
            QTimer.singleShot(600, self.show_welcome)
        if CONTINUOUS_VOICE_ENABLED and self.credential_gate.can_request():
            QTimer.singleShot(250, self.start_continuous_voice)
    
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
        if not self.voice_listener or not self.voice_listener.isRunning():
            return
        ai_busy = bool(self.ai_worker and self.ai_worker.isRunning())
        voice_busy = bool(self.voice_worker and self.voice_worker.isRunning())
        self.voice_listener.set_paused(self._shutting_down or ai_busy or voice_busy)

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
        if self._shutting_down:
            self._sync_voice_listener()
            return
        if self.is_muted or not HF_API_TOKEN:
            self.set_visual_state("speaking")
            visual_duration = max(1200, min(6500, len(text) * 45))
            self.visual_speech_timer.start(visual_duration)
            self._sync_voice_listener()
            return
        if self.voice_worker and self.voice_worker.isRunning():
            return
        self.visual_speech_timer.stop()
        self.set_visual_state("speaking")
        self.voice_worker = VoiceWorker(text, HF_API_TOKEN, self)
        self.voice_worker.finished.connect(self.on_voice_finished)
        self.voice_worker.start()
        self._sync_voice_listener()

    def on_visual_speech_finished(self):
        if self.voice_worker and self.voice_worker.isRunning():
            return
        self.set_visual_state("sleeping" if self.is_sleeping else "idle")

    def start_continuous_voice(self):
        if (
            self._shutting_down
            or not self.credential_gate.can_request()
            or (self.voice_listener and self.voice_listener.isRunning())
        ):
            return
        self.voice_listener = ContinuousVoiceWorker(self.credential_gate, self)
        self.voice_listener.transcribed.connect(self.on_voice_transcribed)
        self.voice_listener.error.connect(self.on_voice_error)
        self.voice_listener.listening.connect(self.on_listening_changed)
        self.voice_listener.start()
        self._sync_voice_listener()

    def on_voice_transcribed(self, text):
        if self._shutting_down or (self.ai_worker and self.ai_worker.isRunning()):
            return
        if self.is_sleeping and self._normalize_text(text) not in {
            "wake up", "wake up muno", "hey muno", "muno", "hello muno",
        }:
            return
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
        center_right = self._clamped_position(QPoint(bottom_right.x() - 190, bottom_right.y()), screen)
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
        ):
            timer.stop()
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

        self.user_input = QLineEdit(self)
        self.user_input.setPlaceholderText(f"Talk to {self.pet_name}…")
        self.user_input.returnPressed.connect(self.ask_ai)
        layout.addWidget(self.user_input)

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

        self.close_btn = QPushButton("Exit", self)
        self.close_btn.clicked.connect(self.request_exit)

        btn_layout.addWidget(self.send_btn)
        btn_layout.addWidget(self.scan_btn)
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
                color: #202033;
                font-family: Segoe UI;
                font-size: 9px;
            }
            QLineEdit {
                min-height: 20px;
                background-color: rgba(231, 231, 237, 238);
                color: #202033;
                border: 1px solid rgba(120, 120, 138, 130);
                border-radius: 5px;
                padding: 2px 5px;
                font-family: Segoe UI;
                font-size: 10px;
                selection-background-color: #6864ff;
            }
            QLineEdit:focus {
                border: 2px solid rgba(104, 100, 255, 210);
            }
            QTextEdit {
                background-color: rgba(49, 50, 68, 230);
                color: #a6e3a1;
                border: 1px solid #45475a;
                border-radius: 5px;
            }
            QPushButton {
                background-color: #89b4fa;
                color: #11111b;
                font-weight: bold;
                border-radius: 5px;
                padding: 5px;
            }
            QPushButton:hover { background-color: #b5c7ff; }
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
        save_conversation(self.current_personality, self.conversation_history)

        self.chat_output.append(f"\nYou: {user_text}")
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
            parent=self,
        )
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.error.connect(self.on_ai_error)
        self.ai_worker.tool_use.connect(self.on_tool_used)
        self.ai_worker.approval_requested.connect(self.request_tool_approval)
        self.ai_worker.start()
        self._sync_voice_listener()

    def request_tool_approval(self, tool_name, arguments, decision, decision_ready):
        """Ask before Muno changes another application through automation."""
        descriptions = {
            "type_text": f"type this into the active app:\n\n{arguments.get('text_to_type', '')}",
            "click_mouse": f"click the {arguments.get('button', 'left')} mouse button",
            "press_key": f"press the {arguments.get('key_name', '')} key",
            "hotkey": "press this shortcut: " + "+".join(arguments.get("keys", [])),
            "minimize_window": f"minimize the window matching: {arguments.get('title_substring', '')}",
            "maximize_window": f"maximize the window matching: {arguments.get('title_substring', '')}",
        }
        try:
            answer = QMessageBox.question(
                self,
                "Allow Muno to act?",
                f"Muno wants to {descriptions.get(tool_name, 'perform a desktop action')}.\n\nAllow it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            decision["approved"] = answer == QMessageBox.StandardButton.Yes
        finally:
            decision_ready.set()

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
        self.conversation_history.append({"role": "assistant", "content": ai_reply})
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
        self.status_label.setText("Sleeping" if self.is_sleeping else "Ready")
        self._sync_voice_listener()
        if not self.is_sleeping and not self.pending_exit:
            QTimer.singleShot(3000, lambda: self.set_visual_state("idle"))


if __name__ == '__main__':
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
