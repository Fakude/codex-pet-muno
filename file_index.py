"""
Fast local file lookup for Muno.

Why an index
------------
Walking the filesystem on every request is far too slow: a full scan of a user
profile takes tens of seconds, and scanning whole drives takes minutes. Instead
Muno keeps a small SQLite catalogue of file names and paths. Building it is a
one-time cost, and lookups afterwards are effectively instant, so "upload that
PDF to GitHub" resolves without a visible pause.

What gets indexed
-----------------
Only places real user files live: the home folder and its documents, desktop,
downloads, and media folders. System directories, dependency folders, and caches
are skipped because indexing them adds hundreds of thousands of useless rows and
makes matches noisier, not better.

Freshness
---------
The index records when it was built. Callers can rebuild it in the background,
and a lookup that misses falls back to a direct scan of the most likely folders
so a brand new file is still found before the next rebuild.
"""
import os
import sqlite3
import threading
import time

INDEX_DIR = os.path.expanduser("~/.codex_pet")
INDEX_FILE = os.path.join(INDEX_DIR, "file_index.db")

# Directory names that never contain files a user would ask for by name.
SKIP_DIRECTORIES = {
    "appdata", "$recycle.bin", "system volume information", "windows",
    "program files", "program files (x86)", "programdata", "node_modules",
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "site-packages", ".cache", ".npm", ".nuget", ".gradle", ".m2",
    ".next", ".nuxt", ".tox", ".mypy_cache", ".pytest_cache", ".idea",
    "dist-info", "egg-info", ".ipynb_checkpoints", "temp", "tmp",
}

# Files that are noise rather than documents.
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".dll", ".lib", ".obj", ".o", ".a", ".so", ".dylib",
    ".tmp", ".temp", ".log", ".lock", ".pdb", ".idb", ".ilk", ".exp",
}

MAX_INDEXED_FILES = 400_000

_build_lock = threading.Lock()
_build_thread = None
_build_state = {"running": False, "files": 0, "finished_at": None, "error": None}


def _default_roots():
    """Return the folders worth indexing, most important first."""
    home = os.path.expanduser("~")
    named = [
        os.path.join(home, name)
        for name in (
            "Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music",
            "OneDrive", "Projects", "source", "repos",
        )
    ]
    roots = [path for path in named if os.path.isdir(path)]
    # The home folder itself catches anything outside the standard folders.
    roots.append(home)

    extra = os.getenv("MUNO_INDEX_ROOTS", "")
    for candidate in extra.split(os.pathsep):
        candidate = candidate.strip()
        if candidate and os.path.isdir(candidate):
            roots.append(candidate)

    # Preserve order while removing duplicates and nested repeats.
    unique = []
    for root in roots:
        normalized = os.path.normcase(os.path.abspath(root))
        if normalized not in {os.path.normcase(p) for p in unique}:
            unique.append(os.path.abspath(root))
    return unique


def _connect():
    os.makedirs(INDEX_DIR, exist_ok=True)
    connection = sqlite3.connect(INDEX_FILE, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=OFF")
    return connection


def _create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            path       TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            extension  TEXT,
            directory  TEXT,
            size       INTEGER,
            modified   REAL
        );
        CREATE INDEX IF NOT EXISTS idx_files_name_lower ON files(name_lower);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )


def _should_skip_directory(name):
    lowered = name.lower()
    if lowered in SKIP_DIRECTORIES:
        return True
    # Hidden folders hold tool state rather than documents. Editor session
    # snapshots in particular contain copies of real files, and letting them in
    # means a search can return a stale copy instead of the file being edited.
    return lowered.startswith(".")


def _walk(roots, limit=MAX_INDEXED_FILES):
    """Yield (path, name, ext, dir, size, mtime) using scandir for speed."""
    seen_directories = set()
    produced = 0

    for root in roots:
        stack = [root]
        while stack:
            current = stack.pop()
            key = os.path.normcase(current)
            if key in seen_directories:
                continue
            seen_directories.add(key)

            try:
                entries = list(os.scandir(current))
            except (PermissionError, OSError):
                continue

            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not _should_skip_directory(entry.name):
                            stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue

                    name = entry.name
                    extension = os.path.splitext(name)[1].lower()
                    if extension in SKIP_EXTENSIONS:
                        continue

                    stat = entry.stat(follow_symlinks=False)
                    yield (
                        entry.path,
                        name,
                        name.lower(),
                        extension,
                        os.path.dirname(entry.path),
                        stat.st_size,
                        stat.st_mtime,
                    )
                    produced += 1
                    if produced >= limit:
                        return
                except (PermissionError, OSError):
                    continue


