"""
DOM-level browser control for Muno.

Muno drives a real Chrome instance through the Chrome DevTools Protocol using
Playwright. Working with the page structure (links, buttons, text) is far more
reliable than screenshots and coordinate clicking: it does not depend on window
position, DPI scaling, or the vision model guessing pixel locations.

Design notes
------------
Dedicated profile
    Chrome ignores ``--remote-debugging-port`` when an instance is already
    running for the same profile: the new invocation just hands off to the
    existing process. Rather than closing the user's browser, Muno runs its own
    persistent profile. It coexists with normal Chrome, and because the profile
    is persistent, sites stay logged in after a one-time sign-in.

Connect per call
    Page state lives in Chrome, not in Playwright, so there is no need to hold a
    long-lived connection. Each action attaches over CDP, acts, and detaches.
    That keeps things thread-safe: Playwright's sync API objects are bound to the
    thread that created them, and Muno runs each turn on a fresh worker thread.
"""
import os
import socket
import subprocess
import time
import urllib.parse
from contextlib import contextmanager

CHROME_DEBUG_PORT = int(os.getenv("MUNO_CHROME_PORT", "9222"))

# Connect to the IPv4 loopback explicitly. "localhost" can resolve to ::1 while
# Chrome listens on 127.0.0.1, which surfaces as ECONNREFUSED.
CDP_ENDPOINT = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"

# By default Muno keeps its own Chrome profile, which coexists with normal
# browsing but starts signed out of everything.
#
# Pointing MUNO_CHROME_PROFILE at your real Chrome user data directory instead
# means every site you are already signed into works immediately, with no
# per-service setup. The catch is that Chrome ignores the debugging port when an
# instance is already running for that profile, so Chrome has to be closed once
# for Muno to attach.
DEFAULT_PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "Muno",
    "ChromeProfile",
)
PROFILE_DIR = (os.getenv("MUNO_CHROME_PROFILE") or "").strip() or DEFAULT_PROFILE_DIR
# Which profile inside that directory, e.g. "Default" or "Profile 1".
PROFILE_NAME = (os.getenv("MUNO_CHROME_PROFILE_NAME") or "").strip()


def real_chrome_profile_dir():
    """Return the user's normal Chrome user data directory, if it exists."""
    candidate = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
    )
    return candidate if os.path.isdir(candidate) else ""


def using_own_profile():
    return os.path.normcase(os.path.abspath(PROFILE_DIR)) == os.path.normcase(
        os.path.abspath(DEFAULT_PROFILE_DIR)
    )


def _chrome_processes_running():
    """True when any chrome.exe is currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "chrome.exe" in (result.stdout or "").lower()
    except Exception:
        return False

DEFAULT_TIMEOUT = 20000
MAX_TEXT_CHARS = 3000


def _resolve_chrome():
    """Locate chrome.exe across common install locations."""
    import shutil

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


def _port_is_open(port, host="127.0.0.1", timeout=0.4):
    """Return True when something is accepting connections on the port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_running():
    """Return True when Muno's controllable Chrome is reachable."""
    return _port_is_open(CHROME_DEBUG_PORT)


def start_browser(wait_seconds=25):
    """Ensure the controllable Chrome instance is running.

    Returns a human-readable status string.
    """
    if is_running():
        return "✅ The controllable browser is already running."

    chrome = _resolve_chrome()
    if not chrome:
        return "❌ Chrome was not found on this computer."

    # Attaching to the user's real profile only works if Chrome isn't already
    # running for it, because a second launch hands off to the existing process
    # and the debugging port is silently ignored.
    if not using_own_profile() and _chrome_processes_running():
        return (
            "❌ Chrome is already running with your normal profile, so I can't attach "
            "to it. Close Chrome completely and ask me again, or unset "
            "MUNO_CHROME_PROFILE to use my own browser instead."
        )

    os.makedirs(PROFILE_DIR, exist_ok=True)
    arguments = [
        chrome,
        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
    ]
    if PROFILE_NAME:
        arguments.append(f"--profile-directory={PROFILE_NAME}")
    try:
        subprocess.Popen(arguments)
    except Exception as e:
        return f"❌ Failed to start the browser: {e}"

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_running():
            return "✅ Started the controllable browser."
        time.sleep(0.3)
    return "❌ The browser did not become controllable in time."


