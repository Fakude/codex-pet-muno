"""
The task journal: a durable record of what Muno did, when, why, and from where.

Why this exists
---------------
The activity log window shows a rich account of a turn, but it lives in memory
and disappears when Muno restarts. That is fine for watching work happen and
useless for answering "did I already ask for this?". Those are different jobs:
one is a live view, this is the record.

So every request and every action is written to SQLite as it happens. Because it
is on disk and keyed by session, Muno can tell the difference between "you asked
for this a minute ago in this conversation" and "you did this on Tuesday in a
different session", which are very different situations for the user.

Duplicate detection
-------------------
Only actions that change something are worth warning about. Re-reading a page or
looking up a file twice costs nothing, but creating the same repository, sending
the same email, or uploading the same file twice causes real mess. Each such
action gets a stable signature from its name and its meaningful arguments, and
Muno checks that signature before acting again.

Privacy
-------
This is a local file. It records prompts, tool names, arguments, and outcomes, so
it is as sensitive as the work itself. Nothing is uploaded. It can be cleared at
any time.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

JOURNAL_DIR = os.path.expanduser("~/.codex_pet")
JOURNAL_FILE = os.path.join(JOURNAL_DIR, "journal.db")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_DENIED = "denied"

# Actions worth warning about when repeated. Reading and searching are omitted
# deliberately: doing them twice is harmless, and warning would be noise.
EFFECTFUL_TOOLS = {
    "open_app", "type_text", "click_mouse", "press_key", "hotkey",
    "minimize_window", "maximize_window",
    "whatsapp_message", "whatsapp_send", "save_contact",
    "send_email", "set_email_identity",
    "github_create_repo", "github_delete_repo", "github_update_repo",
    "github_upload_file", "github_delete_file", "github_create_issue",
    "github_comment_issue", "github_close_issue", "github_create_branch",
    "github_create_pull_request", "github_merge_pull_request",
    "github_create_release", "github_run_workflow",
    "browser_type", "browser_click",
}

# Arguments that do not change what an action means, so they are left out of the
# signature. Otherwise a reworded commit message would look like a new action.
VOLATILE_ARGUMENTS = {"message", "limit", "index", "submit", "branch"}

_lock = threading.Lock()


def _connect():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    connection = sqlite3.connect(JOURNAL_FILE, timeout=20)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.row_factory = sqlite3.Row
    return connection


def _create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  REAL NOT NULL,
            ended_at    REAL,
            personality TEXT,
            label       TEXT
        );
        CREATE TABLE IF NOT EXISTS requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            at          REAL NOT NULL,
            prompt      TEXT NOT NULL,
            source      TEXT,
            personality TEXT,
            plan        TEXT,
            reply       TEXT
        );
        CREATE TABLE IF NOT EXISTS actions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id  INTEGER,
            session_id  INTEGER NOT NULL,
            at          REAL NOT NULL,
            tool        TEXT NOT NULL,
            arguments   TEXT,
            signature   TEXT,
            status      TEXT,
            result      TEXT,
            duration_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_actions_signature ON actions(signature);
        CREATE INDEX IF NOT EXISTS idx_actions_at ON actions(at);
        CREATE INDEX IF NOT EXISTS idx_requests_at ON requests(at);
        """
    )


