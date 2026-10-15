# Muno

[![Windows CI](https://github.com/Fakude/codex-pet-muno/actions/workflows/ci.yml/badge.svg)](https://github.com/Fakude/codex-pet-muno/actions/workflows/ci.yml)

Muno is a floating Windows desktop companion with chat, voice, screen analysis, personalities, and optional desktop automation.

> **Platform:** Windows 10/11 &nbsp;|&nbsp; **Interface:** PyQt6 &nbsp;|&nbsp; **Model provider:** Groq-compatible OpenAI API

## Why Muno

Muno explores what a local-first desktop companion looks like when powerful actions remain human-controlled. It combines a visual desktop pet with voice input, local activity awareness, browser and GitHub tools, file discovery and an optional phone companion. Operations that can send, publish, alter files or control the desktop are designed to request approval before execution.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GROQ_API_KEY = "your-key"
python pet.py
```

Copy `.env.example` into your Windows user environment when you want optional GitHub, email, voice or mobile features. Do not commit credentials.

## Portfolio evidence

- Automated Windows checks run on every push and pull request.
- [Architecture](docs/architecture.md) explains the local components and trust boundaries.
- [Security model](SECURITY.md) documents permissions, data storage, threat assumptions and reporting.
- [Roadmap](ROADMAP.md) clearly separates implemented functionality from planned work.

## What is implemented vs planned

| Area | Current status |
| --- | --- |
| Desktop companion, personalities and local configuration | Implemented |
| Voice input/output and live transcription feedback | Implemented on Windows |
| Browser, GitHub, email and file-index integrations | Implemented with user approvals and configured credentials/sessions |
| Activity journal and duplicate-action prompts | Implemented |
| Phone companion on the local network | Implemented; opt-in and token paired |
| Public internet access, cloud sync and native mobile app | Not implemented; deliberately out of scope for the current local-first release |
| Automated security testing and broad integration-test coverage | In progress |

## Latest Features

**Voice & Interaction**
- Real-time voice streaming with adaptive noise detection and pre-roll buffering
- Interrupt Muno mid-sentence by speaking — it stops and listens immediately
- Live transcription shows exactly what was heard and when listening stopped
- Microphone level meter provides visual feedback as you speak

**Screen Watching & Control**
- Pick a screen region once and repeat tasks over it automatically
- Type into selected fields from voice or generate contextual content
- Watch for specific information, decode data, or monitor for security issues
- Video watching capabilities for selected screen areas

**Self-Extension**
- Muno identifies capability gaps and researches solutions from vetted sources
- Searches PyPI and GitHub with trust scoring (age, releases, stars, license)
- Detects typosquats and abandoned packages automatically
- All installs are approval-gated, pinned, and isolated to `~/.codex_pet/extensions/`

**Mobile & Multi-Device**
- Token-paired PWA for phones and tablets (iOS, Android)
- Works as a home screen app with no installation required
- PC does the work; mobile provides remote control and approval

**Personality & Design**
- Choose between multiple pet characters with unique appearances
- Apple-inspired design tokens across PWA and Qt window
- Smooth, interactive animations that respond to drag speed and direction

## Start

1. Create a virtual environment and install dependencies: `python -m pip install -r requirements.txt`
2. Add `GROQ_API_KEY` to your Windows user environment variables.
3. Run: `python pet.py`
4. Use **Settings** to choose a name and specialty. Use **Snap** to attach the current screen to your next question.

## What Muno can do

Ask in plain language. Muno picks the right action itself.

- **Open apps** — "open Chrome", "launch Spotify", or any installed program by name or path.
- **Browse** — "open youtube.com", "search the web for tide tables", "find lo-fi beats on YouTube".
- **Watch things** — "play lo-fi beats", "play the third result". Muno reads the real YouTube result list and picks by position.
- **Use web pages** — "what does this page say?", "list the links", "click Sign in", "search this page for X".
- **Watch a region** — "watch this part of the screen". Pick a region once, then Muno can repeat tasks over it: looking for changes, typing into fields, reading data, or watching for security warnings. Works with videos too.
- **Type where you point** — select a text field on screen and Muno can type into it from what you say, or generate appropriate content for that field based on context.
- **WhatsApp** — "tell Sarah I'm running late". Muno opens the chat with the message typed in; you press Enter to send. Say "send it" and Muno delivers it through WhatsApp Web.
- **Contacts** — "save Sarah's number as +1 555 123 4567", "who are my contacts?"
- **GitHub** — "create a repo called my cool project", "upload MUNO.pdf to my pet repo",
  "open an issue about the login bug", "merge pull request 3", "did the build pass?"
- **Find files** — "where is MUNO.pdf?" Muno keeps a local catalogue, so lookups are instant.
- **Email** — "email Sarah the MUNO.pdf and say it's the draft she asked for". Muno writes
  the subject and body, attaches the file by name, and shows it to you before sending.
- **Read mail** — "any unread email?"
- **Activity** — "what am I doing?", "what apps are open?", "how have I spent the last hour?"
- **Screen** — use **Snap** to attach the current screen to your next question.
- **Voice control** — speak to Muno and interrupt mid-sentence when it's talking. Live transcription shows exactly what was heard.

Web actions work on the page's real structure (links, buttons, text) rather than
guessing where to click, so they do not depend on window position, DPI scaling,
or what the screen looks like.

## Browser control

For reading and clicking pages, Muno drives Chrome through the Chrome DevTools
Protocol. It runs its own Chrome profile at
`%LOCALAPPDATA%\Muno\ChromeProfile`, which means:

- It starts alongside your normal Chrome instead of taking it over.
- Sites stay signed in, because the profile persists between sessions.
- The first time you use WhatsApp Web, scan the QR code once in Muno's window.

Muno starts this browser on demand. `playwright install` is not needed, since it
attaches to the Chrome you already have.

## GitHub

Set `GITHUB_TOKEN` to a token from [github.com/settings/tokens](https://github.com/settings/tokens).
Scopes: `repo` covers repositories, files, issues, branches, and pull requests.
Add `workflow` to trigger Actions, and `delete_repo` only if you want Muno able
to delete repositories.

Muno uses the GitHub REST API rather than clicking around the website, so
operations are fast and do not break when the site changes. Repository names can
be given as `name` (your account is assumed), `owner/name`, or a pasted URL, and
spoken names are converted automatically, so "my cool project" becomes
`my-cool-project`.

## Finding files quickly

"Upload MUNO.pdf to my repo" works without you giving a path. Muno keeps a
SQLite catalogue of your files at `~/.codex_pet/file_index.db`.

- Building it takes a few seconds and happens in the background at startup.
- Lookups then take milliseconds instead of scanning the disk.
- It covers your home folder and its documents, desktop, downloads, and media
  folders. System, cache, and dependency folders are skipped deliberately: they
  add hundreds of thousands of useless entries and make matches worse.
- Add more locations with `MUNO_INDEX_ROOTS`, separated by `;`.
- If a brand new file is missing, say "refresh your file index".

When several files share a name, Muno picks the best match and tells you which
one it used.

## Access without per-app setup

Creating an API key or app password for every service does not scale. Muno
prefers whatever is **already signed in on your machine**, and only asks for a
credential when nothing else can do the job.

In order of preference:

1. **Browser sessions you already have.** Gmail and other web services work
   through the browser Muno drives, using the session already signed in there.
2. **CLI tools you already trust.** If the GitHub CLI is installed, say
   "connect my GitHub" and Muno starts `gh auth login`, a browser sign-in. It
   then borrows that session, so you never create or paste a token.
3. **Explicit credentials.** `GITHUB_TOKEN`, `EMAIL_PASSWORD` and friends still
   work and take priority when set, for cases the above cannot cover.
4. **Desktop apps, opt-in.** Outlook, Word, and Excel are reachable over COM
   with no password at all. Outlook email is switched off by default because it
   sends from its own configured account rather than your chosen address; enable
   it with `MUNO_EMAIL_USE_OUTLOOK=1`.

Ask "how will you send email" or "check GitHub" and Muno tells you exactly which
of these it is using.

### Using your existing browser logins

By default Muno runs its own Chrome profile. It coexists with your normal
browsing but starts signed out of everything, so Gmail and other sites need one
sign-in inside that window.

To reuse the logins you already have, point Muno at your real profile:

```
MUNO_CHROME_PROFILE=C:\Users\you\AppData\Local\Google\Chrome\User Data
MUNO_CHROME_PROFILE_NAME=Default
```

Then every site you are signed into works with no further setup. The one catch
is that Chrome ignores the debugging port when it is already running for that
profile, so **Chrome must be fully closed** the first time Muno starts it. Muno
detects this and says so rather than failing silently.

## Email

Muno picks a route automatically and tells you which one:

| Route | Needs | Attachments |
|---|---|---|
| Gmail in the browser (default) | One sign-in in Muno's browser | Yes |
| Direct SMTP | `EMAIL_ADDRESS` + app password | Yes |
| Default mail app (`mailto:`) | Nothing; you press send | No |
| Desktop mail client (COM) | Opt-in; an account already set up | Yes |

Set which address you send from by asking, for example "send email as
me@gmail.com", or with `EMAIL_ADDRESS`. Force a route with
`MUNO_EMAIL_BACKEND=gmail|smtp|mailto|outlook`.

Gmail is the default, and Muno keeps your chosen address as the sender. It
attaches files by driving Gmail's own upload, so attachments do not force a
switch to another account.

The desktop mail client is **off by default**, because it sends from whichever
account it is configured with, which may not be the address you want recipients
to see. Turn it on with `MUNO_EMAIL_USE_OUTLOOK=1`, or ask for it once with
`MUNO_EMAIL_BACKEND=outlook`.

### If you do want direct SMTP

Use an **app password**, not your account password. Providers refuse normal
passwords from third-party apps once two-factor authentication is on:

| Provider | Where to create one |
|---|---|
| Gmail | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| Yahoo | [login.yahoo.com/account/security](https://login.yahoo.com/account/security) |
| iCloud | [appleid.apple.com](https://appleid.apple.com) → App-Specific Passwords |
| Outlook | [account.microsoft.com/security](https://account.microsoft.com/security) |

Work accounts on Microsoft 365 often have basic SMTP disabled by an
administrator. Those need OAuth2, which Muno does not do yet; it will tell you
the sign-in was rejected rather than failing quietly. The desktop route above
usually works for those accounts instead.

Attachments are found by name through the file index, so "email that PDF to
Sarah" is enough. Muno writes a real subject and body with a greeting and
sign-off, and never leaves `[placeholders]` behind.

## Knowing what you're working on

Muno samples the foreground window every few seconds and can tell you what you
are doing, what is open, and where recent time went. It reads window titles and
process names only, which is enough to tell coding from email from YouTube.

It also checks in occasionally: when you come back from a break, or after a long
stretch on one thing, it may ask whether you are still on the same task. These
messages are written locally rather than by the model, so they cost nothing and
never trip a rate limit.

Turn check-ins off with `MUNO_CHECKINS=0`, or tune `MUNO_CHECKIN_GAP` and
`MUNO_CHECKIN_TASK`. Muno stays quiet while you are typing, while it is
answering, and in silent or sleeping mode.

### Contacts

WhatsApp needs a phone number, so names are resolved through a local map at
`~/.codex_pet/contacts.json`. Save contacts by asking Muno, or edit that file directly.
Numbers need a country code.

## Moving Muno around

Drag Muno anywhere with the left mouse button. While you drag, it faces the way
it is going and plays its run animation, quicker the faster you pull it. Let go
and it settles back to idling.

- **Drag** to move. Muno stays inside the screen it is on.
- **Double click** to send it back to its corner.
- Where you leave it is remembered, so it returns there next launch.
- The animations are smooth and interactive, responding to drag speed and direction in real time.

Sprite pets have separate left and right run cycles drawn by their artist, and
Muno uses the real ones rather than mirroring a single direction. If an atlas
happens to lack a left cycle, the right one is flipped as a fallback. The
hand-drawn Muno leans into the direction of travel instead, easing in and out so
it reads as weight rather than a sudden flip.

Clicking the text box or the buttons still does what you expect: dragging only
begins on the pet itself and the empty space around it.

## Choosing what Muno looks like

Muno's own body is drawn in code, but it can wear any of the pixel pets shared at
[codex-pets.net](https://codex-pets.net). Open **Choose a Pet** from the tray
menu, or **Appearance** in Settings.

Pick one and it downloads, then animates in place of the built-in look. Nothing
about Muno's abilities changes; only the body does. The UI follows Apple design
principles with refined spacing, typography, and color choices for a premium feel.

Each pet is one WebP atlas of 192x208 cells, a row per animation and a column per
frame. Muno's states are mapped onto the animations the artist provided:

| Muno is | Animation used |
|---|---|
| idle, sleeping | idle |
| speaking | waving |
| thinking | waiting |
| excited, surprised | jumping |
| confused | review |
| error | failed |

| being dragged | running-right / running-left |

There is no dedicated talking animation in the format, so waving stands in for
speech: it moves, and it reads as communicating at this size.

Each animation runs at its own pace rather than one shared rate, so idling looks
calm and running looks urgent. Body motion is sampled at about 60 Hz for smooth
movement and leaning, but sprite frames are still stepped, never blended:
blending pixel art only makes it blurry.

Sheets are a couple of megabytes, so a chosen pet is cached under
`~/.codex_pet/pets/` and loads with no network afterwards. The picker still works
offline, showing whatever is already downloaded.

**These are other people's artwork.** The creator's name is stored with each pet
and shown in the picker, and Muno says who made it when you switch. Pets are
downloaded for personal use in the app; if you want to redistribute one, that is
between you and its creator.

## Growing new abilities

Asked for something it cannot do, Muno works out what is missing rather than just
refusing. It checks what it already has, names the specific packages that would
provide the gap, looks each one up, and reports what it finds:

```
🔎 Looking into: read PDF files

✓ pypdf 6.16.2  (trust 100/100 — looks well established)
   A pure-python PDF library capable of splitting, merging, cropping…
   licence BSD-3-Clause, 116 releases
   last released 8 days ago
   py-pdf/pypdf: 10,183 stars

✕ reqests: No such package on PyPI.

Nothing has been installed.
```

Trust is scored on real evidence: how long the project has existed, how recently
it was released, how many releases there are, whether a licence is stated, and
the stars and archive status of its linked repository. Anything scoring below 25
is refused outright even if you ask for it directly.

### Where the candidates come from

Muno does not guess package names from memory. A confident guess at a package
name is precisely how people end up installing a typosquat, so it consults a
curated corpus of working applications instead:

[Shubhamsaboo/awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps)
· Apache-2.0 · 135k stars

That repository holds around 160 example apps, each with its own
`requirements.txt`. Those are real, working dependency sets for concrete tasks,
so "what should I use for X?" becomes a lookup rather than a recall problem. In
practice it gets the right answer:

| Asked for | Found |
|---|---|
| read PDF files | `pypdf` |
| scrape a website | `playwright`, `firecrawl-py`, `scrapegraphai` |
| work with spreadsheets | `pandas` |
| remember things long term | `mem0ai` |

**Only text is read from it**: file paths, names, and dependency lines. No code
from that repository is downloaded, imported, or run, and packages found there
are still just candidates. Appearing in a popular repo earns a suggestion, not
permission. Each one goes through the same trust scoring and the same approval as
anything else, which is why `scrapegraphai` still comes back flagged for having no
licence stated.

The file listing is cached for a week, so lookups are instant and work offline.
Ask "how do other projects do X?" to query it directly.

**Names that imitate popular packages are flagged loudly.** `reqests`, `numpi`
and `pandaz` are caught as one character from the real thing, which is exactly how
typosquatting works.

### Where the line is drawn, and why

The research is fully automatic. **The install always asks first.**

That split is deliberate. Installing a package runs its code here, and Muno is a
rich target: it holds your model key, GitHub token, mail access, a browser session
with live logins, an index of your home folder, and a journal of your work. One
bad dependency reaches all of it.

More importantly, Muno reads untrusted text by design: web pages, emails, message
threads. If installing were automatic, any of those could ask for it, and a page
saying "install helper-utils to continue" would be a working attack. Requiring a
human on the one irreversible step removes that entire class of problem and costs
you a single click.

Other protections:

- Only released PyPI packages, never a repository cloned and run. A name and a
  pinned version can be checked; a stranger's shell script cannot.
- Versions are pinned, so what you reviewed is what installs.
- Installs go to `~/.codex_pet/extensions/`, isolated from the app's own
  environment, so they cannot silently replace a core dependency and can be
  removed cleanly with "uninstall that".
- Names are validated and passed as arguments, never through a shell.
- Every install is written to the task journal.

Ask "what can you do?" for the current picture, or "what have you added?" for the
list. This does **not** make Muno able to rewrite its own logic; it lets it add
libraries, with your agreement.

## Listening

The microphone is read continuously in 20 ms frames rather than recorded in one
go, which is what makes the conversation feel live rather than turn-based.

**You can interrupt.** If Muno is talking and you start speaking, it stops
mid-sentence and listens. Being talked over is the thing that makes an assistant
feel like a recording, so this takes priority over finishing the reply.

**A meter under the pet shows what is being heard**, so you can tell it is
picking you up before you find out from the reply.

**You can see when your turn ends and what was understood.** The status line
moves through the whole cycle, so a pause is never ambiguous:

```
Listening…                          speech detected
Heard 2.3s, writing it down…        you stopped, transcription started
Heard: "open chrome and search…"    what it actually understood
Ignored background noise: "you"     heard, but judged not to be speech
```

That last one matters: if Muno stayed silent about noise it discarded, you could
not tell a broken microphone from a phrase deliberately ignored. Every transcript
also goes to the activity log, so you can scroll back through exactly what it
thought you said.

Several things work together for accuracy:

- **Word onsets are preserved.** A detector only knows speech began after the
  level rises, which is already inside the first syllable. Muno keeps a rolling
  320 ms buffer and prepends it, so "open Chrome" does not arrive as "pen Chrome".
- **The threshold adapts** to the room instead of being a fixed number, with
  hysteresis so a wavering voice is not chopped into fragments.
- **Names are given to the transcriber.** Your contacts, the apps you use, your
  installed pets and terms like "repository" are passed as a hint, which stops
  them coming back mangled.
- **Guessing is turned off.** The language is pinned and the temperature is zero,
  so the same audio always gives the same text.
- **Invented filler is discarded.** Handed near-silence, Whisper reaches for
  phrases like "Thank you" or "Thanks for watching". A short result matching that
  filler, or a long result from a tiny burst of voice, is dropped rather than
  acted on.

### Interruption and speakers

While Muno is speaking, its own voice reaches the microphone. To avoid it
interrupting itself, the bar is raised during playback and about 180 ms of
sustained speech is needed to count as you cutting in.

On **headphones** this is exact. On **loud speakers** Muno may occasionally hear
itself; turning the volume down or using headphones removes the problem entirely.
Proper echo cancellation is not implemented.

### Calibrating

The microphone tools run from a terminal, with no GUI and no API key, which is
the quickest way to tell whether levels are sane:

```
python voice_stream.py --calibrate     measure the room's noise floor
python voice_stream.py --monitor       live meter and detected speech
```

`--monitor` prints a bar, the current level, the adaptive floor and trigger, and
reports each utterance it captures, including ones it rejected and why.

## Voice and narration

Muno speaks using the Windows speech engine, so there is no API key, no network
call, and no limit on how much it can say. Pick a voice and pace with
`MUNO_VOICE`, `MUNO_VOICE_RATE` (-10 to 10), and `MUNO_VOICE_VOLUME` (0 to 100).
Run `python -c "import voice; print(voice.list_voices())"` to see what is
installed.

As it works, Muno tells you what it is doing:

1. It announces the whole plan first, before touching anything: *"3 things:
   looking for MUNO.pdf, then creating the repository my-report, then uploading
   MUNO.pdf to my-report."*
2. It names each action as it starts.
3. It closes with what actually happened, including anything that failed:
   *"Done: found MUNO.pdf, created my-report, uploaded MUNO.pdf. I couldn't
   finish sending your email."*

Say "be quiet" to silence it, or set `MUNO_NARRATE=0` to keep voice replies but
stop the step-by-step commentary. The written log is kept either way.

## The activity log

Click **Log** on Muno, or **Activity Log** in the tray menu, to open a full
record of everything it has done. Muno's own window is small, so the log gets its
own resizable window.

Each request becomes a heading with its steps underneath, showing the exact
details used and the outcome:

```
15:29:04 You: put MUNO.pdf in a new private repo and email Sarah the link
  Plan: 4 things: looking for MUNO.pdf, then creating the repository …
  ✓ Looking for MUNO.pdf
      name: MUNO.pdf
      → Found: C:\Users\nhlan\...\MUNO.pdf
  ✓ Creating the repository quarterly-report
      name: quarterly-report   private: True
      → Created the private repository quarterly-report
  ⊘ Sending your email to sarah@example.com
      → The user did not approve this desktop action.
  Done: found MUNO.pdf, created quarterly-report, uploaded MUNO.pdf.
  I couldn't finish sending your email to sarah@example.com.
```

Markers are `○` pending, `▶` running, `✓` done, `✕` failed, `⊘` you declined.
**Copy all** puts the whole record on the clipboard.

## Using Muno from a phone or iPad

Muno's hands are on this PC: it opens local apps, reads local files, and drives
the local browser. So Muno cannot run *on* a phone. Instead the phone becomes a
remote control, and the PC keeps doing the work.

It is a web app, not a native one, so iPhone, iPad, and Android all work with
nothing to install.

**Turn it on** with `MUNO_MOBILE=1`, then restart Muno. Open **Connect Phone**
in the tray menu for the link, which looks like:

```
http://192.168.1.5:8765/?t=<your pairing token>
```

Open that on your phone while it is on the same Wi-Fi. On iPhone or iPad, tap
**Share** then **Add to Home Screen** and it gets an icon and runs full screen
like a normal app.

From the phone you can send requests, watch each step as Muno performs it, and
approve or decline the actions that need confirmation. The PC and the phone stay
in sync, and whichever one answers a prompt first wins.

### Two things that will block it

**The pairing token is required.** There is no open mode. The link contains the
token; treat it like a password. It lives in `~/.codex_pet/mobile_token.txt`, and
deleting that file issues a new one.

**A Public network profile blocks incoming connections.** Windows Firewall will
stop your phone reaching Muno. Either set the network to Private, or allow
inbound TCP on the port. Muno checks this and warns you in the Connect Phone
dialog rather than leaving you guessing.

By default Muno listens only on your local network address, which also means
`localhost` on the PC itself will not reach it. Set `MUNO_MOBILE_BIND_ALL=1` if
you need that.

### Away from home

The server is deliberately local. To reach Muno from outside your network, put
it behind something that handles identity and encryption properly, such as
Tailscale or a Cloudflare Tunnel. Do not forward the port on your router: that
publishes desktop control to the internet behind a single token, over plain HTTP.

## The task journal

The activity log window is a live view and is lost when Muno restarts. The
journal is the durable record, kept in SQLite at `~/.codex_pet/journal.db`.

Every request and every action is written as it happens:

- **Sessions** — one per launch, so history can be attributed to a particular run.
- **Requests** — the exact prompt, when it arrived, and where from: typed,
  spoken, or from your phone.
- **Actions** — the tool, the real arguments, the outcome, how long it took, and
  the request that caused it.

That chain means every action can be traced back to the words that triggered it.
Ask "what have you done today?", "did you already do this?", or "how much history
do you have?"

### Not doing things twice

Before Muno repeats an action that changes something, it checks the record. If it
finds a match it asks first, and says when it happened and whether that was this
same conversation, earlier the same day, or a different session entirely:

```
⚠️ This looks like a repeat.

You already did this 2 hours ago, in a different session on another day.
That time you asked: "create a github repo called quarterly-report"

Muno wants to create a GitHub repository named 'quarterly-report' again.

Do it anyway?
```

Two deliberate choices here:

**Only actions with consequences are checked.** Looking up a file or re-reading a
page twice is harmless, so those never prompt. Creating a repository, sending an
email, uploading a file, merging a pull request and similar do.

**A duplicate forces a prompt even when the action normally runs freely.** Saving
a contact does not usually need approval, but doing it a second time will ask,
because an accidental repeat is the expensive case.

Wording differences are ignored when deciding what counts as the same action, so
re-sending the same upload with a reworded commit message is still recognised as
a repeat. Genuinely different arguments are treated as new work.

### What this means for privacy

The journal holds your prompts and the arguments Muno used, so it is as sensitive
as the work itself. It stays on your machine and is never uploaded. Delete
`~/.codex_pet/journal.db` to clear it; a new one starts on the next launch.

## Privacy and control

- Conversations are stored locally in `~/.codex_pet/conversations.json`, separated by personality. This is for conversational continuity, and only the newest 40 messages per personality are kept.
- The task journal in `~/.codex_pet/journal.db` is the accountability record: prompts, actions, arguments, outcomes, and timestamps, kept per session. It is what lets Muno notice repeated work. It never leaves your machine.
- Use **Settings → Clear This Mode's Memory** to delete a personality's saved chat.
- Muno asks before launching an app, typing, clicking, pressing keys, or resizing another app.
- Muno asks before opening a WhatsApp chat, showing the recipient and the exact message. The message is only typed into the chat, never sent automatically, so nothing reaches another person without you pressing Enter.
- Muno asks before sending a WhatsApp message for real, and before submitting text into a web page, since a form can post, buy, or send.
- Opening a page, reading it, listing links, and clicking are not gated, since browsing is reversible. Only `http` and `https` addresses are allowed, so a link cannot be used to start a local program.
- Because Muno can read web pages, treat page text as untrusted. A page or an incoming message could contain wording aimed at the assistant, which is why anything that sends, posts, or submits needs your approval.
- Muno asks before any GitHub action that changes something: creating or deleting a repository, uploading or deleting a file, opening an issue or pull request, merging, publishing a release, or running a workflow. Reading is not gated.
- Deleting a repository shows an explicit warning, since it destroys code, issues, and history permanently.
- The GitHub token is read from the environment, never written to Muno's config, and never included in a reply or an error message.
- The file catalogue stores only names, paths, sizes, and timestamps. It records no file contents and is never uploaded.
- Muno asks before sending an email, showing the recipients, subject, body, and any attachments. Reading your inbox is not gated.
- Your email password is read from the environment, never written to Muno's config, and never shown in a reply or an error.
- Activity awareness keeps window titles and process names in memory for the current session only. Nothing about your activity is written to disk or sent anywhere unless you ask a question that needs it.
- Speech is produced on your machine by the Windows speech engine. Nothing Muno says is sent to a speech service.
- The activity log shows every action with the real arguments used, so a confirmation prompt is never the only place you find out what Muno did. It lives in memory for the session and is not written to disk.
- Phone access is off until you enable it, always requires the pairing token, is compared in constant time, and locks out an address after repeated bad attempts. It listens on your local network only unless you say otherwise, and it serves plain HTTP, so keep it to networks you trust.
- Actions that need confirmation still need it when you are on your phone. The prompt is mirrored, not skipped.
- Contacts stay on your machine in `~/.codex_pet/contacts.json` and are never sent anywhere except as the phone number in a WhatsApp link you approve.
- API credentials are read from environment variables and are never written to Muno's config files.

## Build a Windows executable

Run `powershell -ExecutionPolicy Bypass -File .\build_exe.ps1`. The executable will be at `dist\Muno.exe`.
