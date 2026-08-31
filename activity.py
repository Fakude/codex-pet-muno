"""
Activity awareness for Muno.

Muno watches which window is in front, how long it has been there, and whether
the keyboard and mouse have gone quiet. That is enough to say something useful
about what the user is doing without reading any content: window titles and
process names alone distinguish coding from browsing from writing email.

Everything here uses ctypes against the Win32 API, so it adds no dependencies
and costs almost nothing to sample.

Privacy
-------
Only window titles, process names, and timestamps are kept, in memory, for the
current session. Nothing is written to disk and nothing is sent anywhere unless
the user asks Muno a question that needs it.
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time
from collections import Counter

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_HISTORY = 720  # At one sample every 5s this is roughly the last hour.


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """Seconds since the last keyboard or mouse input, system-wide."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = kernel32.GetTickCount() - info.dwTime
    return max(0.0, millis / 1000.0)


def _window_title(handle):
    length = user32.GetWindowTextLengthW(handle)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def _process_name(handle):
    """Return the executable name owning a window, or an empty string."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    if not pid.value:
        return ""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(process)


def foreground_window():
    """Return (title, process_name) for the window currently in front."""
    handle = user32.GetForegroundWindow()
    if not handle:
        return "", ""
    return _window_title(handle), _process_name(handle)


def open_windows():
    """Return a list of (title, process_name) for visible top-level windows."""
    results = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )

    def handle_window(handle, _param):
        if not user32.IsWindowVisible(handle):
            return True
        title = _window_title(handle)
        if not title or len(title.strip()) < 2:
            return True
        results.append((title, _process_name(handle)))
        return True

    user32.EnumWindows(callback_type(handle_window), 0)
    return results


# ---------------------------------------------------------------------------
# Interpreting activity
# ---------------------------------------------------------------------------

# Matched against the process name first, then the window title. Order matters:
# more specific categories are checked before general ones.
_PROCESS_CATEGORIES = [
    ("coding", {"code.exe", "kiro.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
                "sublime_text.exe", "rider64.exe", "webstorm64.exe", "cursor.exe"}),
    ("terminal", {"windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
                  "wt.exe", "conhost.exe", "bash.exe"}),
    ("design", {"figma.exe", "photoshop.exe", "illustrator.exe", "blender.exe",
                "afdesign.exe", "afphoto.exe"}),
    ("meeting", {"teams.exe", "ms-teams.exe", "zoom.exe", "webex.exe", "slack.exe",
                 "discord.exe"}),
    ("writing", {"winword.exe", "notepad.exe", "notepad++.exe", "obsidian.exe",
                 "onenote.exe", "wordpad.exe"}),
    ("spreadsheets", {"excel.exe"}),
    ("email", {"outlook.exe", "thunderbird.exe", "hxoutlook.exe"}),
    ("media", {"vlc.exe", "spotify.exe", "music.ui.exe", "mpc-hc64.exe"}),
    ("files", {"explorer.exe"}),
    ("browsing", {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}),
]

# Browser tabs say far more than the browser name does.
_TITLE_CATEGORIES = [
    ("email", ("gmail", "outlook.com", "inbox", "proton mail", "yahoo mail", "webmail")),
    ("meeting", ("meet.google", "zoom meeting", "teams meeting", "google meet")),
    ("coding", ("github", "gitlab", "stack overflow", "localhost:", "codepen",
                "jira", "bitbucket", "pull request")),
    ("media", ("youtube", "netflix", "spotify", "twitch", "prime video")),
    ("social", ("facebook", "instagram", "twitter", " x.com", "reddit", "tiktok",
                "linkedin", "whatsapp")),
    ("shopping", ("amazon", "takealot", "ebay", "checkout", "cart")),
    ("research", ("wikipedia", "docs.", "documentation", "arxiv", "scholar")),
    ("writing", ("google docs", "notion", "docs.google")),
]

_CATEGORY_LABELS = {
    "coding": "writing code",
    "terminal": "working in a terminal",
    "design": "designing",
    "meeting": "in a meeting",
    "writing": "writing",
    "spreadsheets": "working in a spreadsheet",
    "email": "dealing with email",
    "media": "watching or listening to something",
    "files": "browsing files",
    "browsing": "browsing the web",
    "social": "on social media",
    "shopping": "shopping online",
    "research": "reading up on something",
    "unknown": "working on something",
}


def categorize(title, process_name):
    """Classify one window into a coarse activity category."""
    lowered_title = str(title or "").lower()
    lowered_process = str(process_name or "").lower()

    # A browser's tab title is more informative than the browser itself.
    is_browser = lowered_process in {
        "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"
    }
    if is_browser:
        for category, needles in _TITLE_CATEGORIES:
            if any(needle in lowered_title for needle in needles):
                return category
        return "browsing"

    for category, processes in _PROCESS_CATEGORIES:
        if lowered_process in processes:
            return category

    for category, needles in _TITLE_CATEGORIES:
        if any(needle in lowered_title for needle in needles):
            return category
    return "unknown"


def describe_category(category):
    return _CATEGORY_LABELS.get(category, _CATEGORY_LABELS["unknown"])


def _short_title(title, limit=70):
    text = " ".join(str(title or "").split())
    return text[:limit]


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class ActivityMonitor:
    """Samples the foreground window on a background thread."""

    def __init__(self, interval=5.0, idle_threshold=120.0):
        self.interval = interval
        self.idle_threshold = idle_threshold
        self._history = []           # (timestamp, title, process, category)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._task_started_at = None
        self._current_category = None
        self._returned_from_idle = False

    def start(self):
        if self._running:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="muno-activity", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False

    def _loop(self):
        was_idle = False
        while self._running:
            try:
                idle = idle_seconds()
                title, process = foreground_window()
                category = categorize(title, process)

                with self._lock:
                    now = time.time()
                    # Only count activity while the user is actually present.
                    if idle < self.idle_threshold:
                        if was_idle:
                            self._returned_from_idle = True
                        was_idle = False
                        self._history.append((now, title, process, category))
                        if len(self._history) > MAX_HISTORY:
                            del self._history[:-MAX_HISTORY]
                        if category != self._current_category:
                            self._current_category = category
                            self._task_started_at = now
                    else:
                        was_idle = True
            except Exception:
                # Sampling must never take the app down.
                pass
            time.sleep(self.interval)

    def snapshot(self):
        """Return the current activity state."""
        title, process = foreground_window()
        category = categorize(title, process)
        idle = idle_seconds()
        with self._lock:
            started = self._task_started_at
            samples = len(self._history)
        on_task_seconds = (time.time() - started) if started else 0.0
        return {
            "title": title,
            "process": process,
            "category": category,
            "description": describe_category(category),
            "idle_seconds": idle,
            "on_task_seconds": on_task_seconds,
            "samples": samples,
        }

    def breakdown(self, minutes=60):
        """Return time spent per category over a recent window."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            recent = [row for row in self._history if row[0] >= cutoff]
        if not recent:
            return []
        counts = Counter(row[3] for row in recent)
        total = sum(counts.values())
        results = []
        for category, count in counts.most_common():
            results.append({
                "category": category,
                "description": describe_category(category),
                "share": count / total,
                "minutes": count * self.interval / 60.0,
            })
        return results

    def consume_returned_from_idle(self):
        """Return True once after the user comes back from being away."""
        with self._lock:
            flagged = self._returned_from_idle
            self._returned_from_idle = False
        return flagged

    def current_task_label(self):
        snapshot = self.snapshot()
        return snapshot["description"], _short_title(snapshot["title"])


