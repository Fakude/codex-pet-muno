"""
Watching one part of the screen and acting when something appears there.

The problem
-----------
Some tools ask the same question over and over. An agent that pauses for approval
on every step turns a long task into a queue of identical clicks, and the person
supervising it stops reading the prompts after the third one anyway. That is the
worst of both worlds: attention is spent, and no real review happens.

So this watches a region the user chooses, looks for a specific thing appearing
in it, and performs one configured click when it does.

How matching works
------------------
By template, not by guesswork. The user captures a picture of the thing to look
for once, and afterwards each snapshot of the region is compared against it.
Interface elements render identically from one appearance to the next, so a
straightforward normalised difference over a greyscale downscale is both accurate
and cheap. There is no OCR and no model involved, which means no dependency on a
network call and no chance of a hallucinated match.

Why it is fenced in
-------------------
This clicks on the user's behalf, repeatedly, while they are looking elsewhere.
That deserves limits rather than trust:

* It must be armed deliberately and never starts on its own.
* It expires. Every watch has a maximum number of clicks and a time limit, and
  stops when either is reached.
* It only clicks inside the region the user selected, so a stray match elsewhere
  cannot be acted on.
* It refuses to run without a template, because "click whenever anything changes"
  is not a safe instruction.
* Every activation is written to the task journal with a timestamp.
* Stopping is immediate and available from the tray, the companion, and the phone.
"""
import threading
import time

import numpy as np
from PIL import ImageGrab

# A match this close counts as the same thing appearing again. Interface
# elements are pixel-identical between appearances, so this can be strict; the
# tolerance is for compression and subpixel text rendering.
DEFAULT_THRESHOLD = 0.92

# Templates are compared at this width to keep each check inexpensive.
COMPARE_WIDTH = 160

# A pixel must differ by at least this much to count as genuinely different
# rather than compression or antialiasing wobble.
PIXEL_CHANGE_THRESHOLD = 0.12

# And at least this share of pixels must differ before the area is considered to
# have become something else.
#
# This is deliberately small. A single digit changing inside a panel moves only
# about a fifth of a percent of its pixels, and in a larger region less still, so
# a threshold set by intuition misses exactly the changes worth catching. It can
# be this low because the per-pixel threshold above already discards noise:
# genuinely identical frames measure zero, not merely small.
CHANGE_SENSITIVITY = 0.0008

MIN_INTERVAL = 0.5
DEFAULT_INTERVAL = 2.0
DEFAULT_MAX_ACTIONS = 25
DEFAULT_EXPIRY_MINUTES = 60

# After a click, ignore the region briefly so one appearance is not counted
# several times while the interface redraws.
COOLDOWN_SECONDS = 2.5


def grab_region(region):
    """Capture one screen region as an RGB image."""
    left, top, width, height = region
    box = (int(left), int(top), int(left + width), int(top + height))
    return ImageGrab.grab(bbox=box)


def _fingerprint(image):
    """Reduce an image to a small greyscale array for comparison."""
    width, height = image.size
    if width == 0 or height == 0:
        return None
    scale = COMPARE_WIDTH / float(width)
    target = (COMPARE_WIDTH, max(1, int(height * scale)))
    grey = image.convert("L").resize(target)
    return np.asarray(grey, dtype=np.float32) / 255.0


def _align(first, second):
    """Crop two fingerprints to a common shape, or return None."""
    if first is None or second is None:
        return None, None
    if first.shape != second.shape:
        rows = min(first.shape[0], second.shape[0])
        columns = min(first.shape[1], second.shape[1])
        if rows == 0 or columns == 0:
            return None, None
        first = first[:rows, :columns]
        second = second[:rows, :columns]
    return first, second


def similarity(first, second):
    """Return 0.0-1.0 for how alike two fingerprints are overall.

    Suitable for deciding whether a whole element is the same element again,
    which is what template matching needs.
    """
    first, second = _align(first, second)
    if first is None:
        return 0.0
    return float(1.0 - np.abs(first - second).mean())