@contextmanager
def _session(auto_start=True):
    """Attach to Chrome over CDP and yield a usable page.

    Detaches on exit without closing the user's browser.
    """
    if not is_running():
        if not auto_start:
            raise RuntimeError("The controllable browser is not running.")
        message = start_browser()
        if not message.startswith("✅"):
            raise RuntimeError(message)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Browser control needs Playwright. Install it with: pip install playwright"
        ) from e

    playwright = sync_playwright().start()
    browser = None
    try:
        browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = _usable_page(context)
        page.set_default_timeout(DEFAULT_TIMEOUT)
        yield page
    finally:
        # Only detach. Closing the browser object would end the user's session.
        try:
            playwright.stop()
        except Exception:
            pass


def _usable_page(context):
    """Return a real page, avoiding the blank startup tab.

    Navigating Chrome's initial ``about:blank`` tab can fail with ERR_ABORTED,
    so prefer a tab that already has a real document.
    """
    for page in context.pages:
        try:
            if page.url and not page.url.startswith(("about:", "chrome://")):
                return page
        except Exception:
            continue
    if context.pages:
        return context.pages[0]
    return context.new_page()


def _goto(page, url):
    """Navigate with a retry, tolerating the aborted-blank-tab case."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    except Exception:
        # A fresh tab reliably navigates when the current one refuses.
        page = page.context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    return page


def _summarize(page, limit=MAX_TEXT_CHARS):
    """Return the visible text of a page, trimmed for a language model."""
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit] + " …"
    return text


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def browser_status():
    """Report whether the controllable browser is available, and whose profile it uses."""
    which = (
        "my own browser profile, which starts signed out"
        if using_own_profile()
        else "your normal Chrome profile, so your existing logins apply"
    )
    if is_running():
        return f"✅ The controllable browser is running, using {which}."
    return (
        f"The browser isn't running yet; I'll start it when needed, using {which}."
    )


def browser_open(url):
    """Open a page in the controllable browser and report its title."""
    target = str(url or "").strip()
    if not target:
        return "❌ No address was provided."
    if "://" not in target:
        target = f"https://{target}"
    scheme = urllib.parse.urlparse(target).scheme.lower()
    if scheme not in {"http", "https"}:
        return f"❌ Only http and https addresses can be opened, not '{scheme}'."

    try:
        with _session() as page:
            page = _goto(page, target)
            page.bring_to_front()
            return f"✅ Opened {page.title() or target}"
    except Exception as e:
        return f"❌ Could not open that page: {e}"


def browser_read():
    """Read the visible text of the current page."""
    try:
        with _session() as page:
            title = page.title()
            body = _summarize(page)
            if not body:
                return f"The page '{title}' has no readable text yet."
            return f"Page: {title}\n\n{body}"
    except Exception as e:
        return f"❌ Could not read the page: {e}"


def browser_links(limit=15):
    """List the main links on the current page so a choice can be made by name."""
    try:
        count = max(1, min(int(limit or 15), 40))
    except (TypeError, ValueError):
        count = 15
    try:
        with _session() as page:
            items = page.eval_on_selector_all(
                "a[href]",
                """els => els
                    .map(e => ({ text: (e.innerText || '').trim(), href: e.href }))
                    .filter(x => x.text.length > 1 && x.href.startsWith('http'))
                """,
            )
            seen = set()
            lines = []
            for item in items:
                label = " ".join(item["text"].split())[:90]
                if label.lower() in seen:
                    continue
                seen.add(label.lower())
                lines.append(f"{len(lines) + 1}. {label}")
                if len(lines) >= count:
                    break
            if not lines:
                return "I don't see any links on this page."
            return "Links on this page:\n" + "\n".join(lines)
    except Exception as e:
        return f"❌ Could not list the links: {e}"


def browser_click(text):
    """Click a link or button by its visible text."""
    label = str(text or "").strip()
    if not label:
        return "❌ Nothing was specified to click."
    try:
        with _session() as page:
            page.bring_to_front()
            attempts = (
                lambda: page.get_by_role("link", name=label, exact=False).first,
                lambda: page.get_by_role("button", name=label, exact=False).first,
                lambda: page.get_by_text(label, exact=False).first,
            )
            for build in attempts:
                try:
                    target = build()
                    target.click(timeout=6000)
                    page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
                    return f"✅ Clicked '{label}'. Now on: {page.title()}"
                except Exception:
                    continue
            return f"❌ I couldn't find anything called '{label}' on this page."
    except Exception as e:
        return f"❌ Could not click that: {e}"


def browser_type(text, submit=True):
    """Type into the page's main input field, optionally submitting."""
    value = str(text or "")
    if not value:
        return "❌ No text was provided."
    try:
        with _session() as page:
            page.bring_to_front()
            selectors = (
                "input[type='search']",
                "textarea",
                "input[type='text']",
                "input:not([type='hidden'])",
                "[contenteditable='true']",
            )
            for selector in selectors:
                try:
                    field = page.locator(selector).first
                    field.click(timeout=4000)
                    field.fill(value, timeout=4000)
                    if submit:
                        field.press("Enter")
                        page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
                    return f"✅ Typed '{value[:60]}'" + (" and submitted." if submit else ".")
                except Exception:
                    continue
            return "❌ I couldn't find a text field on this page."
    except Exception as e:
        return f"❌ Could not type into the page: {e}"


