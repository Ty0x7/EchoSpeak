# EchoSpeak guide

How to install EchoSpeak, set it up, and use it day to day. For how it works
inside, see [ARCHITECTURE.md](ARCHITECTURE.md). For what's next, see
[ROADMAP.md](ROADMAP.md).

## 1. Install

### Windows app (recommended)

1. Download `EchoSpeak_<version>_x64-setup.exe` from the
   [latest release](https://github.com/Ty0x7/EchoSpeak/releases/latest) and run it.
2. Open EchoSpeak. The first screen walks you through picking a model.
3. Later versions install from inside the app: **Settings › About › Update**.
   The app only installs releases signed with the project's key.

### From source (Windows, macOS, Linux)

You need Python 3.11 or 3.12 and Node.js 18+.

```bash
# Backend
cd apps/backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py --mode api           # http://127.0.0.1:8000

# Web app (second terminal)
cd apps/web
npm install
npm run dev                        # http://localhost:5174/app
```

On Arch or CachyOS (PEP 668), install with `./.venv/bin/python -m pip install -r requirements.txt`.
`scripts/install.sh` does the same steps and writes a `start` script.

To build the Windows installer yourself: `powershell -File apps/desktop/scripts/build-windows.ps1 -PythonExecutable <python 3.12>`.

## 2. Pick a model

Cloud providers have two separate checks in **Settings › Models**. **Check catalog**
loads model IDs available to the key; it does not prove the selected model responds.
**Test response** sends a small, explicit request through the same adapter as chat
and may incur an API charge. Authentication, billing, quota, permissions and model
compatibility errors need different fixes; follow the displayed provider message.
Use an exact API model ID rather than a display name. Specialized image/video models
belong in Creations.

EchoSpeak talks to any OpenAI-compatible chat endpoint. Set it in **Settings › Models**.

| Option | What you need |
| --- | --- |
| **LM Studio** (tested default) | Install [LM Studio](https://lmstudio.ai), download a model (Gemma 4 E4B works well), start the server. EchoSpeak finds it on `localhost:1234`. |
| **Ollama** | Install Ollama and pull a model. EchoSpeak finds it on `localhost:11434`. |
| llama.cpp, vLLM, LocalAI | Point **Models** at the server's address. |
| **OpenAI** or **Gemini** | Paste an API key under **Models › Cloud**. |

On first run EchoSpeak checks which local servers are running and picks one. Each agent can
use its own model (**Settings › Agents**), so a small local model can chat while a bigger one codes.

## 3. Chat

- **New chat** in the sidebar starts a Session. Every Session keeps its own history,
  summary and model.
- Echo keeps working (calling tools) until the task is done. If a long task reaches the step
  limit it says what it finished and shows **Continue**.
- **Stop** ends a run at once. Messages you send while Echo is busy wait in a queue and run next.
- Search every past chat from the sidebar. Echo can search them too (`chat_search`).

### Agents and group chats

- Built-in agents: **Echo** (everything), **Jarvis** (research and memory) and **Glados**
  (coding and terminal). Add your own in **Settings › Agents**: name, instructions,
  which tool sets it gets, and an optional model.
- Agents hand work to each other. Mention one with `@Jarvis`; mention several, or `@all`, to
  hear from each.
- **New group chat** creates a room with chosen agents. Turn on **Work together** to have them
  plan, split the work into tasks, do it, and keep going until a check confirms it's done
  (15, 30 or 60 turns).

### Projects and coding

- Drop a folder on the composer (or **Projects › Attach folder**) to work inside it. File tools
  only reach the attached folder.
- Every file overwrite is checkpointed and can be undone.
- Agents read, search and edit files with exact-text edits, run commands, and start dev servers.

### Terminal

**Settings › Terminal** decides where commands run:

| Mode | Behaviour |
| --- | --- |
| **Auto** (recommended) | The Docker sandbox when Docker Desktop is running, this PC otherwise. |
| **Sandbox** | A Linux container with Node, Python and git that only sees the project. Network is off until you allow it. |
| **This PC** | PowerShell on your machine, with everything you have installed. |

Dangerous commands ask first in every mode.

## 4. Approvals and permissions

- **Settings › Permissions** turns whole abilities on or off: editing files, running commands,
  a real browser, opening apps, desktop control, email, and editing EchoSpeak itself.
- **Approvals** decide what asks first: **Risky only** (recommended), **Every action**, or **Never**.
  Risky means deleting files, dangerous commands, messages that leave the PC, desktop control,
  and MCP actions. An approval shows in the chat with **Allow** and **Deny**.
- After an agent reads a web page or email, anything that would send data out asks first, even
  with approvals off. A tool call that contains one of your API keys is always refused.
- People who reach Echo through Discord, Telegram, Twitch or Twitter get look-up tools only:
  no files, terminal, memory or past chats.

## 5. Voice

- **Settings › Voice**: **Download** a local Whisper model (tiny, base or small) for
  speech-to-text. Nothing leaves your PC.
- **Read** reads replies aloud. **Voice** mode listens, sends, speaks the reply and listens again.
- **Wake** listens for "Echo" while idle. Wake checks run locally too.
- Speech output uses Windows voices, Piper, or OpenAI audio if you add a key.

## 6. Memory

- Echo saves facts you ask it to remember, and recalls them when they matter.
  **Settings › Memory** lists, edits and deletes them.
- Long chats get a rolling summary so earlier context isn't lost.
- **Settings › Advanced › Memory & documents** has the full list, compaction, the Obsidian sync
  and uploaded documents. Search can use an embedding model already loaded in your model server.
  For private local search, select **Install** under **Local search model** to download the
  optional ONNX model (about 90 MB), then restart EchoSpeak. The installer does not include
  this model and EchoSpeak never downloads it automatically.

## 7. Channels and automations

- **Settings › Channels**: Discord, Telegram, Twitch and Twitter bots. Each needs its own token.
  Discord users can be owner, trusted or public by id.
- **Settings › Automations**: routines that run a prompt on a schedule, and the heartbeat,
  which checks in on a timer and reports where you choose.
- Webhooks (`POST /webhooks/<path>`) run routines and must be signed with the webhook secret.

## 8. Your data

Everything lives in one data folder:

- Windows app: `%LOCALAPPDATA%\ai.echospeak.desktop\runtime`
- From source: `apps/backend/data` (override with `ECHOSPEAK_DATA_DIR`)

It holds settings (`settings.json`, secrets in `settings.secrets.json`), chats (`phase3/state.db`),
memory, agents, group chats, projects, routines, voice models and the soul. Back it up by copying
the folder while EchoSpeak is closed. The soul (Echo's personality) is editable in
**Settings › Personality**.

## 9. Remote access

The API only answers this PC by default, and refuses to listen on a network address
unless a key is set. To reach it from another device, set `API_AUTH_ENABLED=true` and
`API_AUTH_KEY=<long random key>` in `apps/backend/.env`, then send the key as
`X-EchoSpeak-Key: <key>` or `Authorization: Bearer <key>`. Requests from other machines
always need the key. Don't expose the port to the internet even then; use a VPN or tunnel.

## 10. Troubleshooting

| Problem | Try |
| --- | --- |
| "Model not reachable" | Start LM Studio or Ollama and load a model, then **Settings › Models › Test**. |
| Replies are slow at first | The first message loads the model; later ones are faster. |
| Commands fail in the sandbox | Start Docker Desktop, or switch **Terminal** to **This PC**. |
| Voice says unavailable | Download a Whisper model in **Settings › Voice**. |
| Something looks wrong | The doctor report (`GET /doctor` on the backend) lists what's misconfigured. Desktop logs are in the app log folder (`backend.log`). |

## Research, native Live audio and local creations

Artifacts, research and tool activity share one right sidebar. Switch its tabs to
keep your preview, source passage and filter in place. The shared expand/close
controls work across all views; incoming tools do not switch the tab you are reading.

Open **Research & activity** in a chat, then **Research**, to see that chat's sources,
page passages and working notes. Search-result snippets are marked separately from
pages Echo actually read. The notebook keeps working evidence for seven days, apart
from permanent personal memory. Expired rows are removed on reads, startup and hourly.

To try native Gemini audio, save a Gemini key and select an accessible Live model.
Turn **Live mic** on, then use the existing **Mic** or **Voice** control. Audio goes
to Google and API charges may apply. Gemini's PCM reply plays in the chat; **Read**
can also enable native playback for typed Live requests. In Voice mode the microphone
can listen while the reply plays; speaking interrupts playback. Live sessions attempt
bounded resumption without replaying completed tool actions. Generated tools still
go through EchoSpeak's normal permissions and approvals. Native mic currently supports
direct chats. Turn Live mic off to use the existing transcription path, including
local Whisper when configured.

In **Creations › generation settings**, select the NVIDIA GPU for managed Windows
setup. Setup checks the driver/runtime combination, available disk space and CUDA
execution before downloading model weights. Failed or cancelled downloads retain
verified-manifest partial files and resume on retry; completed files must pass SHA-256.
Server and workflow checks are distinct from a real render: after enabling generation,
use **Test local generation** from an existing chat to submit a small real creation.
The test uses the managed starter model and appears in Creations. Hardware beyond the
managed Windows/NVIDIA path can connect its own ComfyUI installation.