MONITOR = ActivityMonitor()


def start_monitor():
    return MONITOR.start()


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def _humanize(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    remainder = minutes % 60
    if remainder:
        return f"{hours}h {remainder}m"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def check_activity():
    """Describe what the user appears to be doing right now."""
    snapshot = MONITOR.snapshot()
    if not snapshot["title"]:
        return "I can't see an active window at the moment."

    parts = [f"You're {snapshot['description']}"]
    window = _short_title(snapshot["title"])
    if window:
        parts.append(f"in: {window}")
    if snapshot["on_task_seconds"] > 90 and snapshot["samples"] > 1:
        parts.append(f"You've been on this for about {_humanize(snapshot['on_task_seconds'])}.")
    if snapshot["idle_seconds"] > 60:
        parts.append(f"The keyboard has been quiet for {_humanize(snapshot['idle_seconds'])}.")
    return " ".join(parts)


def list_open_apps():
    """List the applications currently open, grouped sensibly."""
    windows = open_windows()
    if not windows:
        return "I don't see any open windows."

    grouped = {}
    for title, process in windows:
        name = (process or "unknown").replace(".exe", "")
        grouped.setdefault(name, []).append(_short_title(title, 60))

    lines = []
    for name, titles in sorted(grouped.items(), key=lambda item: -len(item[1])):
        if len(titles) == 1:
            lines.append(f"- {name}: {titles[0]}")
        else:
            lines.append(f"- {name} ({len(titles)} windows): {titles[0]}")
    return f"🪟 Open right now ({len(windows)} windows):\n" + "\n".join(lines[:15])


def activity_summary(minutes=60):
    """Summarize how recent time has been spent."""
    try:
        window = max(5, min(int(minutes or 60), 240))
    except (TypeError, ValueError):
        window = 60
    breakdown = MONITOR.breakdown(window)
    if not breakdown:
        return (
            "I haven't been watching long enough to summarize yet. Give me a few "
            "minutes of activity first."
        )
    lines = []
    for row in breakdown[:6]:
        share = int(row["share"] * 100)
        lines.append(f"- {row['description']}: {share}% (~{row['minutes']:.0f} min)")
    return f"📊 The last {window} minutes:\n" + "\n".join(lines)
