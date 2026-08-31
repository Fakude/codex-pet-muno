"""
Downloadable pet appearances from the Codex Pets gallery.

Muno's own look is drawn by hand in code. This module lets that be swapped for
one of the community sprite pets published at https://codex-pets.net, so the
companion can look like whatever the user wants without changing any behaviour.

How the sprites are laid out
----------------------------
Each pet is one WebP atlas cut into a grid of 192x208 cells. Every row is one
animation and every column is a frame of it. Rows carry a fixed meaning, and each
row only uses as many columns as that animation has frames:

    row 0  idle           6 frames
    row 1  running-right  8
    row 2  running-left   8
    row 3  waving         4
    row 4  jumping        5
    row 5  failed         8
    row 6  waiting        6
    row 7  running        6
    row 8  review         6

Some atlases have extra rows beyond these; they are ignored rather than guessed
at. A sheet with fewer rows simply offers fewer animations, and anything missing
falls back to idle.

Caching and attribution
-----------------------
Sheets are a couple of megabytes each, so a chosen pet is cached under
``~/.codex_pet/pets/`` and afterwards loads with no network at all. Every pet is
somebody's artwork, so the creator's name is stored with it and shown in the
picker and in settings.
"""
import json
import os
import threading
import urllib.error
import urllib.request

GALLERY_BASE = os.getenv("MUNO_PET_GALLERY", "https://codex-pets.net").rstrip("/")
CACHE_DIR = os.path.join(os.path.expanduser("~/.codex_pet"), "pets")

CELL_WIDTH = 192
CELL_HEIGHT = 208

REQUEST_TIMEOUT = 30
USER_AGENT = "Muno-Desktop-Companion"

# row -> (state id, frame count). Taken from the gallery's own viewer so the
# animations line up with what the artist saw when they uploaded.
SPRITE_STATES = {
    "idle": (0, 6),
    "running-right": (1, 8),
    "running-left": (2, 8),
    "waving": (3, 4),
    "jumping": (4, 5),
    "failed": (5, 8),
    "waiting": (6, 6),
    "running": (7, 6),
    "review": (8, 6),
}

# Muno's internal states mapped onto what the sprites actually provide. There is
# no dedicated talking animation, so waving stands in for speech: it reads as
# communicating and it moves, which is what matters at this size.
STATE_MAP = {
    "idle": "idle",
    "speaking": "waving",
    "thinking": "waiting",
    "excited": "jumping",
    "surprised": "jumping",
    "confused": "review",
    "sleeping": "idle",
    "error": "failed",
    # Being dragged across the screen. The atlases carry proper left and right
    # run cycles, so a dragged pet can actually face where it is going.
    "dragged-right": "running-right",
    "dragged-left": "running-left",
    "walking": "running",
}

# How long each frame of an animation should hold, in milliseconds. A single
# rate for everything makes idling look twitchy and running look sluggish, so
# each animation keeps roughly the pace it was drawn for.
STATE_FRAME_MS = {
    "idle": 170,
    "waiting": 150,
    "review": 150,
    "waving": 120,
    "jumping": 110,
    "failed": 130,
    "running": 90,
    "running-right": 80,
    "running-left": 80,
}
DEFAULT_FRAME_MS = 130

_catalogue_lock = threading.Lock()
_catalogue_cache = {"pets": None, "error": None}