def signature_for(tool_name, arguments):
    """Build a stable identity for an action, ignoring incidental differences."""
    arguments = arguments if isinstance(arguments, dict) else {}
    meaningful = {}
    for key, value in arguments.items():
        if key in VOLATILE_ARGUMENTS:
            continue
        text = " ".join(str(value).split()).strip().lower()
        if text:
            meaningful[str(key).lower()] = text
    payload = json.dumps(
        {"tool": str(tool_name or "").lower(), "args": meaningful}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class Journal:
    """Writes and queries the record. Safe to call from any thread."""

    def __init__(self):
        self.session_id = None
        self._request_id = None

    # -- lifecycle ---------------------------------------------------------

    def start_session(self, personality=None, label=None):
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                cursor = connection.execute(
                    "INSERT INTO sessions (started_at, personality, label) VALUES (?, ?, ?)",
                    (time.time(), personality, label),
                )
                self.session_id = cursor.lastrowid
                connection.commit()
            finally:
                connection.close()
        return self.session_id

    def end_session(self):
        if self.session_id is None:
            return
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                connection.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ?",
                    (time.time(), self.session_id),
                )
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                connection.close()

    # -- writing -----------------------------------------------------------

    def record_request(self, prompt, source="desktop", personality=None):
        """Log a prompt. Returns the request id that actions attach to."""
        if self.session_id is None:
            self.start_session(personality)
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                cursor = connection.execute(
                    "INSERT INTO requests (session_id, at, prompt, source, personality) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.session_id, time.time(), str(prompt or ""), source, personality),
                )
                self._request_id = cursor.lastrowid
                connection.commit()
                return self._request_id
            except sqlite3.Error:
                return None
            finally:
                connection.close()

    def record_plan(self, request_id, plan_text):
        self._update_request(request_id, "plan", plan_text)

    def record_reply(self, request_id, reply):
        self._update_request(request_id, "reply", reply)

    def _update_request(self, request_id, column, value):
        if request_id is None:
            return
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                connection.execute(
                    f"UPDATE requests SET {column} = ? WHERE id = ?",
                    (str(value or ""), request_id),
                )
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                connection.close()

    def record_action(self, request_id, tool_name, arguments, status, result,
                      duration_ms=None):
        """Log one completed action."""
        if self.session_id is None:
            self.start_session()
        payload = json.dumps(arguments if isinstance(arguments, dict) else {},
                             ensure_ascii=False)[:4000]
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                connection.execute(
                    "INSERT INTO actions (request_id, session_id, at, tool, arguments, "
                    "signature, status, result, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        self.session_id,
                        time.time(),
                        str(tool_name or ""),
                        payload,
                        signature_for(tool_name, arguments),
                        status,
                        str(result or "")[:1000],
                        duration_ms,
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                connection.close()

    # -- reading -----------------------------------------------------------

    def find_previous(self, tool_name, arguments, within_days=30):
        """Find an earlier successful run of the same action.

        Returns a dictionary describing it, or None. Only effectful actions are
        considered, so harmless repeats do not raise a warning.
        """
        if tool_name not in EFFECTFUL_TOOLS:
            return None
        target = signature_for(tool_name, arguments)
        cutoff = time.time() - within_days * 86400
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                row = connection.execute(
                    "SELECT a.at, a.session_id, a.status, r.prompt, r.source "
                    "FROM actions a LEFT JOIN requests r ON r.id = a.request_id "
                    "WHERE a.signature = ? AND a.status = ? AND a.at >= ? "
                    "ORDER BY a.at DESC LIMIT 1",
                    (target, STATUS_OK, cutoff),
                ).fetchone()
            except sqlite3.Error:
                return None
            finally:
                connection.close()

        if not row:
            return None
        return {
            "at": row["at"],
            "session_id": row["session_id"],
            "same_session": row["session_id"] == self.session_id,
            "prompt": row["prompt"] or "",
            "source": row["source"] or "",
            "ago": describe_age(row["at"]),
        }

    def recent_actions(self, limit=15, days=None):
        clauses = []
        parameters = []
        if days:
            clauses.append("a.at >= ?")
            parameters.append(time.time() - float(days) * 86400)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(int(limit))
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                rows = connection.execute(
                    "SELECT a.at, a.tool, a.arguments, a.status, a.session_id, r.prompt "
                    f"FROM actions a LEFT JOIN requests r ON r.id = a.request_id {where} "
                    "ORDER BY a.at DESC LIMIT ?",
                    parameters,
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.Error:
                return []
            finally:
                connection.close()

    def recent_requests(self, limit=10, days=None):
        clauses = []
        parameters = []
        if days:
            clauses.append("at >= ?")
            parameters.append(time.time() - float(days) * 86400)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(int(limit))
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                rows = connection.execute(
                    f"SELECT id, at, prompt, source, reply, session_id FROM requests {where} "
                    "ORDER BY at DESC LIMIT ?",
                    parameters,
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.Error:
                return []
            finally:
                connection.close()

    def search(self, text, limit=15):
        needle = f"%{str(text or '').strip().lower()}%"
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                rows = connection.execute(
                    "SELECT a.at, a.tool, a.arguments, a.status, r.prompt "
                    "FROM actions a LEFT JOIN requests r ON r.id = a.request_id "
                    "WHERE lower(a.tool) LIKE ? OR lower(a.arguments) LIKE ? "
                    "   OR lower(coalesce(r.prompt,'')) LIKE ? "
                    "ORDER BY a.at DESC LIMIT ?",
                    (needle, needle, needle, int(limit)),
                ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.Error:
                return []
            finally:
                connection.close()

    def counts(self):
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                row = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM sessions) AS sessions, "
                    "       (SELECT COUNT(*) FROM requests) AS requests, "
                    "       (SELECT COUNT(*) FROM actions)  AS actions, "
                    "       (SELECT MIN(started_at) FROM sessions) AS since"
                ).fetchone()
                return dict(row) if row else {}
            except sqlite3.Error:
                return {}
            finally:
                connection.close()

    def clear(self):
        with _lock:
            connection = _connect()
            try:
                _create_schema(connection)
                connection.executescript(
                    "DELETE FROM actions; DELETE FROM requests; DELETE FROM sessions;"
                )
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                connection.close()
        self.session_id = None
        self._request_id = None


JOURNAL = Journal()


def describe_age(timestamp):
    """Render how long ago something happened, in words."""
    seconds = max(0, time.time() - float(timestamp or 0))
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)} day{'s' if int(days) != 1 else ''} ago"
    return time.strftime("%d %b", time.localtime(float(timestamp)))