def build_index(roots=None):
    """Rebuild the catalogue. Returns the number of files indexed."""
    roots = roots or _default_roots()
    started = time.monotonic()

    connection = _connect()
    try:
        _create_schema(connection)
        connection.execute("DELETE FROM files")
        batch = []
        total = 0
        for record in _walk(roots):
            batch.append(record)
            if len(batch) >= 5000:
                connection.executemany(
                    "INSERT OR REPLACE INTO files "
                    "(path, name, name_lower, extension, directory, size, modified) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                total += len(batch)
                batch = []
        if batch:
            connection.executemany(
                "INSERT OR REPLACE INTO files "
                "(path, name, name_lower, extension, directory, size, modified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            total += len(batch)

        elapsed = time.monotonic() - started
        connection.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [
                ("built_at", str(time.time())),
                ("file_count", str(total)),
                ("build_seconds", f"{elapsed:.2f}"),
                ("roots", os.pathsep.join(roots)),
            ],
        )
        connection.commit()
        return total
    finally:
        connection.close()


def build_index_async():
    """Build the index on a background thread, ignoring repeat calls."""
    global _build_thread

    def worker():
        _build_state.update(running=True, error=None)
        try:
            count = build_index()
            _build_state.update(files=count, finished_at=time.time())
        except Exception as exc:  # pragma: no cover - defensive
            _build_state.update(error=str(exc))
        finally:
            _build_state["running"] = False

    with _build_lock:
        if _build_state["running"]:
            return False
        _build_thread = threading.Thread(target=worker, name="muno-file-index", daemon=True)
        _build_thread.start()
        return True


def get_meta():
    """Return index metadata as a dictionary."""
    try:
        connection = _connect()
    except Exception:
        return {}
    try:
        _create_schema(connection)
        rows = connection.execute("SELECT key, value FROM meta").fetchall()
        return {key: value for key, value in rows}
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def is_built():
    meta = get_meta()
    try:
        return int(meta.get("file_count", "0")) > 0
    except ValueError:
        return False


def _score(name_lower, path_lower, query_lower, tokens):
    """Rank a candidate. Higher is better, 0 means no match."""
    if name_lower == query_lower:
        return 100
    stem = os.path.splitext(name_lower)[0]
    if stem == query_lower:
        return 95
    if name_lower.startswith(query_lower):
        return 80
    if query_lower in name_lower:
        return 65
    if tokens and all(token in name_lower for token in tokens):
        return 45
    if tokens and all(token in path_lower for token in tokens):
        return 25
    return 0


def search(query, limit=10, extension=None):
    """Find files matching a name or partial name, best matches first.

    Returns a list of dictionaries with path, name, size, and modified time.
    """
    text = str(query or "").strip()
    if not text:
        return []
    query_lower = text.lower()
    tokens = [token for token in query_lower.replace("_", " ").replace("-", " ").split() if token]

    try:
        connection = _connect()
    except Exception:
        return []

    try:
        _create_schema(connection)
        connection.row_factory = sqlite3.Row

        # Narrow with SQL first so scoring only runs on plausible rows.
        clauses = []
        parameters = []
        like_targets = {query_lower} | set(tokens)
        for target in like_targets:
            clauses.append("name_lower LIKE ?")
            parameters.append(f"%{target}%")
        where = " OR ".join(clauses) if clauses else "1"
        if extension:
            normalized = extension if extension.startswith(".") else f".{extension}"
            where = f"({where}) AND extension = ?"
            parameters.append(normalized.lower())

        rows = connection.execute(
            f"SELECT path, name, name_lower, size, modified FROM files WHERE {where} LIMIT 4000",
            parameters,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    scored = []
    for row in rows:
        score = _score(row["name_lower"], row["path"].lower(), query_lower, tokens)
        if score:
            scored.append((score, row["modified"] or 0, dict(row)))

    scored.sort(key=lambda item: (-item[0], -item[1]))
    results = []
    for score, _modified, row in scored[:limit]:
        results.append({
            "path": row["path"],
            "name": row["name"],
            "size": row["size"],
            "modified": row["modified"],
            "score": score,
        })
    return results


def _shallow_scan(query_lower, limit=10, depth=3):
    """Direct scan of common folders, for files created since the last build."""
    matches = []
    for root in _default_roots():
        base_depth = root.rstrip(os.sep).count(os.sep)
        for current, directories, filenames in os.walk(root):
            directories[:] = [d for d in directories if not _should_skip_directory(d)]
            if current.count(os.sep) - base_depth >= depth:
                directories[:] = []
            for filename in filenames:
                if query_lower in filename.lower():
                    path = os.path.join(current, filename)
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    matches.append({
                        "path": path,
                        "name": filename,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "score": 60,
                    })
                    if len(matches) >= limit:
                        return matches
    return matches


def resolve_file(reference, extension=None):
    """Resolve a user's description of a file to a single path.

    Accepts a full path, or a name to look up in the index. Returns
    (path, alternatives) where alternatives lists other close matches.
    """
    text = str(reference or "").strip().strip('"').strip("'")
    if not text:
        return None, []

    expanded = os.path.expanduser(os.path.expandvars(text))
    if os.path.isfile(expanded):
        return os.path.abspath(expanded), []

    results = search(text, limit=8, extension=extension)
    if not results:
        results = _shallow_scan(text.lower(), limit=8)
    if not results:
        return None, []
    return results[0]["path"], results[1:]


def status():
    """Human-readable index status."""
    meta = get_meta()
    if _build_state["running"]:
        return "I'm still cataloguing your files. Lookups will speed up shortly."
    count = meta.get("file_count")
    if not count or count == "0":
        return "I haven't catalogued your files yet. Ask me to refresh my file index."
    built_at = meta.get("built_at")
    seconds = meta.get("build_seconds", "?")
    when = ""
    if built_at:
        try:
            age = time.time() - float(built_at)
            if age < 3600:
                when = f", updated {int(age // 60)} minutes ago"
            else:
                when = f", updated {int(age // 3600)} hours ago"
        except ValueError:
            when = ""
    return f"📁 I know about {int(count):,} files{when} (catalogued in {seconds}s)."
