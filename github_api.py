"""
GitHub capabilities for Muno.

This talks to the GitHub REST API directly rather than driving the website in a
browser. The API is deterministic, fast, and does not break when GitHub changes
its layout, so "create a repo and upload that PDF" is a couple of HTTP calls
instead of a fragile sequence of clicks.

Authentication
--------------
A personal access token is read from ``GITHUB_TOKEN`` (or ``GH_TOKEN``) in the
environment. The token is never written to disk, never echoed into a reply, and
never included in an error message.

Create one at https://github.com/settings/tokens with ``repo`` scope, plus
``workflow`` if you want Muno to trigger GitHub Actions, and ``delete_repo`` if
you want it to delete repositories.
"""
import base64
import os
import re
import shutil
import subprocess

import requests

import file_index

API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT = 20

# The Contents API accepts large files, but base64 in JSON is wasteful and slow.
# Anything bigger than this should go through git itself.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_cached_login = None


_gh_token_cache = {"value": None, "checked": False}


def _gh_cli_token():
    """Borrow the token from the GitHub CLI, if it is signed in.

    This is the low-friction path: ``gh auth login`` is a browser flow, so the
    user never has to create or paste a personal access token, and Muno reuses
    the session that already exists on the machine.
    """
    if _gh_token_cache["checked"]:
        return _gh_token_cache["value"] or ""
    _gh_token_cache["checked"] = True

    executable = shutil.which("gh")
    if not executable:
        _gh_token_cache["value"] = ""
        return ""
    try:
        result = subprocess.run(
            [executable, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        token = result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        token = ""
    _gh_token_cache["value"] = token
    return token


def _token():
    explicit = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    return explicit or _gh_cli_token()


def has_token():
    return bool(_token())


def gh_cli_available():
    return bool(shutil.which("gh"))


def github_connect():
    """
    Starts the GitHub sign-in flow so no token has to be created by hand.

    Returns:
        Instructions or confirmation
    """
    if has_token():
        try:
            return f"✅ Already connected to GitHub as {_login()}."
        except GitHubError:
            pass
    if not gh_cli_available():
        return (
            "To connect without creating a token, install the GitHub CLI from "
            "cli.github.com, then ask me again. Otherwise set GITHUB_TOKEN."
        )
    # Reset the cache so a completed login is picked up.
    _gh_token_cache.update({"value": None, "checked": False})
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", "cmd", "/k", "gh auth login --web --git-protocol https"]
        )
    except Exception as exc:
        return f"❌ I couldn't start the sign-in: {exc}"
    return (
        "I've opened a terminal running the GitHub sign-in. Follow the prompts, "
        "then tell me when you're done and I'll pick it up. No token to copy."
    )


def _headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Muno-Desktop-Companion",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class GitHubError(Exception):
    """A GitHub request failed in a way worth reporting to the user."""


def _describe_failure(response):
    """Turn an API failure into a sentence, without leaking credentials."""
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    detail = str(payload.get("message", "")).strip()

    if status == 401:
        return "GitHub rejected the access token. Check GITHUB_TOKEN and restart Muno."
    if status == 403:
        if "rate limit" in detail.lower():
            return "GitHub's rate limit was reached. Try again in a few minutes."
        return "The token doesn't have permission for that. Check its scopes on GitHub."
    if status == 404:
        return "GitHub couldn't find that. Check the repository or path name."
    if status == 422:
        errors = payload.get("errors") or []
        specifics = "; ".join(
            str(e.get("message") or e.get("field", "")) for e in errors if isinstance(e, dict)
        )
        return f"GitHub rejected the request: {detail or 'validation failed'}{(' - ' + specifics) if specifics else ''}"
    return f"GitHub returned an error ({status})" + (f": {detail}" if detail else ".")


def _request(method, path, **kwargs):
    """Perform an API call and return parsed JSON.

    Raises GitHubError with a readable message on failure.
    """
    if not has_token():
        raise GitHubError(
            "No GitHub token is set. Add GITHUB_TOKEN to your environment and restart Muno."
        )
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    try:
        response = requests.request(
            method, url, headers=_headers(), timeout=REQUEST_TIMEOUT, **kwargs
        )
    except requests.Timeout:
        raise GitHubError("GitHub took too long to respond. Try again.")
    except requests.RequestException:
        raise GitHubError("I couldn't reach GitHub. Check your connection.")

    if response.status_code >= 400:
        raise GitHubError(_describe_failure(response))
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def _login():
    """Return the authenticated username, cached for the session."""
    global _cached_login
    if _cached_login:
        return _cached_login
    data = _request("GET", "/user")
    _cached_login = data.get("login")
    return _cached_login


def _full_name(repo):
    """Accept 'repo' or 'owner/repo' and return 'owner/repo'."""
    name = str(repo or "").strip().strip("/")
    if not name:
        raise GitHubError("A repository name is required.")
    # Tolerate a pasted URL.
    match = re.search(r"github\.com[/:]([^/]+/[^/.]+)", name)
    if match:
        return match.group(1)
    if "/" in name:
        return name
    return f"{_login()}/{name}"


def _slug(name):
    """Turn spoken text like 'my cool project' into a valid repo name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").strip())
    return re.sub(r"-{2,}", "-", cleaned).strip("-._") or "new-repository"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def github_status():
    """Report whether Muno can act on GitHub, and as whom."""
    if not has_token():
        if gh_cli_available():
            return (
                "GitHub isn't connected yet, but the GitHub CLI is installed, so no "
                "token is needed. Say 'connect my GitHub' and I'll start the browser "
                "sign-in."
            )
        return (
            "GitHub isn't connected yet. Either install the GitHub CLI from "
            "cli.github.com for a browser sign-in, or set GITHUB_TOKEN."
        )
    try:
        user = _request("GET", "/user")
        source = "an access token" if os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") \
            else "your GitHub CLI sign-in"
        return (
            f"✅ Connected to GitHub as {user.get('login')} via {source} "
            f"({user.get('public_repos', 0)} public repos)."
        )
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

def github_create_repo(name, description="", private=True, auto_init=True):
    """Create a repository under the authenticated account."""
    repo_name = _slug(name)
    body = {
        "name": repo_name,
        "description": str(description or "")[:350],
        "private": bool(private) if isinstance(private, bool) else str(private).lower() != "false",
        "auto_init": bool(auto_init) if isinstance(auto_init, bool) else str(auto_init).lower() != "false",
    }
    try:
        data = _request("POST", "/user/repos", json=body)
        visibility = "private" if data.get("private") else "public"
        return f"✅ Created the {visibility} repository {data.get('full_name')} at {data.get('html_url')}"
    except GitHubError as e:
        return f"❌ {e}"


def github_list_repos(limit=15):
    """List the authenticated user's repositories, most recently updated first."""
    try:
        count = max(1, min(int(limit or 15), 50))
    except (TypeError, ValueError):
        count = 15
    try:
        data = _request(
            "GET", "/user/repos", params={"sort": "updated", "per_page": count, "affiliation": "owner"}
        )
        if not data:
            return "You don't have any repositories yet."
        lines = [
            f"{i + 1}. {r.get('name')} ({'private' if r.get('private') else 'public'})"
            for i, r in enumerate(data)
        ]
        return f"📦 Your repositories ({len(lines)}):\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


def github_repo_info(repo):
    """Summarize one repository."""
    try:
        data = _request("GET", f"/repos/{_full_name(repo)}")
        return (
            f"📦 {data.get('full_name')}\n"
            f"{data.get('description') or 'No description'}\n"
            f"Visibility: {'private' if data.get('private') else 'public'}\n"
            f"Default branch: {data.get('default_branch')}\n"
            f"Stars: {data.get('stargazers_count', 0)}, open issues: {data.get('open_issues_count', 0)}\n"
            f"{data.get('html_url')}"
        )
    except GitHubError as e:
        return f"❌ {e}"


def github_update_repo(repo, description=None, homepage=None, private=None, topics=None):
    """Update a repository's description, homepage, visibility, or topics."""
    try:
        full = _full_name(repo)
        body = {}
        if description is not None:
            body["description"] = str(description)[:350]
        if homepage is not None:
            body["homepage"] = str(homepage)
        if private is not None:
            body["private"] = private if isinstance(private, bool) else str(private).lower() != "false"

        changed = []
        if body:
            _request("PATCH", f"/repos/{full}", json=body)
            changed.extend(body.keys())

        if topics is not None:
            if isinstance(topics, str):
                topic_list = [t.strip().lower() for t in re.split(r"[,\s]+", topics) if t.strip()]
            else:
                topic_list = [str(t).strip().lower() for t in topics if str(t).strip()]
            _request("PUT", f"/repos/{full}/topics", json={"names": topic_list})
            changed.append("topics")

        if not changed:
            return "Nothing was specified to change."
        return f"✅ Updated {', '.join(changed)} on {full}."
    except GitHubError as e:
        return f"❌ {e}"


def github_delete_repo(repo):
    """Permanently delete a repository."""
    try:
        full = _full_name(repo)
        _request("DELETE", f"/repos/{full}")
        return f"✅ Deleted the repository {full}."
    except GitHubError as e:
        return f"❌ {e}"


def github_search_repos(query, limit=10):
    """Search public repositories."""
    text = str(query or "").strip()
    if not text:
        return "❌ No search terms were provided."
    try:
        count = max(1, min(int(limit or 10), 25))
    except (TypeError, ValueError):
        count = 10
    try:
        data = _request("GET", "/search/repositories", params={"q": text, "per_page": count})
        items = data.get("items", [])
        if not items:
            return f"No repositories matched '{text}'."
        lines = [
            f"{i + 1}. {r.get('full_name')} ⭐{r.get('stargazers_count', 0)} - {(r.get('description') or '')[:70]}"
            for i, r in enumerate(items)
        ]
        return f"🔎 Results for '{text}':\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _existing_sha(full_name, dest_path, branch=None):
    """Return the blob sha when a file already exists, else None."""
    params = {"ref": branch} if branch else None
    try:
        data = _request("GET", f"/repos/{full_name}/contents/{dest_path}", params=params)
    except GitHubError:
        return None
    if isinstance(data, dict):
        return data.get("sha")
    return None


def github_upload_file(repo, file, dest_path=None, message=None, branch=None):
    """Upload a local file to a repository, creating or updating it.

    ``file`` may be a full path or just a name: the local file index resolves
    names, so "upload MUNO.pdf" works without the user knowing the path.
    """
    reference = str(file or "").strip()
    if not reference:
        return "❌ Which file should I upload?"

    local_path, alternatives = file_index.resolve_file(reference)
    if not local_path:
        return f"❌ I couldn't find a file called '{reference}' on this computer."

    try:
        size = os.path.getsize(local_path)
    except OSError as e:
        return f"❌ I couldn't read that file: {e}"
    if size > MAX_UPLOAD_BYTES:
        return (
            f"❌ '{os.path.basename(local_path)}' is {size / 1048576:.1f} MB, which is too "
            "large for this method. Use git for files above 25 MB."
        )

    try:
        with open(local_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
    except OSError as e:
        return f"❌ I couldn't read that file: {e}"

    target = str(dest_path or "").strip().lstrip("/") or os.path.basename(local_path)
    try:
        full = _full_name(repo)
        body = {
            "message": str(message or f"Add {os.path.basename(local_path)} via Muno"),
            "content": encoded,
        }
        if branch:
            body["branch"] = str(branch)
        existing = _existing_sha(full, target, branch)
        if existing:
            body["sha"] = existing

        data = _request("PUT", f"/repos/{full}/contents/{target}", json=body)
        action = "Updated" if existing else "Uploaded"
        link = (data.get("content") or {}).get("html_url", "")
        note = ""
        if alternatives:
            note = f"\n(I used {local_path}. {len(alternatives)} other files had similar names.)"
        return f"✅ {action} {target} in {full}. {link}{note}"
    except GitHubError as e:
        return f"❌ {e}"


def github_read_file(repo, path, branch=None):
    """Read a text file from a repository."""
    target = str(path or "").strip().lstrip("/")
    if not target:
        return "❌ Which file should I read?"
    try:
        params = {"ref": branch} if branch else None
        data = _request("GET", f"/repos/{_full_name(repo)}/contents/{target}", params=params)
        if isinstance(data, list):
            return "❌ That path is a folder, not a file."
        if data.get("encoding") != "base64":
            return "❌ I can't read that file's format."
        try:
            text = base64.b64decode(data.get("content", "")).decode("utf-8")
        except UnicodeDecodeError:
            return f"'{target}' looks like a binary file, so there's no text to read."
        if len(text) > 4000:
            text = text[:4000] + "\n… (truncated)"
        return f"📄 {target}:\n\n{text}"
    except GitHubError as e:
        return f"❌ {e}"


def github_list_files(repo, path="", branch=None):
    """List the contents of a repository folder."""
    try:
        params = {"ref": branch} if branch else None
        target = str(path or "").strip().lstrip("/")
        data = _request("GET", f"/repos/{_full_name(repo)}/contents/{target}", params=params)
        if isinstance(data, dict):
            return f"'{target}' is a single file, not a folder."
        if not data:
            return "That folder is empty."
        lines = [
            f"{'📁' if item.get('type') == 'dir' else '📄'} {item.get('name')}"
            for item in data
        ]
        location = target or "the repository root"
        return f"Contents of {location}:\n" + "\n".join(lines[:40])
    except GitHubError as e:
        return f"❌ {e}"


def github_delete_file(repo, path, message=None, branch=None):
    """Delete a file from a repository."""
    target = str(path or "").strip().lstrip("/")
    if not target:
        return "❌ Which file should I delete?"
    try:
        full = _full_name(repo)
        sha = _existing_sha(full, target, branch)
        if not sha:
            return f"❌ '{target}' doesn't exist in {full}."
        body = {"message": str(message or f"Delete {target} via Muno"), "sha": sha}
        if branch:
            body["branch"] = str(branch)
        _request("DELETE", f"/repos/{full}/contents/{target}", json=body)
        return f"✅ Deleted {target} from {full}."
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

def github_create_issue(repo, title, body=""):
    """Open an issue."""
    if not str(title or "").strip():
        return "❌ An issue title is required."
    try:
        data = _request(
            "POST",
            f"/repos/{_full_name(repo)}/issues",
            json={"title": str(title), "body": str(body or "")},
        )
        return f"✅ Opened issue #{data.get('number')}: {data.get('title')} - {data.get('html_url')}"
    except GitHubError as e:
        return f"❌ {e}"


def github_list_issues(repo, state="open", limit=10):
    """List issues in a repository."""
    try:
        count = max(1, min(int(limit or 10), 30))
    except (TypeError, ValueError):
        count = 10
    chosen = str(state or "open").lower()
    if chosen not in {"open", "closed", "all"}:
        chosen = "open"
    try:
        data = _request(
            "GET",
            f"/repos/{_full_name(repo)}/issues",
            params={"state": chosen, "per_page": count},
        )
        issues = [i for i in data if "pull_request" not in i]
        if not issues:
            return f"There are no {chosen} issues."
        lines = [f"#{i.get('number')} {i.get('title')}" for i in issues]
        return f"🐛 {chosen.title()} issues:\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


def github_comment_issue(repo, number, body):
    """Comment on an issue or pull request."""
    if not str(body or "").strip():
        return "❌ There's no comment text."
    try:
        issue_number = int(number)
    except (TypeError, ValueError):
        return "❌ Which issue number?"
    try:
        data = _request(
            "POST",
            f"/repos/{_full_name(repo)}/issues/{issue_number}/comments",
            json={"body": str(body)},
        )
        return f"✅ Commented on #{issue_number}. {data.get('html_url', '')}"
    except GitHubError as e:
        return f"❌ {e}"


def github_close_issue(repo, number):
    """Close an issue."""
    try:
        issue_number = int(number)
    except (TypeError, ValueError):
        return "❌ Which issue number?"
    try:
        _request(
            "PATCH",
            f"/repos/{_full_name(repo)}/issues/{issue_number}",
            json={"state": "closed"},
        )
        return f"✅ Closed issue #{issue_number}."
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# Branches and pull requests
# ---------------------------------------------------------------------------

def github_list_branches(repo):
    """List a repository's branches."""
    try:
        data = _request("GET", f"/repos/{_full_name(repo)}/branches", params={"per_page": 30})
        if not data:
            return "That repository has no branches yet."
        return "🌿 Branches:\n" + "\n".join(f"- {b.get('name')}" for b in data)
    except GitHubError as e:
        return f"❌ {e}"


def github_create_branch(repo, name, from_branch=None):
    """Create a branch from another branch."""
    new_name = _slug(name)
    try:
        full = _full_name(repo)
        source = str(from_branch or "").strip()
        if not source:
            source = _request("GET", f"/repos/{full}").get("default_branch", "main")
        reference = _request("GET", f"/repos/{full}/git/ref/heads/{source}")
        sha = (reference.get("object") or {}).get("sha")
        if not sha:
            return f"❌ I couldn't find the '{source}' branch to branch from."
        _request(
            "POST",
            f"/repos/{full}/git/refs",
            json={"ref": f"refs/heads/{new_name}", "sha": sha},
        )
        return f"✅ Created branch '{new_name}' from '{source}'."
    except GitHubError as e:
        return f"❌ {e}"


def github_create_pull_request(repo, title, head, base=None, body=""):
    """Open a pull request."""
    if not str(title or "").strip() or not str(head or "").strip():
        return "❌ A title and a source branch are required."
    try:
        full = _full_name(repo)
        target = str(base or "").strip()
        if not target:
            target = _request("GET", f"/repos/{full}").get("default_branch", "main")
        data = _request(
            "POST",
            f"/repos/{full}/pulls",
            json={
                "title": str(title),
                "head": str(head),
                "base": target,
                "body": str(body or ""),
            },
        )
        return f"✅ Opened pull request #{data.get('number')}: {data.get('html_url')}"
    except GitHubError as e:
        return f"❌ {e}"


def github_list_pull_requests(repo, state="open"):
    """List pull requests."""
    chosen = str(state or "open").lower()
    if chosen not in {"open", "closed", "all"}:
        chosen = "open"
    try:
        data = _request(
            "GET", f"/repos/{_full_name(repo)}/pulls", params={"state": chosen, "per_page": 20}
        )
        if not data:
            return f"There are no {chosen} pull requests."
        lines = [
            f"#{p.get('number')} {p.get('title')} ({p.get('head', {}).get('ref')} → {p.get('base', {}).get('ref')})"
            for p in data
        ]
        return f"🔀 {chosen.title()} pull requests:\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


def github_merge_pull_request(repo, number, method="merge"):
    """Merge a pull request."""
    try:
        pull_number = int(number)
    except (TypeError, ValueError):
        return "❌ Which pull request number?"
    chosen = str(method or "merge").lower()
    if chosen not in {"merge", "squash", "rebase"}:
        chosen = "merge"
    try:
        data = _request(
            "PUT",
            f"/repos/{_full_name(repo)}/pulls/{pull_number}/merge",
            json={"merge_method": chosen},
        )
        if data.get("merged"):
            return f"✅ Merged pull request #{pull_number}."
        return f"GitHub didn't merge #{pull_number}: {data.get('message', 'unknown reason')}"
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# History and releases
# ---------------------------------------------------------------------------

def github_list_commits(repo, limit=10):
    """List recent commits."""
    try:
        count = max(1, min(int(limit or 10), 30))
    except (TypeError, ValueError):
        count = 10
    try:
        data = _request("GET", f"/repos/{_full_name(repo)}/commits", params={"per_page": count})
        if not data:
            return "There are no commits yet."
        lines = []
        for commit in data:
            info = commit.get("commit", {})
            summary = str(info.get("message", "")).splitlines()[0][:70]
            author = (info.get("author") or {}).get("name", "unknown")
            lines.append(f"- {summary} ({author})")
        return "📝 Recent commits:\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


def github_create_release(repo, tag, name=None, body=""):
    """Publish a release."""
    tag_name = str(tag or "").strip()
    if not tag_name:
        return "❌ A tag name is required, for example v1.0.0."
    try:
        data = _request(
            "POST",
            f"/repos/{_full_name(repo)}/releases",
            json={
                "tag_name": tag_name,
                "name": str(name or tag_name),
                "body": str(body or ""),
            },
        )
        return f"✅ Published release {tag_name}: {data.get('html_url')}"
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# GitHub Actions
# ---------------------------------------------------------------------------

def github_list_workflows(repo):
    """List the GitHub Actions workflows in a repository."""
    try:
        data = _request("GET", f"/repos/{_full_name(repo)}/actions/workflows")
        workflows = data.get("workflows", [])
        if not workflows:
            return "That repository has no GitHub Actions workflows."
        lines = [f"- {w.get('name')} ({w.get('state')})" for w in workflows]
        return "⚙️ Workflows:\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


def github_run_workflow(repo, workflow, ref=None):
    """Trigger a workflow run."""
    identifier = str(workflow or "").strip()
    if not identifier:
        return "❌ Which workflow should I run?"
    try:
        full = _full_name(repo)
        branch = str(ref or "").strip()
        if not branch:
            branch = _request("GET", f"/repos/{full}").get("default_branch", "main")

        # Accept a friendly name as well as a filename or numeric id.
        if not identifier.isdigit() and not identifier.endswith((".yml", ".yaml")):
            listing = _request("GET", f"/repos/{full}/actions/workflows")
            for candidate in listing.get("workflows", []):
                if identifier.lower() in str(candidate.get("name", "")).lower():
                    identifier = str(candidate.get("id"))
                    break

        _request(
            "POST",
            f"/repos/{full}/actions/workflows/{identifier}/dispatches",
            json={"ref": branch},
        )
        return f"✅ Started the workflow on '{branch}'."
    except GitHubError as e:
        return f"❌ {e}"


def github_list_workflow_runs(repo, limit=10):
    """List recent GitHub Actions runs and their results."""
    try:
        count = max(1, min(int(limit or 10), 30))
    except (TypeError, ValueError):
        count = 10
    try:
        data = _request(
            "GET", f"/repos/{_full_name(repo)}/actions/runs", params={"per_page": count}
        )
        runs = data.get("workflow_runs", [])
        if not runs:
            return "There are no workflow runs yet."
        lines = [
            f"- {r.get('name')}: {r.get('status')}"
            + (f" ({r.get('conclusion')})" if r.get("conclusion") else "")
            for r in runs
        ]
        return "⚙️ Recent workflow runs:\n" + "\n".join(lines)
    except GitHubError as e:
        return f"❌ {e}"


# ---------------------------------------------------------------------------
# Local file lookup, exposed so Muno can confirm what it found
# ---------------------------------------------------------------------------

def find_file(name, limit=5):
    """Find files on this computer by name."""
    text = str(name or "").strip()
    if not text:
        return "❌ What should I look for?"
    results = file_index.search(text, limit=max(1, min(int(limit or 5), 20)))
    if not results:
        path, _ = file_index.resolve_file(text)
        if path:
            return f"📄 Found: {path}"
        return f"I couldn't find anything called '{text}'."
    lines = []
    for item in results:
        size = item.get("size") or 0
        readable = f"{size / 1048576:.1f} MB" if size > 1048576 else f"{max(size // 1024, 1)} KB"
        lines.append(f"- {item['path']} ({readable})")
    return f"📄 Files matching '{text}':\n" + "\n".join(lines)


def refresh_file_index():
    """Rebuild the local file catalogue in the background."""
    if file_index.build_index_async():
        return "🔄 I'm re-cataloguing your files now. It usually takes a few seconds."
    return "I'm already cataloguing your files."


def file_index_status():
    """Report how many files Muno knows about."""
    return file_index.status()


# ---------------------------------------------------------------------------
# Tool registration
#
# The schemas are generated from a compact table. Hand-writing ~30 JSON blocks
# invites drift between the schema and the functions, and this keeps the two
# defined in one place.
# ---------------------------------------------------------------------------

STRING = "string"
INTEGER = "integer"
BOOLEAN = "boolean"

_REPO = (STRING, "Repository name, or owner/name for someone else's repository")


def _schema(name, description, properties=None, required=None):
    """Build one OpenAI-style function schema entry."""
    fields = {}
    for field_name, (field_type, field_description) in (properties or {}).items():
        fields[field_name] = {"type": field_type, "description": field_description}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": fields,
                "required": list(required or []),
            },
        },
    }