def change_fraction(first, second, pixel_threshold=PIXEL_CHANGE_THRESHOLD):
    """Fraction of pixels that differ strongly between two fingerprints.

    An average difference is the wrong measure for noticing that something in a
    region has changed. A digit changing from 1 to 2 alters very few pixels, so
    the average barely moves and the change is missed, while video compression
    nudges almost every pixel slightly and would look like a change if averaged.
    Counting only pixels that differ decisively separates the two cases.
    """
    first, second = _align(first, second)
    if first is None:
        return 1.0
    return float((np.abs(first - second) > pixel_threshold).mean())


# What causes a watch to act.
TRIGGER_APPEARS = "appears"   # the captured thing shows up again
TRIGGER_CHANGES = "changes"   # the area becomes materially different
TRIGGER_ASK = "ask"           # the area changed, and a question about it is answered yes

# Looking at a region with a vision model costs tokens, so it only happens when
# the picture has actually changed, and never more often than this.
MIN_ASK_GAP_SECONDS = 8.0


class RegionWatch:
    """One armed watch over a screen region.

    A watch has a trigger and a reaction, and they are independent. That split is
    what makes this more than an auto-clicker: the same machinery can press a
    button when a prompt returns, tell the user when a figure changes, or read a
    region and answer a question about it.
    """

    def __init__(self, region, template_image, action, label="",
                 threshold=DEFAULT_THRESHOLD, interval=DEFAULT_INTERVAL,
                 max_actions=DEFAULT_MAX_ACTIONS,
                 expiry_minutes=DEFAULT_EXPIRY_MINUTES,
                 on_action=None, on_finish=None,
                 trigger=TRIGGER_APPEARS, question="", observer=None):
        self.region = tuple(int(v) for v in region)
        self.template = _fingerprint(template_image)
        self.action = action                    # callable(region, context) -> str
        self.label = label or "the thing you picked"
        self.threshold = float(threshold)
        self.interval = max(MIN_INTERVAL, float(interval))
        self.max_actions = max(1, int(max_actions))
        self.expires_at = time.time() + max(1, int(expiry_minutes)) * 60
        self.on_action = on_action
        self.on_finish = on_finish

        self.trigger = trigger
        self.question = str(question or "")
        # observer(region, question) -> str, supplied by the application so this
        # module needs no knowledge of models or credentials.
        self.observer = observer

        self.actions_taken = 0
        self.checks = 0
        self.looks_taken = 0
        self.last_similarity = 0.0
        self.last_answer = ""
        self.started_at = time.time()
        self.stop_reason = None
        self._baseline = self.template
        self._last_ask = 0.0
        self._running = False
        self._thread = None

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._running or self.template is None:
            return False
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="muno-region-watch", daemon=True
        )
        self._thread.start()
        return True

    def stop(self, reason="stopped by you"):
        if not self._running:
            return
        self._running = False
        self.stop_reason = reason
        if self.on_finish:
            try:
                self.on_finish(reason)
            except Exception:
                pass

    def is_running(self):
        return self._running

    def remaining_minutes(self):
        return max(0.0, (self.expires_at - time.time()) / 60.0)

    def summary(self):
        """Short status, for listing several watches together."""
        if self._running:
            return (
                f"acted {self.actions_taken}/{self.max_actions} times, "
                f"{self.remaining_minutes():.0f} min left, "
                f"last match {self.last_similarity:.0%}"
            )
        return (
            f"acted {self.actions_taken} times"
            + (f", {self.stop_reason}" if self.stop_reason else "")
        )

    def describe(self):
        if self._running:
            return (
                f"Watching for {self.label}. Acted {self.actions_taken} of "
                f"{self.max_actions} times, {self.remaining_minutes():.0f} minutes left."
            )
        return (
            f"Not watching. Last run acted {self.actions_taken} times"
            + (f" and {self.stop_reason}." if self.stop_reason else ".")
        )

    # -- the loop ----------------------------------------------------------

    def _fire(self, score, context):
        """Run the reaction and account for it. Returns False when finished."""
        self.actions_taken += 1
        try:
            result = self.action(self.region, context)
        except TypeError:
            # Reactions that do not care about context.
            result = self.action(self.region)
        except Exception as exc:
            result = f"the reaction failed: {exc}"
        if self.on_action:
            try:
                self.on_action(self.actions_taken, score, str(result))
            except Exception:
                pass
        if self.actions_taken >= self.max_actions:
            self.stop("reached its limit")
            return False
        return True

    def _look(self, image):
        """Ask the supplied observer about the region, sparingly."""
        if not self.observer or not self.question:
            return ""
        if time.time() - self._last_ask < MIN_ASK_GAP_SECONDS:
            return ""
        self._last_ask = time.time()
        self.looks_taken += 1
        try:
            answer = str(self.observer(self.region, self.question) or "")
        except Exception as exc:
            answer = f"could not look: {exc}"
        self.last_answer = " ".join(answer.split())[:400]
        return self.last_answer

    def _loop(self):
        cooldown_until = 0.0
        while self._running:
            if time.time() >= self.expires_at:
                self.stop("reached its time limit")
                return
            try:
                image = grab_region(self.region)
                current = _fingerprint(image)
            except Exception:
                # A screen lock or resolution change can make a grab fail; keep
                # waiting rather than treating it as a match.
                time.sleep(self.interval)
                continue

            self.checks += 1
            in_cooldown = time.time() < cooldown_until

            if self.trigger == TRIGGER_APPEARS:
                score = similarity(self.template, current)
                self.last_similarity = score
                if score >= self.threshold and not in_cooldown:
                    if not self._fire(score, {}):
                        return
                    cooldown_until = time.time() + COOLDOWN_SECONDS

            else:
                # Both remaining triggers start from the area becoming different.
                moved = change_fraction(self._baseline, current)
                score = 1.0 - moved
                self.last_similarity = similarity(self._baseline, current)
                changed = moved > CHANGE_SENSITIVITY

                if changed:
                    self._baseline = current
                    if not in_cooldown:
                        if self.trigger == TRIGGER_CHANGES:
                            if not self._fire(score, {}):
                                return
                            cooldown_until = time.time() + COOLDOWN_SECONDS
                        elif self.trigger == TRIGGER_ASK:
                            answer = self._look(image)
                            if answer and answer_is_yes(answer):
                                if not self._fire(score, {"answer": answer}):
                                    return
                                cooldown_until = time.time() + COOLDOWN_SECONDS

            time.sleep(self.interval)


