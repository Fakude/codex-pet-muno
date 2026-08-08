# Requirements Document

## Introduction
This feature makes Muno a reliable single-instance desktop companion with secure Groq configuration, a compact transparent presentation, and clear non-duplicated interaction feedback. It preserves Muno's identity, personalities, automation, capture, recording, voice, and animated character behavior.

## Glossary
- **Muno**: The visible desktop companion application and character.
- **Groq credential**: The secret API key supplied through the `GROQ_API_KEY` environment variable.
- **Stale instance**: An older Muno process or window that remains active after another instance starts.
- **Logical pixel**: A display-independent UI measurement used by Qt.

## Requirements

### Requirement 1: Single running instance
**User Story:** As a user, I want only one Muno window running so that stale interfaces cannot overlap.

#### Acceptance Criteria
1. WHEN Muno starts while another Muno instance is active, THE SYSTEM SHALL focus the existing instance and terminate the new startup attempt.
2. WHEN Muno exits after confirmation, THE SYSTEM SHALL release its single-instance lock.
3. IF a previous process ended unexpectedly, THEN THE SYSTEM SHALL allow the next valid instance to start.

### Requirement 2: Secure Groq configuration
**User Story:** As a user, I want Muno to use environment-based credentials safely so that secrets are never embedded in project files or displayed.

#### Acceptance Criteria
1. THE SYSTEM SHALL read the Groq credential from `GROQ_API_KEY` without storing it in source code, configuration files, logs, or user-facing text.
2. IF the credential is missing, THEN Muno SHALL show one concise setup message and SHALL NOT submit an API request.
3. IF Groq rejects the credential, THEN Muno SHALL show one actionable invalid-key message and SHALL block further requests until restart or credential reload.
4. THE SYSTEM SHALL log no credential value or authorization header.
5. THE SYSTEM SHALL use `llama-3.1-8b-instant` for text and `meta-llama/llama-4-scout-17b-16e-instruct` for vision unless explicitly overridden.

### Requirement 3: Compact transparent presentation
**User Story:** As a user, I want Muno to occupy half the previous footprint without a rectangular background.

#### Acceptance Criteria
1. THE Muno window SHALL use a transparent background with no visible dark rectangular panel.
2. WHEN no response is visible, THE default total window footprint SHALL be approximately 190 by 325 logical pixels.
3. THE full-body character SHALL remain legible within approximately 140 by 160 logical pixels.
4. THE text input SHALL retain its current rounded light appearance and remain keyboard accessible.
5. THE window SHALL begin near the bottom-right and animate toward center-right without leaving the available screen area.

### Requirement 4: Dynamic response bubble
**User Story:** As a user, I want one readable bubble that grows with Muno's response and keeps every word available.

#### Acceptance Criteria
1. WHEN Muno responds, THE SYSTEM SHALL display exactly one speech bubble above the character.
2. THE bubble SHALL animate into view and grow upward according to its wrapped plain-text content while keeping the input and full-body character usable.
3. WHEN a new request begins, THE SYSTEM SHALL dismiss the previous response bubble, restore the compact window height, and then show the thinking state.
4. THE bubble SHALL contain plain text only and SHALL NOT expose tool names, schemas, raw provider errors, or duplicated messages.
5. THE SYSTEM SHALL preserve and display the complete response without clipping, line clamping, ellipsizing, or dropping text.
6. IF the complete response exceeds the bubble's screen-clamped maximum height, THEN THE bubble SHALL provide internal vertical scrolling while the outer window remains inside the available screen.
7. WHEN the bubble is dismissed, THE outer window SHALL return to its compact idle footprint while preserving its bottom anchor where screen geometry permits.

### Requirement 5: Character reactions and preserved behavior
**User Story:** As a user, I want compact Muno to remain expressive and retain all existing capabilities.

#### Acceptance Criteria
1. Muno SHALL visibly react to idle, thinking, speaking, excited, confused, surprised, sleeping, and error states.
2. WHILE speaking, Muno SHALL animate his mouth, eyes, and body gestures; in text-only mode, a timed visual speaking animation SHALL still occur.
3. THE SYSTEM SHALL preserve mute, persistent sleep, confirmed exit, continuous voice input, TTS, screen capture, recording, and desktop automation.
4. Muno SHALL remain the visible identity regardless of selected specialty.
5. WHEN the application exits, THE SYSTEM SHALL stop microphone, TTS, timers, and background workers cleanly.