# Muno

Muno is a floating Windows desktop companion with chat, voice, screen analysis, personalities, and optional desktop automation.

## Start

1. Create a virtual environment and install dependencies: `python -m pip install -r requirements.txt`
2. Add `GROQ_API_KEY` to your Windows user environment variables.
3. Run: `python pet.py`
4. Use **Settings** to choose a name and specialty. Use **Snap** to attach the current screen to your next question.

## Privacy and control

- Conversations are stored locally in `~/.codex_pet/conversations.json`, separated by personality.
- Only the newest 40 messages per personality are retained.
- Use **Settings → Clear This Mode's Memory** to delete a personality's saved chat.
- Muno asks before typing, clicking, pressing keys, or resizing another app.
- API credentials are read from environment variables and are never written to Muno's config files.

## Build a Windows executable

Run `powershell -ExecutionPolicy Bypass -File .\build_exe.ps1`. The executable will be at `dist\Muno.exe`.
