"""
Speech output for Muno.

Muno speaks through the Windows speech engine (SAPI) rather than a cloud text to
speech service. That choice matters for a companion that narrates what it is
doing: there is no network round trip, no API key, no rate limit, and no length
cap, so narration stays in step with the action it describes.

A PowerShell fallback covers the rare case where the COM bindings are missing,
rendering to a WAV and playing it with winsound.

Threading
---------
COM objects are created inside the thread that uses them, after initialising COM
for that thread. Nothing is shared across threads, which avoids the marshalling
problems that make COM speech flaky when a single voice object is reused.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
import winsound

# SAPI ISpVoice::Speak flags
SVSF_ASYNC = 1
SVSF_PURGE_BEFORE_SPEAK = 2

# SAPI SpeechRunState
SPRS_DONE = 1
SPRS_IS_SPEAKING = 2

# Rate is -10..10, volume 0..100.
DEFAULT_RATE = int(os.getenv("MUNO_VOICE_RATE", "1"))
DEFAULT_VOLUME = int(os.getenv("MUNO_VOICE_VOLUME", "100"))
PREFERRED_VOICE = os.getenv("MUNO_VOICE", "").strip()

# Measured against the Windows desktop voices at rate 1: a 69 character phrase
# took about 6 seconds, so roughly 11.5 characters per second. Used to drive
# mouth animation when the engine cannot report progress.
CHARS_PER_SECOND = 11.5


def _load_com():
    """Return the COM client module, or None when unavailable."""
    try:
        import win32com.client as client

        return client
    except ImportError:
        try:
            import comtypes.client as client

            return client
        except ImportError:
            return None


def available():
    """True when speech output can be produced by any backend."""
    return _load_com() is not None or _powershell_available()


def _powershell_available():
    return bool(
        os.path.exists(os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"),
                                   "System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
    )


def estimate_duration(text):
    """Estimate how long a phrase takes to say, in seconds."""
    length = len(str(text or ""))
    if not length:
        return 0.0
    return max(0.8, min(90.0, length / CHARS_PER_SECOND))


def list_voices():
    """Return the installed voice names."""
    client = _load_com()
    if not client:
        return []
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        voice = client.Dispatch("SAPI.SpVoice")
        tokens = voice.GetVoices()
        return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
    except Exception:
        return []


def _select_voice(voice):
    """Apply the preferred voice, rate, and volume to a SAPI voice object."""
    try:
        voice.Rate = max(-10, min(10, DEFAULT_RATE))
        voice.Volume = max(0, min(100, DEFAULT_VOLUME))
    except Exception:
        pass
    if not PREFERRED_VOICE:
        return
    try:
        tokens = voice.GetVoices()
        for index in range(tokens.Count):
            token = tokens.Item(index)
            if PREFERRED_VOICE.lower() in token.GetDescription().lower():
                voice.Voice = token
                return
    except Exception:
        pass


# Abbreviations that read fine on screen but sound wrong aloud. A weather box
# writes "Mon 24°", and a speech engine says "Mon", which is not a word.
_WEEKDAYS = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}
_MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "jun": "June", "jul": "July", "aug": "August", "sep": "September",
    "sept": "September", "oct": "October", "nov": "November", "dec": "December",
}

# Expanded when a number follows, which is the forecast-table case, so
# "Sun 19°" becomes "Sunday 19 degrees".
_DAY_PATTERN = re.compile(
    r"\b(" + "|".join(_WEEKDAYS) + r")\b(?=[\s:,\-]*\d)", re.IGNORECASE
)

# Also expanded when two day names are joined, as in "Sat and Sun turn cold".
# Requiring either a number or a neighbouring day name is what keeps ordinary
# prose safe: "the Sun is bright" and "I sat down" have neither.
_DAY_PAIR_PATTERN = re.compile(
    r"\b(" + "|".join(_WEEKDAYS) + r")\b"
    r"(\s*(?:and|to|through|&|,|/)\s*)"
    r"\b(" + "|".join(_WEEKDAYS) + r")\b",
    re.IGNORECASE,
)
_MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\b(?=[\s:,\-]*\d)", re.IGNORECASE
)

_UNIT_REPLACEMENTS = [
    (re.compile(r"°\s*C\b", re.IGNORECASE), " degrees Celsius"),
    (re.compile(r"°\s*F\b", re.IGNORECASE), " degrees Fahrenheit"),
    (re.compile(r"(\d)\s*°"), r"\1 degrees"),
    (re.compile(r"\bkm/h\b", re.IGNORECASE), " kilometres per hour"),
    (re.compile(r"\bmph\b", re.IGNORECASE), " miles per hour"),
    (re.compile(r"\bm/s\b", re.IGNORECASE), " metres per second"),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\bmm\b"), " millimetres"),
    (re.compile(r"\bapprox\.?\b", re.IGNORECASE), "approximately"),
    (re.compile(r"\be\.g\.?\b", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.?\b", re.IGNORECASE), "that is"),
    (re.compile(r"\bvs\.?\b", re.IGNORECASE), "versus"),
    (re.compile(r"\betc\.?\b", re.IGNORECASE), "and so on"),
    (re.compile(r"\bmax\b", re.IGNORECASE), "maximum"),
    (re.compile(r"\bmin\b(?!ute)", re.IGNORECASE), "minimum"),
    (re.compile(r"\btemp\b", re.IGNORECASE), "temperature"),
]


# Markers that a passage is code or markup rather than prose. Reading these
# aloud produces minutes of unlistenable punctuation.
# Structural markers. These essentially only occur in code or markup, so one is
# enough to conclude a passage should not be read aloud.
_CODE_STRONG = (
    "<!doctype", "<html", "<head", "<body", "<div", "<style", "<script",
    "</", "/>", "};", ");", "{}", "#include", "console.log", "=>",
    "font-family", "margin:", "padding:", "background:",
)

# Words that appear in code but also in ordinary sentences about code. Several
# together suggest code; one or two do not, because "I used the return value
# from that function" is a person talking, not a program.
_CODE_WEAK = (
    "def ", "class ", "function ", "import ", "return ", "const ", "let ",
    "var ", "select ", "printf", "elif", "null", "true;", "false;",
)

_CODE_MARKERS = _CODE_STRONG + _CODE_WEAK

# Beyond this, a spoken reply stops being useful and becomes an ordeal.
SPOKEN_LIMIT = 420


def looks_like_code(text):
    """True when a passage is code or markup rather than something to say."""
    sample = str(text or "")
    lowered = sample.lower()
    if any(marker in lowered for marker in _CODE_STRONG):
        return True
    if sum(1 for marker in _CODE_WEAK if marker in lowered) >= 3:
        return True
    # Lots of punctuation-heavy lines is the other reliable signal.
    lines = [line for line in sample.splitlines() if line.strip()]
    if len(lines) >= 6:
        dense = sum(
            1 for line in lines
            if sum(character in "{};<>=():" for character in line) >= 3
        )
        if dense >= len(lines) * 0.4:
            return True
    return False


def _first_sentences(text, limit=SPOKEN_LIMIT):
    """Take whole sentences up to a limit, so speech never stops mid-thought."""
    prose = " ".join(str(text or "").split())
    if len(prose) <= limit:
        return prose
    pieces = re.split(r"(?<=[.!?])\s+", prose)
    kept = []
    total = 0
    for piece in pieces:
        if total + len(piece) > limit and kept:
            break
        kept.append(piece)
        total += len(piece) + 1
    return " ".join(kept) if kept else prose[:limit].rsplit(" ", 1)[0]


def spoken_digest(text):
    """Decide what to actually say for a reply that may be long or technical.

    The written reply is left intact on screen. Only the spoken version is
    shortened, because listening to a page of markup read character by character
    is useless, and hearing a seven-day table recited is nearly as bad.
    """
    written = str(text or "").strip()
    if not written:
        return ""

    if looks_like_code(written):
        # Say the explanation around the code, never the code itself.
        prose_lines = [
            line.strip() for line in written.splitlines()
            if line.strip() and not any(
                marker.lower() in line.lower() for marker in _CODE_MARKERS
            )
        ]
        intro = _first_sentences(" ".join(prose_lines), 260).strip()
        if intro:
            return f"{intro} I've put the full code on screen for you."
        return "I've put the code on screen for you."

    if len(written) > SPOKEN_LIMIT:
        return _first_sentences(written) + " There's more on screen."
    return written


def speech_ready(text):
    """Rewrite text so a speech engine pronounces it as a person would.

    The written form stays as it is; only what gets spoken is adjusted. This
    matters because figures scraped from a page keep their compressed on-screen
    spelling, and reading that aloud sounds like a machine reciting a table.
    """
    spoken = str(text or "")

    # Separate figures that were visually adjacent before units are expanded,
    # otherwise a high and low written as "24°11°" is spoken as one run.
    spoken = re.sub(r"(\d+)\s*°\s*(\d+)\s*°", r"\1 degrees, low \2 degrees", spoken)

    spoken = _DAY_PAIR_PATTERN.sub(
        lambda m: f"{_WEEKDAYS[m.group(1).lower()]}{m.group(2)}"
                  f"{_WEEKDAYS[m.group(3).lower()]}",
        spoken,
    )
    spoken = _DAY_PATTERN.sub(lambda m: _WEEKDAYS[m.group(1).lower()], spoken)
    spoken = _MONTH_PATTERN.sub(lambda m: _MONTHS[m.group(1).lower()], spoken)
    for pattern, replacement in _UNIT_REPLACEMENTS:
        spoken = pattern.sub(replacement, spoken)
    # A slash between figures reads as a pause, not the word "slash".
    spoken = re.sub(r"(\d)\s*/\s*(\d)", r"\1, ", spoken)
    return re.sub(r"\s{2,}", " ", spoken).strip()


def speak_blocking(text, should_continue=None):
    """Say ``text``, returning when finished or when interrupted.

    ``should_continue`` is polled while speaking; returning False stops playback
    promptly. Returns True when speech was produced.
    """
    phrase = speech_ready(text)
    if not phrase:
        return False

    client = _load_com()
    if client is not None:
        if _speak_via_sapi(client, phrase, should_continue):
            return True
    return _speak_via_powershell(phrase, should_continue)


def _speak_via_sapi(client, phrase, should_continue):
    """Speak through SAPI in the calling thread."""
    initialized = False
    try:
        import pythoncom

        pythoncom.CoInitialize()
        initialized = True
    except Exception:
        pass

    try:
        voice = client.Dispatch("SAPI.SpVoice")
        _select_voice(voice)
        voice.Speak(phrase, SVSF_ASYNC)

        while True:
            if should_continue is not None and not should_continue():
                # Purge whatever is queued so playback stops immediately.
                try:
                    voice.Speak("", SVSF_ASYNC | SVSF_PURGE_BEFORE_SPEAK)
                except Exception:
                    pass
                break
            try:
                if voice.Status.RunningState == SPRS_DONE:
                    break
            except Exception:
                break
            time.sleep(0.05)
        return True
    except Exception as exc:
        print(f"SAPI speech unavailable: {exc}", file=sys.stderr)
        return False
    finally:
        if initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass


def _speak_via_powershell(phrase, should_continue):
    """Render speech to a WAV with System.Speech, then play it."""
    if not _powershell_available():
        return False
    handle, path = tempfile.mkstemp(suffix=".wav", prefix="muno_voice_")
    os.close(handle)
    escaped = phrase.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {max(-10, min(10, DEFAULT_RATE))}; "
        f"$s.Volume = {max(0, min(100, DEFAULT_VOLUME))}; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{escaped}'); "
        "$s.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return False
        try:
            with wave.open(path, "rb") as handle_wav:
                duration = handle_wav.getnframes() / max(handle_wav.getframerate(), 1)
        except Exception:
            duration = estimate_duration(phrase)

        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if should_continue is not None and not should_continue():
                break
            time.sleep(0.05)
        winsound.PlaySound(None, 0)
        return True
    except Exception as exc:
        print(f"PowerShell speech failed: {exc}", file=sys.stderr)
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def stop_all():
    """Stop any audio started through the winsound fallback."""
    try:
        winsound.PlaySound(None, 0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Narration
#
# Turning a tool call into a spoken phrase happens here so the wording stays in
# one place and reads naturally. Two forms are produced for each action: what
# Muno is about to do, and what it finished doing.
# ---------------------------------------------------------------------------

def _first(arguments, *names, default=""):
    for name in names:
        value = arguments.get(name)
        if value not in (None, ""):
            return str(value)
    return default


def _shorten(text, limit=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# name -> (present participle phrase, completed phrase)
_NARRATION = {
    "open_app": (
        lambda a: f"Opening {_first(a, 'app_name')}",
        lambda a: f"Opened {_first(a, 'app_name')}",
    ),
    "open_url": (
        lambda a: f"Opening {_shorten(_first(a, 'url'), 40)}",
        lambda a: f"Opened {_shorten(_first(a, 'url'), 40)}",
    ),
    "web_search": (
        lambda a: f"Searching the web for {_shorten(_first(a, 'query'), 40)}",
        lambda a: f"Got results for {_shorten(_first(a, 'query'), 40)}",
    ),
    "search_and_read": (
        lambda a: f"Looking up {_shorten(_first(a, 'query'), 40)}",
        lambda a: f"Found out about {_shorten(_first(a, 'query'), 40)}",
    ),
    "read_page": (
        lambda a: f"Reading {_shorten(_first(a, 'url'), 40)}",
        lambda a: "Read that page",
    ),
    "youtube_search": (
        lambda a: f"Searching YouTube for {_shorten(_first(a, 'query'), 40)}",
        lambda a: f"Found YouTube results for {_shorten(_first(a, 'query'), 40)}",
    ),
    "youtube_play": (
        lambda a: f"Playing {_shorten(_first(a, 'query'), 40)} on YouTube",
        lambda a: f"Started {_shorten(_first(a, 'query'), 40)}",
    ),
    "browser_open": (
        lambda a: f"Opening {_shorten(_first(a, 'url'), 40)} in the browser",
        lambda a: f"Loaded {_shorten(_first(a, 'url'), 40)}",
    ),
    "browser_read": (lambda a: "Reading the page", lambda a: "Read the page"),
    "browser_links": (lambda a: "Looking at the links", lambda a: "Listed the links"),
    "browser_click": (
        lambda a: f"Clicking {_shorten(_first(a, 'text'), 30)}",
        lambda a: f"Clicked {_shorten(_first(a, 'text'), 30)}",
    ),
    "browser_type": (
        lambda a: f"Typing {_shorten(_first(a, 'text'), 30)} into the page",
        lambda a: "Submitted that on the page",
    ),
    "browser_status": (lambda a: "Checking the browser", lambda a: "Checked the browser"),
    "type_text": (lambda a: "Typing that for you", lambda a: "Typed it"),
    "click_mouse": (lambda a: "Clicking", lambda a: "Clicked"),
    "press_key": (
        lambda a: f"Pressing {_first(a, 'key_name')}",
        lambda a: f"Pressed {_first(a, 'key_name')}",
    ),
    "hotkey": (lambda a: "Using a keyboard shortcut", lambda a: "Used the shortcut"),
    "list_windows": (lambda a: "Checking your windows", lambda a: "Checked your windows"),
    "focus_window": (
        lambda a: f"Switching to {_shorten(_first(a, 'title_substring'), 30)}",
        lambda a: f"Switched to {_shorten(_first(a, 'title_substring'), 30)}",
    ),
    "minimize_window": (lambda a: "Minimising that window", lambda a: "Minimised it"),
    "maximize_window": (lambda a: "Maximising that window", lambda a: "Maximised it"),
    "take_screenshot": (lambda a: "Taking a screenshot", lambda a: "Took a screenshot"),
    # Files
    "find_file": (
        lambda a: f"Looking for {_shorten(_first(a, 'name'), 40)}",
        lambda a: f"Found what matches {_shorten(_first(a, 'name'), 40)}",
    ),
    "refresh_file_index": (
        lambda a: "Re-cataloguing your files",
        lambda a: "Started re-cataloguing your files",
    ),
    "file_index_status": (
        lambda a: "Checking my file catalogue",
        lambda a: "Checked my file catalogue",
    ),
    # Messaging
    "whatsapp_message": (
        lambda a: f"Opening a WhatsApp chat with {_first(a, 'contact')}",
        lambda a: f"Opened the chat with {_first(a, 'contact')}",
    ),
    "whatsapp_send": (
        lambda a: f"Sending your WhatsApp message to {_first(a, 'contact')}",
        lambda a: f"Sent it to {_first(a, 'contact')}",
    ),
    "list_contacts": (lambda a: "Checking your contacts", lambda a: "Checked your contacts"),
    "save_contact": (
        lambda a: f"Saving {_first(a, 'name')} to contacts",
        lambda a: f"Saved {_first(a, 'name')}",
    ),
    # Email
    "send_email": (
        lambda a: f"Sending your email to {_shorten(_first(a, 'to'), 40)}",
        lambda a: f"Sent the email to {_shorten(_first(a, 'to'), 40)}",
    ),
    "check_email": (lambda a: "Checking your inbox", lambda a: "Checked your inbox"),
    "email_status": (lambda a: "Checking your email setup", lambda a: "Checked your email setup"),
    "set_email_identity": (
        lambda a: f"Setting your sending address to {_first(a, 'address')}",
        lambda a: f"I'll send as {_first(a, 'address')}",
    ),
    "github_connect": (
        lambda a: "Starting the GitHub sign-in",
        lambda a: "Opened the GitHub sign-in",
    ),
    # Activity
    "capability_report": (
        lambda a: "Checking what I can do", lambda a: "Checked what I can do",
    ),
    "find_reference_examples": (
        lambda a: f"Looking up how others do {_shorten(_first(a, 'need'), 40)}",
        lambda a: "Found some examples",
    ),
    "research_capability": (
        lambda a: f"Looking into how I could {_shorten(_first(a, 'need'), 40)}",
        lambda a: "Finished looking into that",
    ),
    "install_capability": (
        lambda a: f"Installing {_first(a, 'package')} to gain a new ability",
        lambda a: f"Installed {_first(a, 'package')}",
    ),
    "list_capabilities": (
        lambda a: "Checking what I've added", lambda a: "Checked what I've added",
    ),
    "uninstall_capability": (
        lambda a: f"Removing {_first(a, 'name')}", lambda a: f"Removed {_first(a, 'name')}",
    ),
    "task_history": (lambda a: "Looking through my records", lambda a: "Checked my records"),
    "check_if_done": (
        lambda a: f"Checking whether I've already done {_shorten(_first(a, 'description'), 40)}",
        lambda a: "Checked my records",
    ),
    "journal_status": (lambda a: "Checking my journal", lambda a: "Checked my journal"),
    "watch_screen_region": (
        lambda a: "Opening the screen mapper", lambda a: "Ready for you to map it",
    ),
    "write_into_field": (
        lambda a: "I've written it, point me at the field",
        lambda a: "Ready to type it in",
    ),
    "list_screen_watches": (
        lambda a: "Checking what I'm watching", lambda a: "Checked what I'm watching",
    ),
    "stop_screen_watch": (
        lambda a: "Stopping the screen watch", lambda a: "Stopped watching",
    ),
    "check_activity": (lambda a: "Checking what you're working on", lambda a: "Had a look"),
    "list_open_apps": (lambda a: "Checking what's open", lambda a: "Checked what's open"),
    "activity_summary": (lambda a: "Adding up your recent time", lambda a: "Worked out your recent time"),
    # GitHub
    "github_status": (lambda a: "Checking GitHub", lambda a: "Checked GitHub"),
    "github_create_repo": (
        lambda a: f"Creating the repository {_first(a, 'name')}",
        lambda a: f"Created {_first(a, 'name')}",
    ),
    "github_list_repos": (lambda a: "Fetching your repositories", lambda a: "Listed your repositories"),
    "github_repo_info": (
        lambda a: f"Looking up {_first(a, 'repo')}",
        lambda a: f"Looked up {_first(a, 'repo')}",
    ),
    "github_update_repo": (
        lambda a: f"Updating {_first(a, 'repo')}",
        lambda a: f"Updated {_first(a, 'repo')}",
    ),
    "github_delete_repo": (
        lambda a: f"Deleting the repository {_first(a, 'repo')}",
        lambda a: f"Deleted {_first(a, 'repo')}",
    ),
    "github_search_repos": (
        lambda a: f"Searching GitHub for {_shorten(_first(a, 'query'), 40)}",
        lambda a: "Got the search results",
    ),
    "github_upload_file": (
        lambda a: f"Uploading {_first(a, 'file')} to {_first(a, 'repo')}",
        lambda a: f"Uploaded {_first(a, 'file')} to {_first(a, 'repo')}",
    ),
    "github_read_file": (
        lambda a: f"Reading {_first(a, 'path')} from {_first(a, 'repo')}",
        lambda a: f"Read {_first(a, 'path')}",
    ),
    "github_list_files": (
        lambda a: f"Listing files in {_first(a, 'repo')}",
        lambda a: f"Listed the files in {_first(a, 'repo')}",
    ),
    "github_delete_file": (
        lambda a: f"Deleting {_first(a, 'path')} from {_first(a, 'repo')}",
        lambda a: f"Deleted {_first(a, 'path')}",
    ),
    "github_create_issue": (
        lambda a: f"Opening an issue on {_first(a, 'repo')}",
        lambda a: "Opened the issue",
    ),
    "github_list_issues": (
        lambda a: f"Fetching issues on {_first(a, 'repo')}",
        lambda a: "Listed the issues",
    ),
    "github_comment_issue": (
        lambda a: f"Commenting on number {_first(a, 'number')}",
        lambda a: "Posted the comment",
    ),
    "github_close_issue": (
        lambda a: f"Closing issue {_first(a, 'number')}",
        lambda a: f"Closed issue {_first(a, 'number')}",
    ),
    "github_list_branches": (lambda a: "Fetching the branches", lambda a: "Listed the branches"),
    "github_create_branch": (
        lambda a: f"Creating the branch {_first(a, 'name')}",
        lambda a: f"Created the branch {_first(a, 'name')}",
    ),
    "github_create_pull_request": (
        lambda a: "Opening a pull request",
        lambda a: "Opened the pull request",
    ),
    "github_list_pull_requests": (
        lambda a: "Fetching the pull requests",
        lambda a: "Listed the pull requests",
    ),
    "github_merge_pull_request": (
        lambda a: f"Merging pull request {_first(a, 'number')}",
        lambda a: f"Merged pull request {_first(a, 'number')}",
    ),
    "github_list_commits": (lambda a: "Fetching recent commits", lambda a: "Listed recent commits"),
    "github_create_release": (
        lambda a: f"Publishing release {_first(a, 'tag')}",
        lambda a: f"Published {_first(a, 'tag')}",
    ),
    "github_list_workflows": (lambda a: "Fetching the workflows", lambda a: "Listed the workflows"),
    "github_run_workflow": (
        lambda a: f"Running the workflow {_shorten(_first(a, 'workflow'), 30)}",
        lambda a: "Started the workflow",
    ),
    "github_list_workflow_runs": (
        lambda a: "Checking recent workflow runs",
        lambda a: "Checked the workflow runs",
    ),
    # Companion state
    "system_sleep": (lambda a: "Going quiet", lambda a: "Resting"),
    "system_mute": (lambda a: "Changing my voice setting", lambda a: "Changed my voice setting"),
    "system_exit": (lambda a: "Getting ready to go", lambda a: "Ready to go"),
}


def describe_step(tool_name, arguments, completed=False):
    """Return a short spoken phrase for one action."""
    arguments = arguments if isinstance(arguments, dict) else {}
    entry = _NARRATION.get(tool_name)
    if not entry:
        readable = str(tool_name or "something").replace("_", " ")
        return f"Finished {readable}" if completed else f"Working on {readable}"
    try:
        return entry[1](arguments) if completed else entry[0](arguments)
    except Exception:
        readable = str(tool_name or "something").replace("_", " ")
        return f"Finished {readable}" if completed else f"Working on {readable}"


def describe_plan(tool_calls):
    """Announce a set of upcoming actions in one sentence."""
    steps = []
    for name, arguments in tool_calls:
        steps.append(describe_step(name, arguments, completed=False))
    if not steps:
        return ""
    if len(steps) == 1:
        return steps[0] + "."
    lowered = [steps[0]] + [step[0].lower() + step[1:] for step in steps[1:]]
    return f"{len(steps)} things: " + ", then ".join(lowered) + "."


def summarize_completed(results):
    """Close out a turn by saying what actually happened.

    ``results`` is a list of (tool_name, arguments, succeeded).
    """
    done = [
        describe_step(name, arguments, completed=True)
        for name, arguments, succeeded in results
        if succeeded
    ]
    failed = [
        describe_step(name, arguments, completed=False).lower()
        for name, arguments, succeeded in results
        if not succeeded
    ]
    parts = []
    if done:
        if len(done) == 1:
            parts.append(f"{done[0]}.")
        else:
            lowered = [done[0]] + [step[0].lower() + step[1:] for step in done[1:]]
            parts.append("Done: " + ", ".join(lowered) + ".")
    if failed:
        parts.append(f"I couldn't finish {', '.join(failed)}.")
    return " ".join(parts)
