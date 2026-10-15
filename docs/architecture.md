# Muno architecture

## Purpose

Muno is a Windows-first desktop companion. Its core design is **local-first, explicit approval for consequential actions, and auditable activity**.

## Components

```text
User (desktop UI, voice, or paired phone)
                |
                v
         pet.py — PyQt6 application shell
                |
    +-----------+------------+----------------+
    |           |            |                |
    v           v            v                v
Voice       Local state   Tool router      Visual UI
modules     & journal     and approvals    / pet system
    |           |            |
    |           |            +--> Browser / GitHub / Email / files
    |           |            +--> Screen and desktop automation
    |           |
    |           +--> ~/.codex_pet/ (configuration, journal, index, history)
    |
    +--> Windows speech services and configured model endpoint

Optional: token-paired phone PWA -> local-network mobile server -> approval bridge
```

## Trust boundaries

1. **Local desktop boundary.** Muno reads and controls only the current Windows computer. Files, journals, contact data and configuration remain local by default.
2. **External-service boundary.** Model requests, GitHub requests, browser sessions and email routes interact with third parties only when the relevant feature is configured.
3. **Action boundary.** A request to send, post, upload, create, merge, delete, type or control a desktop application must be approved by the user. Read-only actions may not need confirmation.
4. **Mobile boundary.** The optional phone interface is disabled by default, intended for a trusted LAN and protected by a pairing token. It is not an internet-facing service.

## Key modules

| Module | Responsibility |
| --- | --- |
| `pet.py` | Application composition, UI and request lifecycle |
| `tools.py` | Tool schemas, routing and approval-aware action dispatch |
| `github_api.py`, `email_api.py`, `browser.py` | External integration adapters |
| `file_index.py`, `journal.py`, `activity_log.py` | Local discovery and audit trail |
| `voice.py`, `voice_stream.py` | Windows speech output and streaming voice interaction |
| `mobile_server.py` | Optional token-paired phone companion |
| `watcher.py`, `region_picker.py` | Screen-region observation and selection |

## Development boundaries

The current implementation is Windows-specific in several places (Win32 APIs, SAPI and desktop automation). CI therefore runs on `windows-latest`. Cross-platform support is a future design effort, not a current claim.