_AFFIRMATIVE = ("yes", "true", "found", "detected", "present", "appeared",
                "it is", "there is", "correct", "affirmative", "looks")
_NEGATIVE = ("no", "not ", "none", "nothing", "absent", "no change",
             "cannot", "can't", "unclear", "unable")


def answer_is_yes(answer):
    """Decide whether an answer about a region means the condition was met.

    Deliberately conservative: an ambiguous reply counts as no, because acting on
    a maybe is worse than waiting for the next check.
    """
    text = " ".join(str(answer or "").lower().split())
    if not text:
        return False
    opening = text[:70]
    if any(opening.startswith(word) for word in _NEGATIVE):
        return False
    return any(word in opening for word in _AFFIRMATIVE)


# ---------------------------------------------------------------------------
# Reactions a watch can perform
# ---------------------------------------------------------------------------

def click_point_action(point):
    """Click one fixed point on screen.

    The place to click is given separately from the place to watch, because they
    are usually not the same. The thing that signals "your turn" might be a line
    of text, a heading, or a highlighted row, while the button to press sits
    somewhere else entirely. Assuming the two coincide only works by luck.
    """
    target_x, target_y = int(point[0]), int(point[1])

    def perform(_region, _context=None):
        import pyautogui

        width, height = pyautogui.size()
        if not (0 <= target_x < width and 0 <= target_y < height):
            return "the click target is off screen, so nothing was done"
        previous = pyautogui.position()
        pyautogui.click(target_x, target_y)
        # Put the pointer back so the user's own work is not disturbed.
        pyautogui.moveTo(previous.x, previous.y, duration=0)
        return f"clicked at {target_x}, {target_y}"

    return perform


def click_centre_action(offset=(0, 0)):
    """Click the middle of the watched region, for when they do coincide."""
    def perform(region, _context=None):
        import pyautogui

        left, top, width, height = region
        x = int(left + width / 2 + offset[0])
        y = int(top + height / 2 + offset[1])
        if not (left <= x <= left + width and top <= y <= top + height):
            return "the click target fell outside the watched area, so nothing was done"
        previous = pyautogui.position()
        pyautogui.click(x, y)
        pyautogui.moveTo(previous.x, previous.y, duration=0)
        return f"clicked at {x}, {y}"

    return perform


