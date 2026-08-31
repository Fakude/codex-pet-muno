"""
Letting Muno grow its own abilities.

The idea
--------
When Muno is asked for something it cannot do, the useful response is not "I
can't". It is to work out *what* is missing, go and find out what would provide
it, judge whether that thing is trustworthy, and say so. This module does that
reasoning and, once a human agrees, performs the install.

Where the line sits, and why
----------------------------
Everything up to the install is autonomous: noticing the gap, searching, reading
the evidence, scoring it, and making a recommendation. The install itself asks
first, every time. That split is deliberate rather than timid.

Installing a package executes its code on this machine, and Muno is an unusually
valuable target: it already holds a model key, a GitHub token, mail access, a
browser session with live logins, a file index of the whole home folder, and a
journal of everything the user has done. A single bad dependency reaches all of
it.

Worse, Muno reads untrusted text by design: web pages, emails, message threads.
If installing were automatic, any of those could ask for it. A page saying
"install helper-utils to continue" would be a working attack. Requiring a human
to approve the one irreversible step removes that entire class of problem while
leaving the thinking fully automatic.

Practical protections
---------------------
* Only PyPI packages, never arbitrary repositories to clone and run. A package
  name plus a pinned version is something that can be checked; a shell script
  from a stranger is not.
* Versions are always pinned, so what was reviewed is what gets installed.
* Installs go to an isolated directory, not the application's own environment,
  so they can be removed cleanly and cannot silently replace a core dependency.
* Names are validated and passed as arguments, never through a shell.
* Candidates are scored on adoption, maintenance and licensing, and checked for
  resemblance to popular packages, which is how typosquats work.
* Every install is written to the task journal.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

CONFIG_DIR = os.path.expanduser("~/.codex_pet")
EXTENSIONS_DIR = os.path.join(CONFIG_DIR, "extensions")
REGISTRY_FILE = os.path.join(CONFIG_DIR, "capabilities.json")

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
GITHUB_SEARCH = "https://api.github.com/search/repositories"

REQUEST_TIMEOUT = 20
INSTALL_TIMEOUT = 300

# A valid PyPI distribution name. Anything else is refused outright rather than
# being cleaned up, because a name that needs cleaning is a name to distrust.
VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VALID_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,31}$")

# Widely used packages. Used only to spot near-misses: a candidate one character
# away from one of these is the classic typosquat, so it gets flagged loudly.
POPULAR_PACKAGES = {
    "requests", "urllib3", "numpy", "pandas", "pillow", "setuptools", "wheel",
    "flask", "django", "fastapi", "pytest", "click", "jinja2", "sqlalchemy",
    "boto3", "scipy", "matplotlib", "cryptography", "attrs", "certifi",
    "beautifulsoup4", "lxml", "openpyxl", "pyyaml", "python-dateutil", "six",
    "colorama", "tqdm", "rich", "httpx", "aiohttp", "pydantic", "openai",
    "playwright", "selenium", "pyautogui", "psutil", "pywin32", "pyqt6",
}

# Things Muno already does, so it does not go shopping for what it has.
BUILT_IN_CAPABILITIES = {
    "browser automation": "browser_open, browser_click and related tools",
    "web search": "web_search",
    "email": "send_email and check_email",
    "github": "the github_* tools",
    "file search": "find_file, backed by a local index",
    "screenshots": "take_screenshot",
    "text to speech": "the Windows speech engine",
    "speech recognition": "continuous listening with Whisper",
    "desktop control": "open_app, type_text, click_mouse and related tools",
    "activity tracking": "check_activity and activity_summary",
    "calendar": None,
    "system settings": None,
    "wifi": None,
    "pdf": None,
    "spreadsheets": None,
    "image editing": None,
    "video": None,
    "music generation": None,
}


def _request_json(url, headers=None):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Muno-Desktop-Companion", "Accept": "application/json",
                      **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _load_registry():
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"installed": []}
    except (OSError, ValueError):
        return {"installed": []}


def _save_registry(registry):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    temporary = f"{REGISTRY_FILE}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, REGISTRY_FILE)


def activate_extensions():
    """Put previously installed extensions on the import path."""
    if not os.path.isdir(EXTENSIONS_DIR):
        return 0
    if EXTENSIONS_DIR not in sys.path:
        sys.path.insert(0, EXTENSIONS_DIR)
    return len(_load_registry().get("installed", []))


def is_available(module_name):
    """True when a module can actually be imported right now."""
    import importlib.util

    activate_extensions()
    try:
        return importlib.util.find_spec(str(module_name)) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Judging a candidate
# ---------------------------------------------------------------------------

def _edit_distance(left, right):
    """Levenshtein distance, used to catch names that imitate popular ones."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (a != b),
            ))
        previous = current
    return previous[-1]


