# Implementation Plan

## Overview
Implement the validated Muno reliability and compact UI design in dependency order while preserving existing interaction, automation, capture, recording, and voice behavior.

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1.1", "2.1"]},
    {"wave": 2, "tasks": ["1.2", "2.2"]},
    {"wave": 3, "tasks": ["3.1"]},
    {"wave": 4, "tasks": ["3.2"]},
    {"wave": 5, "tasks": ["4.1"]},
    {"wave": 6, "tasks": ["4.2", "4.3"]},
    {"wave": 7, "tasks": ["5.1"]},
    {"wave": 8, "tasks": ["5.2"]},
    {"wave": 9, "tasks": ["6.1"]},
    {"wave": 10, "tasks": ["6.2"]}
  ]
}
```

## Tasks

- [x] 1. Enforce a single running Muno instance
  - [x] 1.1 Implement the local single-instance guard
    - Add a stable per-user `QLocalServer` name and a `QLocalSocket` activation message.
    - Distinguish owner, active secondary, and stale endpoint outcomes.
    - Remove a server endpoint only after a failed connection and retry ownership once.
    - _Requirements: 1.1, 1.3_
  - [x] 1.2 Integrate activation and lock cleanup with application lifecycle
    - Run ownership negotiation before constructing `CodexPet`, microphone workers, or API clients.
    - Make secondary launches focus the existing Muno window and exit immediately.
    - Close the local server during confirmed shutdown.
    - _Requirements: 1.1, 1.2, 5.5_

- [x] 2. Gate Groq requests with secure credential state
  - [x] 2.1 Add credential state and lazy request gating
    - Represent missing, ready, and invalid credential states without persisting the key.
    - Prevent AI and transcription requests when credentials are missing or invalid.
    - Keep the required text and vision model defaults and environment overrides.
    - _Requirements: 2.1, 2.2, 2.5_
  - [x] 2.2 Classify authentication failures and suppress repeats
    - Convert provider 401 and invalid-key failures into one sanitized UI event.
    - Mark credentials invalid after rejection and block later requests until restart or reload.
    - Ensure logs never contain keys, headers, or credential fragments.
    - _Requirements: 2.3, 2.4, 4.4_

- [x] 3. Apply the compact transparent window design
  - [x] 3.1 Remove every opaque root-window background
    - Configure translucent window and root-widget attributes and transparent styles.
    - Preserve the rounded light text input and keyboard submission.
    - Set the default window to approximately 190 by 325 logical pixels.
    - _Requirements: 3.1, 3.2, 3.4_
  - [x] 3.2 Scale and constrain avatar and startup geometry
    - Keep the full-body vector character legible in an approximately 140 by 160 viewport.
    - Scale layout margins, spacing, and controls consistently.
    - Clamp bottom-right to center-right startup movement to available screen geometry.
    - _Requirements: 3.3, 3.5, 5.1_

- [x] 4. Make response presentation singular and dynamic
  - [x] 4.1 Consolidate all visible responses into one speech bubble
    - Reuse one `SpeechBubble` instance for normal responses, local state messages, and sanitized errors.
    - Stop prior animations and replace content instead of appending or creating another bubble.
    - Dismiss the previous bubble before entering the thinking state.
    - _Requirements: 4.1, 4.3, 4.4_
  - [x] 4.2 Add content-driven response sizing and overflow behavior
    - Compute wrapped content height and grow the outer window upward from its bottom anchor.
    - Clamp the expanded window to available-screen height and restore 190 by 325 idle size on dismissal.
    - Keep the full-body avatar and keyboard input usable at every response height.
    - _Requirements: 3.2, 3.3, 4.2, 4.7_
  - [x] 4.3 Preserve complete text in a scrollable response surface
    - Replace line clamping and ellipsis with one read-only plain-text response viewport.
    - Retain every sanitized character and use internal vertical scrolling for very long responses.
    - Replace prior content in the same widget so one logical response never creates a duplicate bubble.
    - _Requirements: 4.1, 4.4, 4.5, 4.6_

- [x] 5. Preserve expressive behavior and clean lifecycle transitions
  - [x] 5.1 Centralize character visual-state transitions
    - Map idle, thinking, speaking, excited, confused, surprised, sleeping, and error states to the compact avatar.
    - Preserve mouth, eye, breathing, and body gestures for audio and timed text-only speech.
    - Keep Muno as the visible identity for every specialty.
    - _Requirements: 5.1, 5.2, 5.4_
  - [x] 5.2 Reconcile interaction and shutdown state
    - Preserve mute, persistent sleep, explicit wake, and confirmed exit behavior.
    - Pause and resume continuous listening around AI and TTS activity.
    - Stop microphone, TTS, timers, local server, and background workers during shutdown.
    - Preserve capture, recording, and desktop automation paths.
    - _Requirements: 5.3, 5.5_

- [x] 6. Validate the integrated reliability and compact UI behavior
  - [x] 6.1 Run targeted static and off-screen validation
    - Run diagnostics and `python -m py_compile pet.py tools.py`.
    - Render compact avatar and bubble states with Qt's off-screen platform.
    - Confirm source contains no embedded key, deprecated vision model, unsafe `eval`, or invalid avatar text calls.
    - _Requirements: 2.1, 2.4, 2.5, 3.1, 3.2, 3.3, 4.1_
  - [x] 6.2 Perform the final interaction regression checklist
    - Verify a second launch activates the owner rather than creating another window.
    - Verify missing and invalid keys each produce one message and no repeated requests.
    - Verify long responses, Enter submission, mute, sleep/wake, exit yes/no, voice pause/resume, and clean shutdown.
    - Compare the result with the supplied screenshot and confirm the dark panel, stale window, generic error, and duplicate bubble are absent.
    - _Requirements: 1.1, 1.2, 1.3, 2.2, 2.3, 3.4, 3.5, 4.3, 4.4, 4.5, 5.3, 5.5_

## Notes
- Do not embed, print, or commit API keys.
- Preserve Muno's name and all existing specialties and tools.
- Treat the supplied screenshot as the before-state regression reference.
- Do not launch the long-running GUI during automated validation; use off-screen Qt checks and let the user perform the final visible launch.