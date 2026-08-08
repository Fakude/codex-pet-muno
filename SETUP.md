# Codex Pet - Setup Guide

## What is Codex Pet?

A futuristic AI companion that adapts to your needs. Choose your specialty (Code, Writing, Life Coach, Research, or Design) and get personalized help.

## Quick Start

### 1. Install Requirements
```bash
pip install pyqt6 openai
```

### 2. Get a Free API Key
- Go to: https://console.groq.com
- Sign up (free)
- Create an API key
- Copy it

### 3. Add Your Key
Open `pet.py` and find this line (around line 18):
```python
api_key="YOUR_GROQ_API_KEY" # <--- Put your key here
```
Replace `YOUR_GROQ_API_KEY` with your actual key:
```python
api_key="gsk_abc123xyz..." # <--- Your actual key
```

### 4. Run It
```bash
python pet.py
```

## Using Codex Pet

### First Time
1. App opens with default "Code Expert" mode
2. Type a question and click "Send"
3. Wait for response

### Switch Personality
1. Click **⚙️ Settings** button
2. Choose a specialty from dropdown
3. Enter your name (optional)
4. Click **Save**
5. Chat history clears (new personality = fresh start)

### Your Settings Are Saved
- Closes the app? Reopens with your last personality & name
- Stored in: `~/.codex_pet/config.json`

## Personalities & What They Do

### 💻 Code Expert
- Debug and explain code
- Suggest improvements
- Write code snippets
- Fast, direct answers

### ✍️ Writing Assistant
- Draft emails and posts
- Improve writing
- Brainstorm ideas
- Friendly, encouraging tone

### 🎯 Life Coach
- Goal setting & motivation
- Habit tracking tips
- Personal growth advice
- Supportive & practical

### 🔬 Research Partner
- Explore topics deeply
- Find credible info
- Think critically
- Thorough & curious

### 🎨 Design Advisor
- UI/UX feedback
- Color & layout ideas
- Design principles
- Visual & creative

## Keyboard Shortcuts
- **Ctrl+Enter** in input box = Send immediately (not yet implemented, coming soon)

## Troubleshooting

### "Error: Invalid API key"
- Check your key is copied fully
- Make sure no spaces before/after
- Try generating a new key on Groq console

### "Connection timeout"
- Check internet connection
- Groq API might be down temporarily
- Try again in a minute

### "Empty response"
- Ask a clearer question
- Try simpler language
- Switch personality and retry

### Settings not saving?
- Make sure `~/.codex_pet/` directory exists
- Check file permissions
- Try creating folder manually: `mkdir ~/.codex_pet`

## Advanced

### Clear All Settings
Delete this file:
- Windows: `C:\Users\[YourName]\.codex_pet\config.json`
- Mac/Linux: `~/.codex_pet/config.json`

App will reset to defaults on next run.

### Check Your Config
Open `~/.codex_pet/config.json` to see:
- Your current personality
- Your saved name

### Customize Personalities
Edit `pet.py`, find `PERSONALITIES` dict (around line 25), modify system prompts.

## Next Steps

1. **Test all personalities** — Get familiar with each mode
2. **Collect feedback** — What works? What's missing?
3. **Phase 2**: Integration features (cross-device, app connectors)
4. **Phase 3**: Full platform support (web, mobile, embedded)

## Roadmap
See `ROADMAP.md` for full vision and timeline.

## Support

- Questions? Check if your issue is in "Troubleshooting"
- Feature idea? Note it down for Phase 2
- Bug? Describe what happened + your OS

---

**Made with ❤️ for building the future of AI companions**