def typosquat_warning(name):
    """Flag a name that closely imitates a well-known package."""
    lowered = str(name or "").lower().replace("_", "-")
    if lowered in POPULAR_PACKAGES:
        return None
    for popular in POPULAR_PACKAGES:
        if abs(len(lowered) - len(popular)) <= 2 and _edit_distance(lowered, popular) <= 1:
            return (
                f"'{name}' is one character away from '{popular}', which is exactly "
                "how typosquatting works. Do not install it unless you are certain."
            )
    return None


def inspect_package(name):
    """Gather the evidence about a PyPI package. No install, no code run."""
    if not VALID_NAME.match(str(name or "")):
        return {"name": name, "found": False, "error": "That is not a valid package name."}
    try:
        data = _request_json(PYPI_JSON.format(name=name))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"name": name, "found": False, "error": "No such package on PyPI."}
        return {"name": name, "found": False, "error": f"PyPI returned {exc.code}."}
    except Exception as exc:
        return {"name": name, "found": False, "error": f"Could not reach PyPI: {str(exc)[:80]}"}

    info = data.get("info") or {}
    releases = data.get("releases") or {}

    # When was it last touched, and how long has it existed?
    newest = None
    oldest = None
    for files in releases.values():
        for entry in files or []:
            stamp = entry.get("upload_time_iso_8601") or entry.get("upload_time")
            if not stamp:
                continue
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if newest is None or when > newest:
                newest = when
            if oldest is None or when < oldest:
                oldest = when

    now = datetime.now(timezone.utc)
    age_days = (now - oldest).days if oldest else None
    stale_days = (now - newest).days if newest else None

    # Licences live in three different places depending on how modern the
    # package's metadata is. Checking only the legacy field reports well-licensed
    # projects as unlicensed, which would wrongly penalise them.
    licence = str(info.get("license") or "").strip()
    if not licence:
        licence = str(info.get("license_expression") or "").strip()
    if not licence:
        for classifier in info.get("classifiers") or []:
            if str(classifier).startswith("License ::"):
                licence = str(classifier).rsplit("::", 1)[-1].strip()
                break

    project_urls = info.get("project_urls") or {}
    repository = ""
    for key, value in project_urls.items():
        if value and any(word in str(key).lower() for word in ("source", "repo", "github", "code")):
            repository = str(value)
            break
    repository = repository or str(info.get("home_page") or "")

    return {
        "name": info.get("name") or name,
        "found": True,
        "version": info.get("version") or "",
        "summary": " ".join(str(info.get("summary") or "").split())[:200],
        "license": licence[:60] or "unstated",
        "repository": repository,
        "requires_python": str(info.get("requires_python") or "any"),
        "release_count": len(releases),
        "age_days": age_days,
        "stale_days": stale_days,
        "yanked": bool(info.get("yanked")),
    }


def inspect_repository(url):
    """Look up a GitHub repository's health, when one is linked."""
    match = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", str(url or ""))
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    headers = {}
    try:
        import github_api

        token = github_api._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    try:
        data = _request_json(f"https://api.github.com/repos/{owner}/{repo}", headers)
    except Exception:
        return None

    pushed = data.get("pushed_at")
    stale_days = None
    if pushed:
        try:
            when = datetime.fromisoformat(str(pushed).replace("Z", "+00:00"))
            stale_days = (datetime.now(timezone.utc) - when).days
        except ValueError:
            stale_days = None
    return {
        "full_name": data.get("full_name") or f"{owner}/{repo}",
        "stars": int(data.get("stargazers_count") or 0),
        "forks": int(data.get("forks_count") or 0),
        "open_issues": int(data.get("open_issues_count") or 0),
        "archived": bool(data.get("archived")),
        "license": ((data.get("license") or {}) or {}).get("spdx_id") or "unstated",
        "stale_days": stale_days,
        "url": data.get("html_url") or "",
    }