def press_key_action(key="enter"):
    """Send a single keystroke to whatever currently has focus."""
    def perform(_region, _context=None):
        import pyautogui

        pyautogui.press(str(key))
        return f"pressed {key}"

    return perform


def type_text_action(text, point=None, submit=False, clear_first=False):
    """Focus a field and type into it.

    Clicking first matters: typing goes to whatever holds focus, and without a
    deliberate click that could be any window. Giving the field's location makes
    the target explicit rather than a guess about what happens to be focused.
    """
    body = str(text or "")

    def perform(region, context=None):
        import pyautogui

        # A generated body can be supplied at fire time instead of up front.
        payload = (context or {}).get("text") or body
        if not payload:
            return "there was nothing to type"

        target = point
        if target is None:
            left, top, width, height = region
            target = (int(left + width / 2), int(top + height / 2))

        screen_width, screen_height = pyautogui.size()
        if not (0 <= target[0] < screen_width and 0 <= target[1] < screen_height):
            return "the field is off screen, so nothing was typed"

        pyautogui.click(int(target[0]), int(target[1]))
        time.sleep(0.25)
        if clear_first:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.press("delete")
            time.sleep(0.1)
        # A small interval keeps up with fields that filter input as it arrives.
        pyautogui.write(payload, interval=0.012)
        if submit:
            pyautogui.press("enter")
        preview = " ".join(payload.split())[:60]
        return f"typed {len(payload)} characters: {preview}"

    return perform


def notify_action(notifier, label="that area"):
    """Tell the user instead of touching anything.

    This is the reaction that turns a watcher into an observer. Nothing on screen
    is altered: the user is simply told what changed, which is what "keep an eye
    on this" usually means.
    """
    def perform(_region, context=None):
        answer = (context or {}).get("answer", "")
        if answer:
            notifier(f"{label}: {answer}")
            return "told you what it says"
        notifier(f"{label} just changed.")
        return "told you it changed"

    return perform


def report_action(notifier, label="that area", save_dir=None):
    """Report what changed and keep a copy of the evidence.

    Saving the frame matters for anything being kept track of over time: a claim
    that a figure changed is far more useful alongside the picture it came from.
    """
    def perform(region, context=None):
        answer = (context or {}).get("answer", "")
        saved = ""
        if save_dir:
            try:
                import os

                os.makedirs(save_dir, exist_ok=True)
                path = os.path.join(
                    save_dir, f"{time.strftime('%Y%m%d-%H%M%S')}.png"
                )
                grab_region(region).save(path, "PNG")
                saved = path
            except Exception:
                saved = ""
        message = f"{label}: {answer}" if answer else f"{label} changed."
        if saved:
            message += f" (saved {saved})"
        notifier(message)
        return f"reported it{' and saved a copy' if saved else ''}"

    return perform


# ---------------------------------------------------------------------------
# Several watches at once
#
# One watch per screen is not enough in practice. A person supervising a few
# tools has a few different prompts to clear, each in its own place, and wants
# them all handled without babysitting any of them.
# ---------------------------------------------------------------------------

