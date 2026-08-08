# Design Document

## Overview
This design stabilizes Muno around four boundaries: one process owns the desktop companion, one credential state gates Groq requests, one speech bubble owns visible responses, and one compact transparent window owns character presentation. Existing automation, capture, recording, voice, personality, and exit behavior remain additive dependencies rather than being replaced.

## Architecture
The application remains a PyQt6 desktop process with background workers. Startup first establishes single-instance ownership, then constructs the UI and workers. User input passes through local state handling and credential gating before AI dispatch. Worker results return as structured signals to a single UI presentation path.

Startup flow:
1. Create `QApplication`.
2. Ask `SingleInstanceGuard` to connect to the known local-server name.
3. If an owner responds, send `activate`, exit the new process, and make no API or microphone calls.
4. If no owner responds, remove only a stale endpoint, listen as owner, and construct `CodexPet`.
5. On confirmed shutdown, stop workers, close the server, and quit.

## Components and Interfaces

### SingleInstanceGuard
- Uses `QLocalServer` and `QLocalSocket` with a stable per-user server name.
- `acquire()` returns owner or secondary status.
- Secondary instances send an `activate` message and terminate.
- Owner handles `activate` by showing, raising, and focusing the existing Muno window.
- A failed connection permits stale endpoint cleanup before one listen retry.

### CredentialGate
- Represents `missing`, `ready`, and `invalid` states without retaining or displaying the key beyond client construction.
- Reads only `GROQ_API_KEY`; provider URLs and model IDs remain non-secret environment overrides.
- `can_request()` prevents worker creation in missing or invalid states.
- Authentication errors transition to `invalid` and publish one sanitized UI event.
- No logs include request headers, environment values, or credential fragments.

### AIWorker and interaction controller
- Keeps `json.loads`-only dictionary tool arguments and executes each requested side effect at most once.
- Emits structured response, tool-use, and classified error signals.
- The controller owns persistent sleep, mute, pending exit, credential state, and worker lifecycle.
- Authentication failure clears the active worker, re-enables input, marks the gate invalid, and presents one error bubble.

### CompactPetWindow
- Uses `WA_TranslucentBackground`, a transparent root widget, and no dark panel stylesheet.
- Uses an approximately 190 by 325 logical-pixel idle footprint and clamps startup movement to available screen geometry.
- Contains one `SpeechBubble`, one `AnimatedAvatar`, and the existing rounded `QLineEdit`.
- While a response is visible, adds the bubble's clamped height above the compact layout, preserves the bottom anchor, and restores compact height on dismissal.

### SpeechBubble
- Is one read-only `QTextEdit` configured for plain text, word wrapping, text selection, and internal vertical scrolling.
- `present(text, max_height)` sanitizes plain text, stops any prior animation, replaces rather than appends content, computes wrapped height, and animates from zero to the screen-clamped target.
- `dismiss()` stops animation, clears and hides the widget, and signals the window to restore its compact height.
- No response content is line-clamped or ellipsized: short and multiline replies grow the surface, while very long replies retain all text in a vertically scrollable viewport.
- All normal and error responses pass through this single component exactly once.

### AnimatedAvatar
- Retains the full-body vector character and scales from a fixed 280 by 330 design coordinate system into an approximately 140 by 160 viewport.
- A visual state machine maps idle, thinking, speaking, excited, confused, surprised, sleeping, and error to face, eye, mouth, body, and gesture behavior.
- Audio-backed speech runs until playback completion; text-only speech uses a bounded timer based on response length.

## Data Models

### Instance state
- `owner`: current process owns the local server.
- `secondary`: activation was forwarded and startup must end.

### Credential state
- `missing`: no environment credential; requests blocked.
- `ready`: credential is present and may be used.
- `invalid`: provider rejected authentication; requests blocked until restart or explicit reload.

### Companion state
- Independent flags: `is_muted`, `is_sleeping`, `pending_exit`, `shutting_down`.
- Visual state: one of idle, thinking, speaking, excited, confused, surprised, sleeping, or error.
- Active work references: AI worker, TTS worker, microphone worker, and visual speech timer.

## Correctness Properties

### Property 1: Unique UI ownership
**Validates: Requirements 1.1, 1.2, 1.3**

At most one process can own the Muno local-server name and display the companion UI.

### Property 2: Credential request gating
**Validates: Requirements 2.2, 2.3, 2.4**

For every application state, missing or invalid credential state implies zero new Groq requests.

### Property 3: Single response presentation
**Validates: Requirements 4.1, 4.3, 4.4**

For every logical response event, the UI produces at most one visible bubble presentation.

### Property 4: Complete screen-bounded response layout
**Validates: Requirements 3.2, 3.3, 4.2, 4.5, 4.6, 4.7**

For every response length, the bubble's plain text equals the complete sanitized response; its target height equals wrapped content height up to the configured and available-screen limits; overflow is vertically scrollable; and dismissal restores compact height.

### Property 5: Persistent sleep
**Validates: Requirements 5.3**

For every AI callback while sleeping, sleep remains true unless an explicit wake phrase was accepted.

### Property 6: Complete shutdown request
**Validates: Requirements 5.5**

For every application shutdown, termination is requested for each microphone, TTS, timer, and background worker.

## Error Handling
- Missing key: show one local setup message without starting `AIWorker`.
- HTTP 401 or provider invalid-key code: mark credentials invalid, show one actionable message, and suppress repeated requests.
- Timeout or connectivity failure: preserve ready credential state and permit a later retry.
- Stale local-server endpoint: remove it only after connection failure, then retry ownership once.
- Bubble animation interruption: stop the prior animation before changing text or height.
- Worker shutdown timeout: log only the worker category and continue bounded cleanup without secret data.

## Testing Strategy
- Unit-test credential-state classification and request gating with fake provider errors.
- Unit-test plain-text sanitization, replacement semantics, complete text retention, wrapped-height growth, overflow scrolling, and bubble dismissal.
- Integration-test two startup attempts: the second forwards activation and creates no second window.
- Integration-test stale local-server recovery after simulated abnormal termination.
- Off-screen Qt tests verify transparent attributes, 190 by 325 idle size, 140 by 160 avatar minimum, one bubble widget, upward growth for short and multiline replies, screen-clamped scrolling for very long replies, and compact restoration.
- State-transition tests cover sleep/wake, mute, exit yes/no, AI success/error, text-only speech, and clean shutdown.
- Regression-test current text and vision model defaults and safe tool argument handling.