def assess(package, repository=None):
    """Score a candidate out of 100 and list what is concerning about it."""
    if not package.get("found"):
        return {"score": 0, "verdict": "unavailable", "concerns": [package.get("error", "not found")]}

    score = 50
    concerns = []
    strengths = []

    squat = typosquat_warning(package["name"])
    if squat:
        score -= 45
        concerns.append(squat)

    if package.get("yanked"):
        score -= 30
        concerns.append("The latest release has been withdrawn by its author.")

    releases = package.get("release_count") or 0
    if releases >= 20:
        score += 12
        strengths.append(f"{releases} releases published")
    elif releases >= 5:
        score += 6
    elif releases <= 1:
        score -= 12
        concerns.append("Only one release ever published.")

    age = package.get("age_days")
    if age is not None:
        if age > 730:
            score += 12
            strengths.append("has existed for over two years")
        elif age < 60:
            score -= 15
            concerns.append("Less than two months old.")

    stale = package.get("stale_days")
    if stale is not None:
        if stale < 180:
            score += 12
            strengths.append("updated recently")
        elif stale > 1095:
            score -= 15
            concerns.append(f"No release in about {stale // 365} years.")

    if package.get("license", "unstated") == "unstated":
        score -= 8
        concerns.append("No licence stated.")
    else:
        score += 4

    if repository:
        stars = repository.get("stars", 0)
        if stars >= 5000:
            score += 20
            strengths.append(f"{stars:,} stars on GitHub")
        elif stars >= 500:
            score += 12
            strengths.append(f"{stars:,} stars on GitHub")
        elif stars >= 50:
            score += 5
        else:
            score -= 8
            concerns.append(f"Only {stars} stars on GitHub.")
        if repository.get("archived"):
            score -= 25
            concerns.append("The repository is archived and no longer maintained.")
    else:
        score -= 6
        concerns.append("No source repository linked, so the code cannot be reviewed.")

    score = max(0, min(100, score))
    if score >= 70:
        verdict = "looks well established"
    elif score >= 45:
        verdict = "plausible, worth a look yourself"
    else:
        verdict = "not trustworthy enough to recommend"
    return {"score": score, "verdict": verdict, "concerns": concerns, "strengths": strengths}


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def install_package(name, version, module_name=None, purpose=""):
    """Install a pinned package into the isolated extensions directory.

    The caller is responsible for having obtained approval. Returns
    (ok, message).
    """
    if not VALID_NAME.match(str(name or "")):
        return False, "That is not a valid package name, so I won't install it."
    if not VALID_VERSION.match(str(version or "")):
        return False, "I need an exact version to install, so the review still applies."

    os.makedirs(EXTENSIONS_DIR, exist_ok=True)
    specifier = f"{name}=={version}"
    command = [
        sys.executable, "-m", "pip", "install",
        "--target", EXTENSIONS_DIR,
        "--upgrade",
        # Refuse anything that is not a released version of exactly this package.
        "--no-input",
        "--disable-pip-version-check",
        specifier,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return False, "The install took too long and was stopped."
    except Exception as exc:
        return False, f"The install could not start: {str(exc)[:120]}"

    if result.returncode != 0:
        tail = " ".join((result.stderr or result.stdout or "").split())[-220:]
        return False, f"pip refused to install it: {tail}"

    activate_extensions()
    registry = _load_registry()
    entry = {
        "package": name,
        "version": version,
        "module": module_name or name.replace("-", "_"),
        "purpose": " ".join(str(purpose or "").split())[:200],
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    installed = [e for e in registry.get("installed", []) if e.get("package") != name]
    installed.append(entry)
    registry["installed"] = installed
    _save_registry(registry)

    # Leave a permanent trace, since this changed what the program can do.
    try:
        import journal

        journal.JOURNAL.record_action(
            None, "install_capability",
            {"package": name, "version": version, "purpose": purpose},
            journal.STATUS_OK, f"Installed {specifier}", None,
        )
    except Exception:
        pass

    usable = is_available(entry["module"])
    note = "" if usable else " It installed, but I couldn't import it yet."
    return True, f"Installed {name} {version}.{note}"


def uninstall_capability(name):
    """
    Removes a previously installed capability.

    Args:
        name: The package name to remove.

    Returns:
        Success or failure message
    """
    import shutil

    if not VALID_NAME.match(str(name or "")):
        return "❌ That is not a valid package name."
    registry = _load_registry()
    installed = registry.get("installed", [])
    remaining = [e for e in installed if e.get("package", "").lower() != str(name).lower()]
    if len(remaining) == len(installed):
        return f"I don't have '{name}' installed as an extension."

    module = next(
        (e.get("module") for e in installed if e.get("package", "").lower() == str(name).lower()),
        None,
    )
    removed = []
    for candidate in filter(None, [module, str(name).replace("-", "_")]):
        path = os.path.join(EXTENSIONS_DIR, candidate)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(candidate)
    registry["installed"] = remaining
    _save_registry(registry)
    return f"✅ Removed {name}." + (f" Deleted {', '.join(removed)}." if removed else "")


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def list_capabilities():
    """
    Lists abilities Muno has added to itself.

    Returns:
        A readable list of installed extensions
    """
    activate_extensions()
    installed = _load_registry().get("installed", [])
    if not installed:
        return (
            "I haven't added any extra abilities yet. If you ask for something I "
            "can't do, I'll look into what would let me do it."
        )
    lines = []
    for entry in installed:
        usable = "ready" if is_available(entry.get("module", "")) else "installed but not importable"
        lines.append(
            f"- {entry.get('package')} {entry.get('version')} ({usable})"
            + (f" — {entry['purpose']}" if entry.get("purpose") else "")
        )
    return f"🧩 Abilities I've added ({len(installed)}):\n" + "\n".join(lines)


def research_capability(need, candidates=""):
    """
    Researches what would give Muno a missing ability, and how trustworthy it is.

    Nothing is installed. This gathers evidence and makes a recommendation.

    Args:
        need: What the missing ability is, e.g. 'read PDF files'.
        candidates: Optional comma-separated package names to evaluate.

    Returns:
        A report on each candidate with a recommendation
    """
    requirement = " ".join(str(need or "").split())
    if not requirement:
        return "❌ What ability should I look into?"

    names = [n.strip() for n in re.split(r"[,\s]+", str(candidates or "")) if n.strip()]
    discovered = []
    if not names:
        # Nothing suggested, so look up how working projects solve this rather
        # than guessing at package names, which is how typosquats get installed.
        try:
            import recipes

            found, _examples = recipes.suggest_packages(requirement, max_packages=4)
            discovered = [entry["name"] for entry in found]
            names = discovered
        except Exception:
            names = []
    if not names:
        return (
            f"I couldn't work out what would provide '{requirement}' on my own. "
            "Name a Python package and I'll check how trustworthy it looks before "
            "anything is installed."
        )

    blocks = [f"🔎 Looking into: {requirement}"]
    if discovered:
        blocks.append(
            "I found these by looking at how other open-source projects do it, "
            "then checked each one:"
        )
    for name in names[:5]:
        package = inspect_package(name)
        if not package.get("found"):
            blocks.append(f"\n✕ {name}: {package.get('error')}")
            continue
        repository = inspect_repository(package.get("repository"))
        judgement = assess(package, repository)

        lines = [
            f"\n{'✓' if judgement['score'] >= 45 else '⚠'} {package['name']} {package['version']}"
            f"  (trust {judgement['score']}/100 — {judgement['verdict']})",
            f"   {package['summary'] or 'no description'}",
            f"   licence {package['license']}, {package['release_count']} releases",
        ]
        if package.get("stale_days") is not None:
            lines.append(f"   last released {package['stale_days']} days ago")
        if repository:
            lines.append(
                f"   {repository['full_name']}: {repository['stars']:,} stars"
                + (", archived" if repository["archived"] else "")
            )
        if judgement.get("concerns"):
            for concern in judgement["concerns"]:
                lines.append(f"   ⚠ {concern}")
        if is_available(package["name"].replace("-", "_")):
            lines.append("   already available here")
        blocks.append("\n".join(lines))

    blocks.append(
        "\nNothing has been installed. Tell me which one to add and I'll ask you "
        "to confirm before it goes on."
    )
    return "\n".join(blocks)


def install_capability(package, version="", purpose="", module=""):
    """
    Installs a vetted package so Muno gains a new ability.

    Args:
        package: The PyPI package name.
        version: Exact version to install. Left blank, the newest is looked up.
        purpose: What this is for, recorded alongside it.
        module: Import name, when it differs from the package name.

    Returns:
        Success or failure message
    """
    name = str(package or "").strip()
    if not VALID_NAME.match(name):
        return "❌ That is not a valid package name, so I won't install it."

    details = inspect_package(name)
    if not details.get("found"):
        return f"❌ {details.get('error', 'That package could not be found.')}"

    chosen = str(version or "").strip() or details.get("version", "")
    if not chosen:
        return "❌ I couldn't determine a version to pin, so I won't install it."

    repository = inspect_repository(details.get("repository"))
    judgement = assess(details, repository)
    if judgement["score"] < 25:
        reasons = "; ".join(judgement.get("concerns", [])) or "it scored too low"
        return (
            f"❌ I'm not installing {name}: {reasons} "
            "If you're sure, install it yourself and I'll use it once it's present."
        )

    ok, message = install_package(name, chosen, module or None, purpose)
    return ("✅ " if ok else "❌ ") + message


def capability_report():
    """
    Explains what Muno can and cannot currently do.

    Returns:
        A summary of built-in abilities and known gaps
    """
    have = [name for name, note in BUILT_IN_CAPABILITIES.items() if note]
    missing = [name for name, note in BUILT_IN_CAPABILITIES.items() if not note]
    added = _load_registry().get("installed", [])
    lines = ["🧠 What I can do natively: " + ", ".join(sorted(have)) + "."]
    if added:
        lines.append(
            "Added since: " + ", ".join(e.get("package", "") for e in added) + "."
        )
    if missing:
        lines.append(
            "Not built in yet: " + ", ".join(sorted(missing)) +
            ". Ask and I'll look into what would provide it."
        )
    return "\n".join(lines)