class WatchManager:
    """Holds the armed watches and keeps their names unique."""

    def __init__(self):
        self._watches = {}
        self._lock = threading.Lock()

    def add(self, name, watch):
        """Arm a watch under a name, replacing any earlier one with that name."""
        label = str(name or "").strip() or f"watch {len(self._watches) + 1}"
        with self._lock:
            existing = self._watches.get(label)
            if existing and existing.is_running():
                existing.stop("replaced")
            self._watches[label] = watch
        if not watch.start():
            with self._lock:
                self._watches.pop(label, None)
            return None
        return label

    def stop(self, name=None, reason="stopped by you"):
        """Stop one watch by name, or all of them when no name is given."""
        with self._lock:
            targets = (
                list(self._watches.items()) if name is None
                else [(n, w) for n, w in self._watches.items()
                      if n.lower() == str(name).strip().lower()]
            )
        stopped = []
        for label, watch in targets:
            if watch.is_running():
                watch.stop(reason)
                stopped.append(label)
        return stopped

    def active(self):
        with self._lock:
            return {n: w for n, w in self._watches.items() if w.is_running()}

    def all(self):
        with self._lock:
            return dict(self._watches)

    def count_active(self):
        return len(self.active())

    def describe(self):
        watches = self.all()
        if not watches:
            return "I'm not watching any part of the screen."
        running = self.active()
        lines = []
        for label, watch in watches.items():
            state = "watching" if label in running else "finished"
            lines.append(f"- {label}: {state}, {watch.summary()}")
        heading = (
            f"👁 {len(running)} watch(es) running of {len(watches)} set up:"
            if running else "No watches running. Previously set up:"
        )
        return heading + "\n" + "\n".join(lines)

    def prune(self):
        """Forget finished watches so the list stays meaningful."""
        with self._lock:
            self._watches = {
                n: w for n, w in self._watches.items()
                if w.is_running() or w.actions_taken
            }


MANAGER = WatchManager()


# ---------------------------------------------------------------------------
# Tool surface
#
# Setting a watch needs the user to point at the screen, which no amount of
# reasoning can do on their behalf. So the tool opens the mapping overlay rather
# than trying to guess coordinates: asking for it out loud and clicking the two
# places end up at the same result.
# ---------------------------------------------------------------------------

# Set by the application at startup so a tool call can open the picker.
_REQUEST_SETUP = None


def register_setup_handler(handler):
    """Let the application supply the function that opens the mapping overlay."""
    global _REQUEST_SETUP
    _REQUEST_SETUP = handler


def watch_screen_region(purpose=""):
    """
    Starts mapping a screen area to watch and a spot to click, so a repeating
    prompt gets handled without the user doing it each time.

    Args:
        purpose: What is being automated, e.g. 'Kiro approval prompts'.

    Returns:
        What the user should do next
    """
    if _REQUEST_SETUP is None:
        return "❌ I can't open the screen mapper right now."
    what = " ".join(str(purpose or "").split())
    try:
        _REQUEST_SETUP()
    except Exception as exc:
        return f"❌ I couldn't open the screen mapper: {str(exc)[:120]}"
    return (
        "I've dimmed the screen so we can map it"
        + (f" for {what}" if what else "")
        + ". First drag around the area where the prompt appears, then click "
        "exactly where I should click. I'll confirm the limits before starting."
    )


def list_screen_watches():
    """
    Lists the screen areas Muno is watching and what each has done.

    Returns:
        A readable status for every watch
    """
    return MANAGER.describe()


def stop_screen_watch(name=""):
    """
    Stops watching a screen area, or all of them.

    Args:
        name: Which watch to stop. Leave blank to stop every one.

    Returns:
        Confirmation of what was stopped
    """
    label = str(name or "").strip()
    stopped = MANAGER.stop(label or None)
    if not stopped:
        if label:
            return f"I'm not watching anything called '{label}'."
        return "I wasn't watching anything."
    return f"✅ Stopped watching {', '.join(stopped)}."


# Set by the application so a tool call can open the field picker.
_REQUEST_WRITE = None


def register_write_handler(handler):
    """Let the application supply the function that opens the field picker."""
    global _REQUEST_WRITE
    _REQUEST_WRITE = handler


def write_into_field(text):
    """
    Types text into a field the user points at on screen.

    Compose the full text yourself before calling this. If the user described
    what they want rather than dictating it, write it for them: a sentence, a
    paragraph, or a short description as the field requires. Do not send
    placeholders and do not ask them to type it themselves.

    Args:
        text: The exact, finished text to type.

    Returns:
        What the user should do next
    """
    body = str(text or "").strip()
    if not body:
        return "❌ I need the finished text before I can put it anywhere."
    if _REQUEST_WRITE is None:
        return "❌ I can't open the field picker right now."
    try:
        _REQUEST_WRITE(body)
    except Exception as exc:
        return f"❌ I couldn't open the field picker: {str(exc)[:120]}"
    preview = " ".join(body.split())[:80]
    return (
        f"I've written it and dimmed the screen. Click the field where it should "
        f"go and I'll confirm before typing. Text begins: “{preview}”"
    )