_SPEC = [
    # (function, name, description, properties, required)
    (github_status, "github_status",
     "Checks whether Muno is connected to GitHub and reports which account.", {}, []),

    (github_connect, "github_connect",
     "Starts the GitHub sign-in so Muno can act on GitHub without the user creating "
     "an access token. Use when the user wants to connect or link their GitHub.",
     {}, []),

    (github_create_repo, "github_create_repo",
     "Creates a new GitHub repository. Use when the user wants to make a repo.",
     {"name": (STRING, "Repository name; spoken names are converted automatically"),
      "description": (STRING, "Short description of the repository"),
      "private": (BOOLEAN, "True for a private repo (default), False for public"),
      "auto_init": (BOOLEAN, "Whether to create an initial README (default true)")},
     ["name"]),

    (github_list_repos, "github_list_repos",
     "Lists the user's GitHub repositories.",
     {"limit": (INTEGER, "How many to list (default 15)")}, []),

    (github_repo_info, "github_repo_info",
     "Gets details about one repository: description, visibility, branch, stars.",
     {"repo": _REPO}, ["repo"]),

    (github_update_repo, "github_update_repo",
     "Updates a repository's description, homepage, visibility, or topics.",
     {"repo": _REPO,
      "description": (STRING, "New description"),
      "homepage": (STRING, "New homepage URL"),
      "private": (BOOLEAN, "True to make private, False to make public"),
      "topics": (STRING, "Comma-separated topics")},
     ["repo"]),

    (github_delete_repo, "github_delete_repo",
     "Permanently deletes a repository. This cannot be undone.",
     {"repo": _REPO}, ["repo"]),

    (github_search_repos, "github_search_repos",
     "Searches public GitHub repositories.",
     {"query": (STRING, "What to search for"),
      "limit": (INTEGER, "How many results (default 10)")},
     ["query"]),

    (github_upload_file, "github_upload_file",
     "Uploads a file from this computer to a GitHub repository, creating or updating it. "
     "The file can be given by name alone; Muno finds it locally.",
     {"repo": _REPO,
      "file": (STRING, "Local file name or full path, e.g. 'MUNO.pdf'"),
      "dest_path": (STRING, "Path inside the repository (defaults to the file name)"),
      "message": (STRING, "Commit message"),
      "branch": (STRING, "Branch to commit to (defaults to the default branch)")},
     ["repo", "file"]),

    (github_read_file, "github_read_file",
     "Reads a text file stored in a GitHub repository.",
     {"repo": _REPO,
      "path": (STRING, "Path of the file inside the repository"),
      "branch": (STRING, "Branch to read from")},
     ["repo", "path"]),

    (github_list_files, "github_list_files",
     "Lists the files and folders in a GitHub repository.",
     {"repo": _REPO,
      "path": (STRING, "Folder inside the repository (blank for the root)"),
      "branch": (STRING, "Branch to list")},
     ["repo"]),

    (github_delete_file, "github_delete_file",
     "Deletes a file from a GitHub repository.",
     {"repo": _REPO,
      "path": (STRING, "Path of the file to delete"),
      "message": (STRING, "Commit message"),
      "branch": (STRING, "Branch to delete from")},
     ["repo", "path"]),

    (github_create_issue, "github_create_issue",
     "Opens a new issue on a repository.",
     {"repo": _REPO,
      "title": (STRING, "Issue title"),
      "body": (STRING, "Issue description")},
     ["repo", "title"]),

    (github_list_issues, "github_list_issues",
     "Lists issues on a repository.",
     {"repo": _REPO,
      "state": (STRING, "open, closed, or all (default open)"),
      "limit": (INTEGER, "How many to list")},
     ["repo"]),

    (github_comment_issue, "github_comment_issue",
     "Adds a comment to an issue or pull request.",
     {"repo": _REPO,
      "number": (INTEGER, "The issue or pull request number"),
      "body": (STRING, "The comment text")},
     ["repo", "number", "body"]),

    (github_close_issue, "github_close_issue",
     "Closes an issue.",
     {"repo": _REPO, "number": (INTEGER, "The issue number")},
     ["repo", "number"]),

    (github_list_branches, "github_list_branches",
     "Lists the branches in a repository.",
     {"repo": _REPO}, ["repo"]),

    (github_create_branch, "github_create_branch",
     "Creates a new branch in a repository.",
     {"repo": _REPO,
      "name": (STRING, "Name for the new branch"),
      "from_branch": (STRING, "Branch to start from (defaults to the default branch)")},
     ["repo", "name"]),

    (github_create_pull_request, "github_create_pull_request",
     "Opens a pull request.",
     {"repo": _REPO,
      "title": (STRING, "Pull request title"),
      "head": (STRING, "Branch containing the changes"),
      "base": (STRING, "Branch to merge into (defaults to the default branch)"),
      "body": (STRING, "Description of the changes")},
     ["repo", "title", "head"]),

    (github_list_pull_requests, "github_list_pull_requests",
     "Lists pull requests on a repository.",
     {"repo": _REPO, "state": (STRING, "open, closed, or all")},
     ["repo"]),

    (github_merge_pull_request, "github_merge_pull_request",
     "Merges a pull request.",
     {"repo": _REPO,
      "number": (INTEGER, "The pull request number"),
      "method": (STRING, "merge, squash, or rebase")},
     ["repo", "number"]),

    (github_list_commits, "github_list_commits",
     "Lists recent commits in a repository.",
     {"repo": _REPO, "limit": (INTEGER, "How many commits to list")},
     ["repo"]),

    (github_create_release, "github_create_release",
     "Publishes a release on a repository.",
     {"repo": _REPO,
      "tag": (STRING, "Tag name, e.g. v1.0.0"),
      "name": (STRING, "Release title"),
      "body": (STRING, "Release notes")},
     ["repo", "tag"]),

    (github_list_workflows, "github_list_workflows",
     "Lists the GitHub Actions workflows in a repository.",
     {"repo": _REPO}, ["repo"]),

    (github_run_workflow, "github_run_workflow",
     "Triggers a GitHub Actions workflow run.",
     {"repo": _REPO,
      "workflow": (STRING, "Workflow name or file name"),
      "ref": (STRING, "Branch to run on")},
     ["repo", "workflow"]),

    (github_list_workflow_runs, "github_list_workflow_runs",
     "Lists recent GitHub Actions runs and whether they passed.",
     {"repo": _REPO, "limit": (INTEGER, "How many runs to list")},
     ["repo"]),

    (find_file, "find_file",
     "Finds files on this computer by name. Use this when the user refers to a file "
     "and the exact path is unknown.",
     {"name": (STRING, "File name or part of it"),
      "limit": (INTEGER, "How many matches to show")},
     ["name"]),

    (refresh_file_index, "refresh_file_index",
     "Rebuilds Muno's catalogue of local files. Use if a new file cannot be found.",
     {}, []),

    (file_index_status, "file_index_status",
     "Reports how many local files Muno currently knows about.", {}, []),
]

TOOLS_SCHEMA = [_schema(name, description, properties, required)
                for _function, name, description, properties, required in _SPEC]

TOOL_FUNCTIONS = {name: function for function, name, _d, _p, _r in _SPEC}

# Actions that cost money, publish to others, or cannot be undone.
CONFIRMATION_REQUIRED = {
    "github_create_repo",
    "github_delete_repo",
    "github_update_repo",
    "github_upload_file",
    "github_delete_file",
    "github_create_issue",
    "github_comment_issue",
    "github_create_pull_request",
    "github_merge_pull_request",
    "github_create_release",
    "github_run_workflow",
}
