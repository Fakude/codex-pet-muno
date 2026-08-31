"""
A curated reference for working out which libraries a new ability needs.

The problem this solves
-----------------------
When Muno hits a capability gap, something has to decide which package would fill
it. Leaving that to the model's memory produces confident guesses, and a confident
guess at a package name is exactly how people end up installing typosquats.

So instead Muno consults a human-curated corpus of working applications:

    https://github.com/Shubhamsaboo/awesome-llm-apps   Apache-2.0

That repository holds a hundred-odd example apps, each with its own
``requirements.txt``. Those files are real, working dependency sets for concrete
tasks: voice agents, retrieval, web scraping, document parsing, and so on. Reading
them turns "what should I use for X?" from a recall problem into a lookup.

What is and is not taken from it
--------------------------------
Only text is read: file paths, README titles, and dependency names. No code from
the repository is ever downloaded, imported, or executed. Package names found here
are treated purely as *candidates*, and still go through the same trust scoring and
the same human approval as any other install. A name appearing in a popular repo
raises a suggestion; it does not authorise anything.

Caching
-------
The file listing is fetched once and cached, so searching afterwards costs nothing
and works offline. Dependency files come from ``raw.githubusercontent.com``, which
does not consume the GitHub API rate limit.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

REPO = os.getenv("MUNO_RECIPE_REPO", "Shubhamsaboo/awesome-llm-apps")
BRANCH = os.getenv("MUNO_RECIPE_BRANCH", "main")

CACHE_DIR = os.path.expanduser("~/.codex_pet")
TREE_CACHE = os.path.join(CACHE_DIR, "recipes_tree.json")
CACHE_MAX_AGE_SECONDS = 7 * 86400

TREE_API = "https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

REQUEST_TIMEOUT = 25

# Plain-language topics mapped to the words that appear in this corpus's paths.
# Without this, a search for "read a PDF" misses directories named "rag" or
# "chat_with_pdf", which are the ones that actually matter.
TOPIC_HINTS = {
    "pdf": ("pdf", "document", "doc", "rag"),
    "document": ("pdf", "document", "doc", "rag"),
    "voice": ("voice", "speech", "audio", "whisper", "tts"),
    "speech": ("voice", "speech", "audio", "whisper"),
    "audio": ("voice", "audio", "music", "speech"),
    "music": ("music", "audio"),
    "video": ("video", "youtube", "multimodal"),
    "image": ("image", "vision", "multimodal", "photo"),
    "vision": ("vision", "image", "multimodal"),
    "scrape": ("scrap", "crawl", "web", "browser"),
    "web": ("web", "scrap", "crawl", "browser", "search"),
    "browser": ("browser", "web", "scrap"),
    "search": ("search", "web", "research"),
    "memory": ("memory", "mem0", "recall"),
    "remember": ("memory", "mem0", "recall"),
    "recall": ("memory", "mem0", "recall"),
    "history": ("memory", "mem0"),
    "persist": ("memory", "persistent"),
    "database": ("sql", "database", "postgres", "duckdb"),
    "sql": ("sql", "database", "postgres"),
    "spreadsheet": ("csv", "data", "excel", "pandas"),
    "excel": ("excel", "csv", "data"),
    "calendar": ("calendar", "schedul", "meeting"),
    "email": ("email", "mail", "gmail"),
    "finance": ("finance", "stock", "trading", "invest"),
    "travel": ("travel", "trip"),
    "health": ("health", "medical", "doctor"),
    "legal": ("legal", "law"),
    "resume": ("resume", "job", "recruit"),
    "chart": ("chart", "plot", "visual", "graph"),
    "translate": ("translat", "language"),
    "agent": ("agent", "crew", "autogen"),
    "rag": ("rag", "retriev", "vector", "embed"),
    "mcp": ("mcp",),
    "game": ("game", "chess", "pygame"),
}

# Packages that provide the app scaffolding in these examples rather than the
# ability being looked for. Suggesting them would be noise.
IGNORED_PACKAGES = {
    "streamlit", "gradio", "python-dotenv", "dotenv", "pip", "setuptools",
    "wheel", "typing-extensions", "pydantic", "requests", "aiohttp", "httpx",
    "openai", "anthropic", "google-generativeai", "groq", "ollama",
    "langchain", "langchain-core", "langchain-community", "langchain-openai",
    "agno", "phidata", "crewai", "autogen", "llama-index", "litellm",
    "fastapi", "uvicorn", "flask", "nest-asyncio", "tqdm", "rich", "numpy",
}

_tree_cache = {"paths": None, "fetched_at": 0.0, "error": None}


def _fetch(url, headers=None):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Muno-Desktop-Companion", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return response.read()


def _load_cached_tree():
    try:
        with open(TREE_CACHE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return None
        if time.time() - float(data.get("fetched_at", 0)) > CACHE_MAX_AGE_SECONDS:
            return data  # Stale but usable; refreshed opportunistically.
        return data
    except (OSError, ValueError):
        return None


def _save_cached_tree(paths):
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {"repo": REPO, "branch": BRANCH, "fetched_at": time.time(), "paths": paths}
    temporary = f"{TREE_CACHE}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temporary, TREE_CACHE)


def file_listing(force=False):
    """Return every file path in the reference repository.

    Cached on disk, so this is a single request the first time and free after.
    """
    if _tree_cache["paths"] is not None and not force:
        return _tree_cache["paths"]

    cached = _load_cached_tree() if not force else None
    fresh_enough = cached and (time.time() - float(cached.get("fetched_at", 0))) <= CACHE_MAX_AGE_SECONDS
    if cached and fresh_enough:
        _tree_cache["paths"] = cached.get("paths") or []
        return _tree_cache["paths"]

    headers = {"Accept": "application/vnd.github+json"}
    try:
        import github_api

        token = github_api._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    try:
        raw = _fetch(TREE_API.format(repo=REPO, branch=BRANCH), headers)
        tree = json.loads(raw)
        paths = [
            item.get("path", "") for item in tree.get("tree", [])
            if item.get("type") == "blob"
        ]
        paths = [p for p in paths if p]
        _tree_cache.update(paths=paths, fetched_at=time.time(), error=None)
        _save_cached_tree(paths)
        return paths
    except Exception as exc:
        _tree_cache["error"] = str(exc)
        # Fall back to a stale cache rather than failing entirely.
        if cached and cached.get("paths"):
            _tree_cache["paths"] = cached["paths"]
            return _tree_cache["paths"]
        return []


def _keywords_for(topic):
    """Expand a plain-language need into words likely to appear in paths."""
    text = str(topic or "").lower()
    words = [w for w in re.split(r"[^a-z0-9]+", text) if len(w) > 2]
    keywords = set()
    for word in words:
        keywords.add(word)
        # Match on the stem so "scraping" finds "scraper".
        if len(word) > 5:
            keywords.add(word[:5])
        for key, hints in TOPIC_HINTS.items():
            if word.startswith(key[:4]) or key.startswith(word[:4]):
                keywords.update(hints)
    return {k for k in keywords if len(k) > 2}


def find_examples(topic, limit=8):
    """Find example applications in the corpus matching a described need."""
    paths = file_listing()
    if not paths:
        return []
    keywords = _keywords_for(topic)
    if not keywords:
        return []

    # Score each app directory by how many keywords its path contains.
    scored = {}
    for path in paths:
        if not path.endswith("requirements.txt"):
            continue
        directory = path.rsplit("/", 1)[0]
        haystack = directory.lower().replace("_", " ").replace("-", " ")
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits:
            scored[directory] = max(scored.get(directory, 0), hits)

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], len(kv[0])))
    return [
        {"directory": directory, "score": hits,
         "name": directory.rsplit("/", 1)[-1].replace("_", " "),
         "requirements": f"{directory}/requirements.txt"}
        for directory, hits in ranked[:limit]
    ]


def _parse_requirements(text):
    """Pull bare package names out of a requirements file."""
    names = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "git+", "http")):
            continue
        # Drop version specifiers, extras and comments.
        name = re.split(r"[<>=!~\[;]", line, 1)[0].strip()
        if not name or " " in name:
            continue
        lowered = name.lower()
        if lowered in IGNORED_PACKAGES:
            continue
        if lowered.startswith(("langchain-", "llama-index-", "agno", "crewai")):
            continue
        names.append(name)
    return names


def packages_for(requirements_path):
    """Read one example's dependency list. Text only, no code."""
    try:
        raw = _fetch(RAW_BASE.format(repo=REPO, branch=BRANCH, path=requirements_path))
    except Exception:
        return []
    return _parse_requirements(raw.decode("utf-8", "replace"))