def describe_duplicate(previous):
    """Phrase a duplicate warning, distinguishing session and day."""
    if not previous:
        return ""
    when = previous["ago"]
    if previous["same_session"]:
        context = "earlier in this same conversation"
    else:
        same_day = time.strftime("%Y-%m-%d", time.localtime(previous["at"])) == \
            time.strftime("%Y-%m-%d", time.localtime())
        context = "earlier today, in a different session" if same_day \
            else "in a different session on another day"
    line = f"You already did this {when}, {context}."
    if previous.get("prompt"):
        asked = " ".join(previous["prompt"].split())[:120]
        line += f'\nThat time you asked: "{asked}"'
    if previous.get("source") and previous["source"] != "desktop":
        line += f" (from your {previous['source']})"
    return line


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def _format_arguments(raw):
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return ""
    if not isinstance(data, dict) or not data:
        return ""
    parts = []
    for key, value in data.items():
        text = " ".join(str(value).split())
        parts.append(f"{key}={text[:40]}")
    return ", ".join(parts)


def task_history(limit=10, days=None):
    """
    Shows what Muno has done recently, with when and why.

    Args:
        limit: How many actions to list.
        days: Only include the last N days.

    Returns:
        A readable history
    """
    try:
        count = max(1, min(int(limit or 10), 40))
    except (TypeError, ValueError):
        count = 10
    rows = JOURNAL.recent_actions(limit=count, days=days)
    if not rows:
        return "I haven't recorded any actions yet."

    lines = []
    for row in rows:
        marker = {"ok": "✓", "failed": "✕", "denied": "⊘"}.get(row["status"], "•")
        when = describe_age(row["at"])
        arguments = _format_arguments(row["arguments"])
        detail = f" ({arguments})" if arguments else ""
        lines.append(f"{marker} {when}: {row['tool']}{detail}")
        if row.get("prompt"):
            asked = " ".join(str(row["prompt"]).split())[:80]
            lines.append(f'      you asked: "{asked}"')
    return "📓 What I've done:\n" + "\n".join(lines)


def check_if_done(description):
    """
    Checks whether Muno has already done something like this before.

    Args:
        description: What to look for, e.g. 'create repo my-report'.

    Returns:
        Matching past actions, or a note that there are none
    """
    text = str(description or "").strip()
    if not text:
        return "❌ What should I check for?"
    rows = JOURNAL.search(text, limit=10)
    if not rows:
        return f"I have no record of doing anything matching '{text}'."
    lines = []
    for row in rows:
        marker = {"ok": "✓", "failed": "✕", "denied": "⊘"}.get(row["status"], "•")
        arguments = _format_arguments(row["arguments"])
        detail = f" ({arguments})" if arguments else ""
        lines.append(f"{marker} {describe_age(row['at'])}: {row['tool']}{detail}")
    return f"📓 Records matching '{text}':\n" + "\n".join(lines)


def journal_status():
    """Report how much history Muno holds."""
    counts = JOURNAL.counts()
    if not counts or not counts.get("actions"):
        return "My task journal is empty so far."
    since = counts.get("since")
    when = time.strftime("%d %b %Y", time.localtime(since)) if since else "unknown"
    return (
        f"📓 I've recorded {counts.get('requests', 0)} requests and "
        f"{counts.get('actions', 0)} actions across {counts.get('sessions', 0)} "
        f"sessions since {when}. This is session {JOURNAL.session_id}."
    )