import re as _re

# Boilerplate that appears on every results page and carries no answer.
_RESULT_NOISE = (
    "Accessibility links", "Skip to main content", "Accessibility help",
    "Accessibility feedback", "Filters and topics", "Sign in",
    "AI Mode All News Images Short videos Forums Videos More Tools",
    "Searching", "Thinking a little longer", "Feedback",
)


def clean_result_text(body):
    """Tidy scraped page text so the useful content is not buried.

    Answer boxes are rendered for the eye, not for reading aloud. A weather box
    in particular embeds its chart axis as a solid run of times like
    ``00:0003:0006:00...`` repeated for every day, which is pure noise: it costs
    tokens and buries the figures that actually answer the question.
    """
    text = " ".join(str(body or "").split())
    for noise in _RESULT_NOISE:
        text = text.replace(noise, " ")

    # Collapse runs of concatenated clock times from chart axes.
    text = _re.sub(r"(?:\d{1,2}:\d{2}){3,}", " ", text)
    # Collapse any other long digit runs left behind by charts.
    text = _re.sub(r"\b\d{8,}\b", " ", text)
    # Separate values that were visually adjacent but ran together, such as a
    # high and low temperature rendered as "24°11°".
    text = _re.sub(r"(\d+)°(\d+)°", r"\1° / \2°", text)
    return " ".join(text.split())


def search_and_read(query, limit=3000):
    """Search the web and return what the results page actually says.

    This exists because opening a search in a browser and reading a search
    result are two different things. ``web_search`` launches the user's normal
    browser, which Muno has no connection to, so it can announce that it looked
    something up but never tell the user the answer. Doing both here, inside the
    controllable browser, means a question like "what's the weather this week?"
    gets answered rather than merely googled.
    """
    text = str(query or "").strip()
    if not text:
        return "❌ No search terms were provided."

    search_url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(text)
    try:
        with _session() as page:
            page = _goto(page, search_url)
            # Google's answer box is the useful part and renders after the shell.
            for selector in ("#search", "#rso", "#main"):
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    break
                except Exception:
                    continue
            page.wait_for_timeout(600)

            body = _summarize(page, limit=int(limit))
            if not body:
                return f"I opened results for '{text}' but couldn't read the page."

            return f"Search results for '{text}':\n\n{clean_result_text(body)}"
    except Exception as e:
        return f"❌ Could not read the search results: {e}"