def suggest_packages(topic, max_examples=5, max_packages=6):
    """Suggest candidate packages for a need, with where each was seen.

    Returns (packages, examples) where packages is ordered by how often it
    appeared across matching examples.
    """
    examples = find_examples(topic, limit=max_examples)
    if not examples:
        return [], []

    counts = {}
    seen_in = {}
    for example in examples:
        for name in packages_for(example["requirements"]):
            key = name.lower()
            counts[key] = counts.get(key, 0) + 1
            seen_in.setdefault(key, []).append(example["name"])

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    packages = [
        {"name": name, "seen": count, "examples": seen_in[name][:3]}
        for name, count in ranked[:max_packages]
    ]
    return packages, examples


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def find_reference_examples(need):
    """
    Looks up how working open-source apps accomplish something.

    Consults a curated collection of example AI applications to see which
    libraries are actually used for a given task. Reads text only and installs
    nothing.

    Args:
        need: What the ability is, e.g. 'read PDF files' or 'scrape a website'.

    Returns:
        Matching examples and the packages they rely on
    """
    requirement = " ".join(str(need or "").split())
    if not requirement:
        return "❌ What ability should I look up?"

    packages, examples = suggest_packages(requirement)
    if not examples:
        error = _tree_cache.get("error")
        if error and not _tree_cache.get("paths"):
            return f"❌ I couldn't reach the reference collection: {str(error)[:100]}"
        return (
            f"I found no examples for '{requirement}' in the reference collection. "
            "Tell me a package name and I'll check it directly."
        )

    lines = [f"📚 How other projects do '{requirement}':", "", "Similar examples:"]
    for example in examples[:5]:
        lines.append(f"  • {example['name']}")

    if packages:
        lines.append("")
        lines.append("Libraries they use:")
        for package in packages:
            where = ", ".join(package["examples"][:2])
            lines.append(
                f"  • {package['name']} (in {package['seen']} of them: {where})"
            )
        lines.append("")
        lines.append(
            "These are suggestions from a public repository, not vetted yet. "
            "Ask me to research any of them and I'll check how trustworthy it is."
        )
    return "\n".join(lines)


def reference_status():
    """Report the state of the reference collection."""
    paths = file_listing()
    if not paths:
        return (
            f"I have no cached copy of the reference collection ({REPO}) and "
            "couldn't reach it just now."
        )
    apps = len({p.rsplit("/", 1)[0] for p in paths if p.endswith("requirements.txt")})
    cached = _load_cached_tree() or {}
    age = time.time() - float(cached.get("fetched_at", 0) or 0)
    when = f"{int(age // 86400)} days ago" if age > 86400 else "today"
    return (
        f"📚 Reference collection: {REPO}, {len(paths):,} files covering {apps} "
        f"example apps. Listing cached {when}."
    )
