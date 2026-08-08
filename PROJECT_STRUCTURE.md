# Project Structure - Phase 1 & Beyond

## Current Structure (Phase 1)
```
Pet/
├── pet.py                 # Main app (all-in-one for now)
├── SETUP.md              # Getting started guide
├── ROADMAP.md            # Development phases & vision
└── config.json           # Auto-created at ~/.codex_pet/
```

## Recommended Structure for Phase 2
When you're ready to scale, reorganize like this:

```
codex-pet/
├── README.md             # Main project overview
├── SETUP.md              # Installation & quick start
├── ROADMAP.md            # Feature roadmap
├── requirements.txt      # Python dependencies
├── .gitignore           # Git ignore rules
├── config.example.json  # Template for user config
│
├── src/                 # All source code
│   ├── __init__.py
│   ├── main.py          # Entry point (like current pet.py)
│   │
│   ├── ui/              # UI components
│   │   ├── __init__.py
│   │   ├── main_window.py
│   │   ├── settings_dialog.py
│   │   └── styles.py
│   │
│   ├── personality/     # Personality system
│   │   ├── __init__.py
│   │   ├── personalities.py  # Personality definitions
│   │   ├── manager.py        # Personality management
│   │   └── custom.py         # User-created personalities
│   │
│   ├── ai/              # AI/LLM logic
│   │   ├── __init__.py
│   │   ├── client.py         # API client wrapper
│   │   ├── models.py         # Model configurations
│   │   └── local_fallback.py # Offline LLM (Phase 3)
│   │
│   ├── core/            # Core utilities
│   │   ├── __init__.py
│   │   ├── config.py         # Config management
│   │   ├── logger.py         # Logging
│   │   └── constants.py      # App constants
│   │
│   ├── integrations/    # External app connectors (Phase 2)
│   │   ├── __init__.py
│   │   ├── clipboard.py      # Clipboard I/O
│   │   ├── file_handler.py   # File operations
│   │   ├── browser_ext.py    # Browser extension API
│   │   └── app_connectors/
│   │       ├── slack.py
│   │       ├── discord.py
│   │       └── vscode.py
│   │
│   ├── api/             # Local REST API (Phase 2)
│   │   ├── __init__.py
│   │   ├── server.py         # FastAPI server
│   │   └── endpoints.py      # API routes
│   │
│   └── storage/         # Data storage
│       ├── __init__.py
│       ├── local_db.py       # SQLite or JSON storage
│       └── cloud_sync.py     # Cloud backup (Phase 3)
│
├── tests/               # Test suite
│   ├── __init__.py
│   ├── test_personalities.py
│   ├── test_ai_client.py
│   └── test_integrations.py
│
├── build/               # Installer & distribution
│   ├── build.spec       # PyInstaller spec
│   ├── windows_installer.iss # InnoSetup script
│   └── mac_bundle.sh
│
├── docs/                # Documentation
│   ├── api.md
│   ├── architecture.md
│   ├── contributing.md
│   └── personality_guide.md
│
└── scripts/             # Utility scripts
    ├── install.sh       # Linux/Mac installation
    ├── build_exe.py     # Build Windows executable
    └── deploy.py        # Deployment script
```

## Phase 1 to Phase 2 Migration

When you're ready to scale from single `pet.py`:

1. **Create src/ folder**
2. **Split pet.py into modules**:
   - `ui/main_window.py` — UI code
   - `personality/personalities.py` — Personality definitions
   - `ai/client.py` — AI API wrapper
   - `core/config.py` — Config management
3. **Keep main.py** that imports and runs everything
4. **Update imports** in each module
5. **Add tests** as you go

## Key Principles for Scaling

### Separation of Concerns
- **UI** only handles display
- **AI** only handles API calls
- **Config** handles settings
- **Integrations** handle external connections

### Make It Modular
```python
# Bad: Everything in one file
# Good: Import what you need

from src.personality.manager import PersonalityManager
from src.ai.client import AIClient
from src.ui.main_window import MainWindow
```

### Testing
```python
# Each module should be testable independently

def test_personality_switching():
    pm = PersonalityManager()
    pm.set_personality("Writing Assistant")
    assert pm.current == "Writing Assistant"

def test_ai_client_connection():
    client = AIClient(api_key="test")
    response = client.query("test")
    assert response is not None
```

### Configuration
```python
# Keep secrets out of code
# Use environment variables or secure config

from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("GROQ_API_KEY")
```

## Building an Installer (Phase 1.5)

### Windows EXE with PyInstaller
```bash
pip install pyinstaller
pyinstaller --onefile --windowed pet.py
# Creates: dist/pet.exe
```

### Package Dependencies
```bash
pip freeze > requirements.txt
```

### Create Setup Wizard
Users can then:
1. Download `.exe`
2. Run installer
3. Automatically installs Python dependencies
4. Creates desktop shortcut

## Repository Setup (for GitHub)

```bash
git init
git add .
git commit -m "Initial Codex Pet Phase 1"
git remote add origin https://github.com/yourusername/codex-pet.git
git push -u origin main
```

**.gitignore** content:
```
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.venv
.DS_Store
*.exe
dist/
build/
*.egg-info/
.env
config.json
```

## Deployment Strategy

### Phase 1
- GitHub releases (source code)
- PyInstaller for Windows users

### Phase 2
- Web app: Vercel / Railway
- Desktop installer: GitHub releases
- Browser extension: Chrome Web Store

### Phase 3
- Mobile: TestFlight (iOS) + Google Play (Android)
- Cloud: AWS / GCP for sync server
- CI/CD: GitHub Actions for automated builds

---

**This structure scales with you. Start simple, expand as needed.**