def read_page(url, limit=3000):
    """Open a specific page in the controllable browser and return its text."""
    target = str(url or "").strip()
    if not target:
        return "❌ No address was provided."
    if "://" not in target:
        target = f"https://{target}"
    if urllib.parse.urlparse(target).scheme.lower() not in {"http", "https"}:
        return "❌ Only http and https addresses can be read."
    try:
        with _session() as page:
            page = _goto(page, target)
            title = page.title()
            body = _summarize(page, limit=int(limit))
            if not body:
                return f"'{title}' has no readable text."
            return f"{title}\n\n{body}"
    except Exception as e:
        return f"❌ Could not read that page: {e}"


def youtube_play(query, index=1):
    """Search YouTube and open one of the results by position.

    This is the reliable version of "find a video and pick one": it reads the
    real result list instead of guessing where to click.
    """
    text = str(query or "").strip()
    if not text:
        return "❌ No search terms were provided."
    try:
        position = max(1, int(index or 1))
    except (TypeError, ValueError):
        position = 1

    search_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(text)
    try:
        with _session() as page:
            page = _goto(page, search_url)
            page.bring_to_front()
            try:
                page.wait_for_selector("a#video-title", timeout=DEFAULT_TIMEOUT)
            except Exception:
                pass

            videos = page.locator("a#video-title")
            found = videos.count()
            if not found:
                videos = page.locator("ytd-video-renderer a[href*='/watch']")
                found = videos.count()
            if not found:
                return f"❌ I couldn't read any results for '{text}'."

            if position > found:
                position = 1
            chosen = videos.nth(position - 1)
            title = (chosen.get_attribute("title") or chosen.inner_text() or "").strip()
            chosen.click(timeout=8000)
            page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
            label = " ".join(title.split())[:100] or page.title()
            return f"✅ Playing result {position} for '{text}': {label}"
    except Exception as e:
        return f"❌ Could not play that video: {e}"


def whatsapp_web_send(phone_digits, message, confirm_send=True):
    """Send a WhatsApp message through WhatsApp Web in the controllable browser.

    Requires a one-time QR sign-in in Muno's Chrome profile. When
    ``confirm_send`` is False the message is typed but left unsent.
    """
    digits = "".join(ch for ch in str(phone_digits or "") if ch.isdigit())
    body = str(message or "").strip()
    if not digits:
        return "❌ A phone number is required."
    if not body:
        return "❌ There is no message to send."

    url = (
        "https://web.whatsapp.com/send?phone="
        + digits
        + "&text="
        + urllib.parse.quote(body)
    )
    try:
        with _session() as page:
            page = _goto(page, url)
            page.bring_to_front()

            composer = None
            # Allow time for a first-run QR scan.
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                for selector in (
                    "div[contenteditable='true'][data-tab='10']",
                    "footer div[contenteditable='true']",
                    "div[contenteditable='true'][data-tab]",
                ):
                    try:
                        candidate = page.locator(selector).first
                        if candidate.count() and candidate.is_visible():
                            composer = candidate
                            break
                    except Exception:
                        continue
                if composer:
                    break
                time.sleep(1.0)

            if not composer:
                return (
                    "❌ WhatsApp Web isn't signed in yet. Scan the QR code in Muno's "
                    "browser window, then ask me again."
                )

            if not confirm_send:
                return "✅ The message is typed in WhatsApp. Press Enter to send it."

            composer.click(timeout=8000)
            composer.press("Enter")
            time.sleep(1.0)
            return f"✅ Sent your WhatsApp message to +{digits}."
    except Exception as e:
        return f"❌ Could not send the WhatsApp message: {e}"