def _request(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return response.read()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def fetch_catalogue(limit=60, force=False):
    """Return the list of available pets, newest first.

    Results are held for the session so browsing does not re-download.
    """
    with _catalogue_lock:
        if _catalogue_cache["pets"] is not None and not force:
            return list(_catalogue_cache["pets"])

    collected = []
    page = 1
    try:
        while len(collected) < limit:
            raw = _request(f"{GALLERY_BASE}/api/pets?page={page}")
            payload = json.loads(raw)
            batch = payload.get("pets") or []
            if not batch:
                break
            collected.extend(batch)
            if page >= int(payload.get("totalPages") or 1):
                break
            page += 1
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
        with _catalogue_lock:
            _catalogue_cache["error"] = str(exc)
        # Fall back to whatever is already downloaded so the picker still works.
        return installed_pets()

    summaries = [summarize(pet) for pet in collected[:limit]]
    with _catalogue_lock:
        _catalogue_cache["pets"] = summaries
        _catalogue_cache["error"] = None
    return list(summaries)


def summarize(pet):
    """Reduce an API record to the fields Muno needs."""
    report = pet.get("validationReport") or {}
    atlas = str(report.get("atlasSize") or "")
    rows = 0
    if "x" in atlas:
        try:
            rows = int(atlas.split("x")[1]) // CELL_HEIGHT
        except (ValueError, IndexError):
            rows = 0
    return {
        "id": str(pet.get("id") or ""),
        "name": str(pet.get("displayName") or pet.get("id") or "Unnamed"),
        "description": " ".join(str(pet.get("description") or "").split()),
        "kind": str(pet.get("kind") or "unknown"),
        "tags": list(pet.get("tags") or []),
        "owner": str(pet.get("ownerName") or pet.get("ownerHandle") or "unknown"),
        "spritesheet_url": str(pet.get("spritesheetUrl") or ""),
        "preview_url": str(pet.get("previewUrl") or pet.get("posterUrl") or ""),
        "rows": rows,
        "likes": int(pet.get("likeCount") or 0),
    }


def catalogue_error():
    with _catalogue_lock:
        return _catalogue_cache["error"]


# ---------------------------------------------------------------------------
# Local cache
# ---------------------------------------------------------------------------

def _pet_dir(pet_id):
    safe = "".join(c for c in str(pet_id) if c.isalnum() or c in "-_")
    return os.path.join(CACHE_DIR, safe)


def is_installed(pet_id):
    directory = _pet_dir(pet_id)
    return (
        os.path.isfile(os.path.join(directory, "spritesheet.webp"))
        and os.path.isfile(os.path.join(directory, "pet.json"))
    )


def installed_pets():
    """Pets already downloaded, usable with no network."""
    results = []
    if not os.path.isdir(CACHE_DIR):
        return results
    for entry in sorted(os.listdir(CACHE_DIR)):
        metadata_path = os.path.join(CACHE_DIR, entry, "pet.json")
        try:
            with open(metadata_path, "r", encoding="utf-8") as handle:
                results.append(json.load(handle))
        except (OSError, ValueError):
            continue
    return results


def install(pet):
    """Download a pet's sheet into the cache. Returns (ok, message)."""
    pet_id = pet.get("id")
    url = pet.get("spritesheet_url")
    if not pet_id or not url:
        return False, "That pet is missing its artwork."
    if is_installed(pet_id):
        return True, f"{pet.get('name', pet_id)} is already downloaded."

    directory = _pet_dir(pet_id)
    os.makedirs(directory, exist_ok=True)
    try:
        data = _request(url)
    except Exception as exc:
        return False, f"Couldn't download that pet: {str(exc)[:120]}"
    if len(data) < 1024:
        return False, "That pet's artwork looked empty."

    try:
        sheet_path = os.path.join(directory, "spritesheet.webp")
        with open(sheet_path, "wb") as handle:
            handle.write(data)
        with open(os.path.join(directory, "pet.json"), "w", encoding="utf-8") as handle:
            json.dump(pet, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        return False, f"Couldn't save that pet: {exc}"
    return True, f"Downloaded {pet.get('name', pet_id)}."


def load_metadata(pet_id):
    try:
        with open(os.path.join(_pet_dir(pet_id), "pet.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def spritesheet_path(pet_id):
    path = os.path.join(_pet_dir(pet_id), "spritesheet.webp")
    return path if os.path.isfile(path) else None


def remove(pet_id):
    """Delete a downloaded pet's files."""
    import shutil

    directory = _pet_dir(pet_id)
    if not os.path.isdir(directory):
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Sprite access
# ---------------------------------------------------------------------------

class SpritePet:
    """A loaded pet atlas, able to hand out individual animation frames."""

    def __init__(self, pet_id):
        self.pet_id = pet_id
        self.metadata = load_metadata(pet_id) or {"id": pet_id, "name": pet_id}
        self._sheet = None
        self._frames = {}
        self.rows = 0
        self.columns = 0
        self.loaded = False

    def load(self):
        """Read the atlas into memory. Returns True on success."""
        from PyQt6.QtGui import QPixmap

        path = spritesheet_path(self.pet_id)
        if not path:
            return False
        sheet = QPixmap(path)
        if sheet.isNull():
            return False
        self._sheet = sheet
        self.columns = max(1, sheet.width() // CELL_WIDTH)
        self.rows = max(1, sheet.height() // CELL_HEIGHT)
        self.loaded = True
        return True

    @property
    def name(self):
        return self.metadata.get("name", self.pet_id)

    @property
    def owner(self):
        return self.metadata.get("owner", "unknown")

    def available_states(self):
        """Sprite states this atlas actually contains."""
        return [
            state for state, (row, _frames) in SPRITE_STATES.items()
            if row < self.rows
        ]

    def resolve_state(self, muno_state):
        """Map one of Muno's states to a state this atlas has."""
        wanted = STATE_MAP.get(muno_state, "idle")
        row, _frames = SPRITE_STATES.get(wanted, SPRITE_STATES["idle"])
        if row >= self.rows:
            return "idle"
        return wanted

    def frame_count(self, muno_state):
        state = self.resolve_state(muno_state)
        _row, frames = SPRITE_STATES[state]
        return max(1, min(frames, self.columns))

    def frame_interval(self, muno_state, speed=0.0):
        """Milliseconds per frame, optionally quickened by movement speed.

        ``speed`` is pixels per millisecond of drag. A pet hauled quickly across
        the screen should visibly run harder than one nudged along.
        """
        state = self.resolve_state(muno_state)
        base = STATE_FRAME_MS.get(state, DEFAULT_FRAME_MS)
        if speed <= 0:
            return base
        # Halve the interval at roughly 1.5 px/ms, and never go below 45 ms.
        factor = max(0.45, 1.0 - min(speed / 1.5, 0.55))
        return max(45, int(base * factor))

    def frame(self, muno_state, index):
        """Return one frame as a QPixmap, cached after first use."""
        if not self.loaded and not self.load():
            return None
        state = self.resolve_state(muno_state)
        row, frames = SPRITE_STATES[state]
        usable = max(1, min(frames, self.columns))
        column = int(index) % usable

        key = (state, column)
        cached = self._frames.get(key)
        if cached is not None:
            return cached
        frame = self._sheet.copy(
            column * CELL_WIDTH, row * CELL_HEIGHT, CELL_WIDTH, CELL_HEIGHT
        )
        if frame.isNull():
            return None
        self._frames[key] = frame
        return frame


def load_pet(pet_id):
    """Load an installed pet, or None when it is unavailable."""
    if not pet_id or not is_installed(pet_id):
        return None
    pet = SpritePet(pet_id)
    return pet if pet.load() else None
