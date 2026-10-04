<h1 align="center">
  <img src="assets/echospeak/logo.png" alt="" width="36" /> EchoSpeak
</h1>

<p align="center">
  <strong>Your AI. Your machine. Your rules.</strong>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-18+-339933.svg" alt="Node 18+" /></a>
</p>

<p align="center">
  <a href="docs/GUIDE.md">Guide</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="docs/ROADMAP.md">Roadmap</a> ·
  <a href="CHANGES.md">Changelog</a> ·
  <a href="docs/releases/v10.1.0.md">10.1 release notes</a>
</p>

---

EchoSpeak is a personal AI agent that runs on your hardware — not in someone else's cloud. It combines persistent memory, governed tool use, and multi-channel delivery across Discord, Telegram, Twitter, Twitch, and the web. Research, automate, and manage your world without giving up your data.

If you want an AI assistant that feels local, fast, and always-on — this is it.

## Download

**Windows 10/11 (64-bit):** get the installer from the [latest release](https://github.com/Ty0x7/EchoSpeak/releases/latest) (`EchoSpeak_10.1.0_x64-setup.exe`). Later versions install from inside the app: **Settings › About › Update**.

For a local model, install [LM Studio](https://lmstudio.ai) or Ollama (Gemma 4 E4B is the tested default). Docker Desktop is optional; when it's running, terminal commands run in a sandbox.

<details>
<summary><strong>Run from source</strong></summary>

```bash
# Backend (Python 3.11–3.12)
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py --mode api

# Web UI
cd apps/web
npm install && npm run dev
# → http://localhost:5174/app

# Windows desktop app (PowerShell)
powershell -File apps/desktop/scripts/build-windows.ps1 -PythonExecutable <path to python 3.12>
```

On Arch/CachyOS with PEP 668, use `./.venv/bin/python -m pip install -r requirements.txt`.

</details>

## Highlights

- **Local-first.** Conversations, memory and data stay on your machine. No telemetry, no cloud dependency.
- **Any model.** LM Studio, Ollama, llama.cpp, vLLM, LocalAI, OpenAI, Gemini; each agent can use its own.
- **Agents that finish the job.** One agent loop keeps calling tools until the task is done, says honestly when it runs out of steps, and offers Continue.
- **Agents and group chats.** Echo, Jarvis and Glados (or your own agents) hand work to each other, answer side by side, or work together: plan, split the work, do it, and keep going until it is checked done.
- **Coding and terminal.** Project folders, exact-text edits, file search, background processes, and a Docker sandbox by default.
- **Voice.** Local Whisper speech-to-text set up from Settings in one click, read-aloud, voice mode, and a "Hey Echo" wake word.
- **Memory and search.** Long-term memory, rolling chat summaries, and full-text search across every past chat.
- **Channels.** Discord, Telegram, Twitch and Twitter, with guests limited to look-up tools.
- **One-click updates.** Signed releases install from inside the app.

## How it works

```
App window · voice · Discord · Telegram · Twitch · routines
                         │
                         ▼
               process_query()  →  lean runtime (agent/lean)
                         │
        ┌────────────────┼──────────────────┐
        ▼                ▼                  ▼
   LeanSession       LeanTurn loop       Toolbox
   routing, group    model ⇄ tools,      files · terminal (sandbox)
   chats, handoffs   approvals, context  web · memory · chat search · MCP
                         │
                         ▼
          SQLite state · FAISS memory · settings (your data folder)
```

The full walkthrough, with diagrams, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python · FastAPI · lean agent loop · SQLite (FTS5) · FAISS |
| Frontend | React · Vite · Framer Motion |
| Windows desktop | Tauri 2 · Rust host · packaged Python sidecar · signed in-app updates |
| Voice | faster-whisper (local) · Windows SAPI · Piper · OpenAI audio |
| Models | Any OpenAI-compatible endpoint: LM Studio · Ollama · llama.cpp · vLLM · LocalAI · OpenAI · Gemini |

## Project structure

```
EchoSpeak/
├── apps/
│   ├── backend/
│   │   ├── agent/lean/      # The agent runtime: loop, sessions, toolbox, approvals, personas, rooms
│   │   ├── agent/           # Tools, memory, state store (SQLite), voice, projects
│   │   ├── api/             # FastAPI app (server.py), routes/ by area, deps.py, auth.py
│   │   ├── scripts/         # eval_gemma.py (20-prompt evaluation) and tools
│   │   ├── discord_bot.py, telegram_bot.py, twitch_bot.py, twitter_bot.py
│   │   └── SOUL.md          # Echo's personality
│   ├── web/                 # React app (and the website)
│   └── desktop/             # Tauri Windows app, sidecar packaging, release scripts
├── docs/
│   ├── GUIDE.md             # Install, set up, use
│   ├── ARCHITECTURE.md      # How EchoSpeak works
│   ├── ROADMAP.md           # What's next
│   ├── releases/            # Release notes
│   ├── research/            # Research behind design decisions
│   └── archive/             # Older design docs, kept for history
└── CHANGES.md               # Changelog
```

## Configuration

Most settings can be changed from the **Web UI Settings tab** without editing files.

| Layer | File | Purpose |
|-------|------|---------|
| Static | `apps/backend/.env` | API keys, master switches |
| Runtime | `data/settings.json` | Persisted overrides (provider, toggles) |
| Secrets | `data/settings.secrets.json` | API keys and tokens saved from Settings |
| Personality | `apps/backend/SOUL.md` | Agent voice and boundaries |

## Safety model

1. **Smart approvals.** Deleting files, dangerous commands, messages that leave the PC, desktop control and MCP actions wait for Allow / Deny in the chat. Everything else runs.
2. **Sandbox by default.** With Docker running, commands run in a container with only the project mounted, offline until you allow the internet.
3. **Project scope.** File tools work inside the attached project folder.
4. **Caller roles.** Only you get files, terminal, memory and past chats. People reaching Echo through Discord, Telegram, Twitch or Twitter get look-up tools only.
5. **Signed updates.** The app installs only releases signed with the project's key.

## Documentation

| Document | Description |
|----------|-------------|
| [Guide](docs/GUIDE.md) | Install, set up and use EchoSpeak |
| [Architecture](docs/ARCHITECTURE.md) | How EchoSpeak works, with diagrams |
| [Roadmap](docs/ROADMAP.md) | What's next |
| [Changelog](CHANGES.md) | Full version history |
| [Release notes](docs/releases/) | One page per release |

## Contributing

Contributions welcome.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [Architecture](docs/ARCHITECTURE.md) for how the pieces fit before extending the agent.

## License

MIT — see [LICENSE](LICENSE) for details.

## Acknowledgments

- [FastAPI](https://fastapi.tiangolo.com/) — Backend framework
- [FAISS](https://github.com/facebookresearch/faiss) — Vector similarity search
