# Codex Pet - Development Roadmap

> **Current delivery status (October 2026):** Muno has progressed beyond the original Phase 1 plan below. The historical sections are retained for context; the table below is the authoritative portfolio status.

## Current status

| Capability | Status | Evidence |
| --- | --- | --- |
| Windows desktop companion and settings | Implemented | `pet.py`, `pets.py`, `pet_picker.py` |
| Voice interaction and local Windows narration | Implemented | `voice.py`, `voice_stream.py` |
| Browser, GitHub and email integrations | Implemented with approval gates | `browser.py`, `github_api.py`, `email_api.py` |
| Local file index, journal and activity history | Implemented | `file_index.py`, `journal.py`, `activity_log.py` |
| Token-paired local-network phone companion | Implemented; opt-in | `mobile_server.py` |
| Automated Windows unit-test CI | Implemented | `.github/workflows/ci.yml` |
| Broader integration/security test coverage | In progress | Add tests per integration boundary |
| Public cloud sync, internet-hosted control plane, native mobile app | Planned, not implemented | Future work |

## Vision
A futuristic, multi-purpose digital companion that adapts to any task and works across all devices with seamless integration to applications.

---

## Phase 1: Foundation (CURRENT)
**Goal**: Single-device desktop app with personality system

### ✅ Completed
- [x] Desktop UI with PyQt6
- [x] 5 personality types (Code, Writing, Life Coach, Research, Design)
- [x] Personality selection & settings dialog
- [x] Config file storage (~/.codex_pet/config.json)
- [x] User profile (name, preferred specialty)

### Next Steps
1. **Get API key working** — Add your free Groq API key
2. **Test all personalities** — Verify each mode responds correctly
3. **Add memory feature** — Store recent conversations per personality
4. **Create installer** — PyInstaller for standalone .exe

---

## Phase 2: Integration & Cross-Device (Estimated: 2-4 weeks)
**Goal**: Connect to other applications and support multiple devices

### Features to Build
- **Clipboard integration** — Read/write to system clipboard
- **Quick commands** — Buttons for common tasks (email draft, code review, etc.)
- **Context awareness** — Detect what app you're using and adapt
- **Local web server** — Let other apps talk to Codex Pet via API
- **Cloud sync** — Sync settings & preferences across devices
- **Browser extension** — Use Codex Pet in web apps

### Implementation Strategy
```
├── core/ (refactored AI logic)
├── personality/ (personality definitions)
├── integrations/
│   ├── clipboard.py
│   ├── file_handler.py
│   ├── browser_extension/ (Manifest v3)
│   └── app_connectors/ (for Slack, Discord, etc.)
├── api/ (local REST server)
└── sync/ (cloud backup & cross-device)
```

---

## Phase 3: Multi-Device & Advanced Features (Estimated: 4-8 weeks)
**Goal**: Available on all platforms with intelligent adaptation

### Platforms to Support
1. **Desktop** ✓ (Windows/Mac/Linux)
2. **Web PWA** (Progressive Web App)
3. **Mobile** (iOS/Android via browser + lightweight app)
4. **Embedded** (Raspberry Pi, smart devices)

### Advanced Features
- **Smart notifications** — Alerts based on context
- **Voice I/O** — Speak to pet, listen to responses
- **Vision mode** — Analyze screenshots for design/code reviews
- **Multi-persona mode** — Tag tasks with personality, auto-switch
- **Offline mode** — Local LLM fallback
- **Custom personalities** — Users create their own specialty modes
- **Learning & adaptation** — Pet learns user preferences over time

### Tech Stack Options
```
Desktop:  PyQt6 / Electron
Web:      React + TypeScript + Vite
Mobile:   React Native / Flutter wrapper
Server:   FastAPI / Node.js (sync, notifications, auth)
AI:       Groq API + Local LLM (ollama) fallback
```

---

## Budget Strategy (Low-Cost)

### Free/Low-Cost Services
- **AI**: Groq (free tier, excellent speed)
- **Hosting**: Replit/Railway (free tier)
- **Database**: Firebase (free tier) or SQLite local
- **Deployment**: GitHub Actions for CI/CD
- **Storage**: GitHub + personal cloud

### Estimated Costs Year 1
- **Groq API**: $0-50/month (if usage grows)
- **Cloud storage**: $0 (free tier)
- **Hosting**: $0-20/month
- **Domain**: $0-12/year (optional)
- **Total**: ~$60-200/year = Very budget-friendly

---

## Quick Setup Now

### Prerequisites
```bash
pip install pyqt6 openai
```

### Run
1. Add your free Groq API key to `pet.py`
2. Get key from: https://console.groq.com
3. Run: `python pet.py`

### Test Each Personality
- Click ⚙️ Settings
- Pick each specialty
- Ask it something relevant to that mode

---

## Personality Modes Explained

| Mode | Best For | Example Tasks |
|------|----------|---|
| **Code Expert** | Programming | Debug code, explain logic, suggest refactors |
| **Writing Assistant** | Content | Blog posts, emails, creative writing |
| **Life Coach** | Productivity | Goal setting, motivation, habit tracking |
| **Research Partner** | Learning | Deep research, critical thinking, Q&A |
| **Design Advisor** | Visual | UI/UX feedback, color theory, layout ideas |

---

## Phase 1.5: Quality & Deployment (Next Week)

- [ ] Add error handling & retry logic
- [ ] Store conversation history per personality
- [ ] Create Windows installer (.exe)
- [ ] Write user guide
- [ ] Get feedback from 3-5 beta testers
- [ ] Push to GitHub for open-source sharing

---

## Questions for You

1. **Which personality matters most first?** Focus there for Phase 1.5
2. **Which device do you use most?** Optimize for that in Phase 2
3. **Want to sell this?** Plan monetization (freemium, Pro tier, custom personalities)
4. **Time available?** Adjust roadmap accordingly

---

## Success Metrics

- ✅ Phase 1: App runs, all personalities work, settings persist
- ✅ Phase 2: Can integrate with 3 external apps, works on 2 devices
- ✅ Phase 3: Full cross-device sync, 100+ users, sustainable tech stack

---

*This roadmap is flexible. Adjust based on feedback, available time, and priorities.*
