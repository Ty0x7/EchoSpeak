# EchoSpeak guide

How to install EchoSpeak, set it up, and use it day to day. For how it works
inside, see [ARCHITECTURE.md](ARCHITECTURE.md). For what's next, see
[ROADMAP.md](ROADMAP.md).

## 1. Install

### Windows app (recommended)

1. Download `EchoSpeak_<version>_x64-setup.exe` from the
   [latest release](https://github.com/Ty0x7/EchoSpeak/releases/latest) and run it.
2. Open EchoSpeak. The first screen walks you through picking a model.
   Echo greets you before setup. Optional tools can wait. Choose an installed local
   runtime or cloud provider; setup can download and load a supported starter model
   through Ollama or LM Studio after you choose **Download and load**. On the last
   step, **Check my model** sends a small response request. **Start chatting** opens
   your first chat only after setup finishes; it does not send a greeting for you.
3. A new-release notice appears above **Settings** in the desktop sidebar. Click it
   to open **Settings › About › Update** and install the update.
   Updates are checked before installation.
4. If Windows flags a download or installed file as a threat, stop and check
   **Windows Security › Protection history**. Report the threat name and app version
   through [GitHub issues](https://github.com/Ty0x7/EchoSpeak/issues), without keys or
   personal logs. Keep Windows protection enabled.

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

## 2. Pick a model

Cloud providers have two separate checks in **Settings › Models**. **Check catalog**
loads model IDs available to the key; it does not prove the selected model responds.
**Test response** checks a small reply and a harmless two-tool round-trip through
the same adapter as chat. It never runs file, terminal, media or external tools.
The test requests may incur an API charge. Authentication, billing, quota, permissions and model
compatibility errors need different fixes; follow the displayed provider message.
Pick a model from the provider's list. For an ID the list doesn't show, choose
**Use a custom model ID…** and enter the exact API ID, not a display name. Specialized
image/video models belong in Creations. In the chat, the provider menu groups apps on
this PC and cloud providers; switching provider brings back the model you saved for it.

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
- Use **Copy** beneath a prompt or reply. Replies also have **Read aloud / Stop reading**.
  Prompts have **Edit** and **Retry**: continue in a new chat from before that prompt,
  keeping the original chat and its later messages. Completed history, the selected
  model, project and group members carry forward; past tools are not run again and
  approval grants are not copied. Stop an active chat before retrying it.
- New chats get a topic title after the first reply. This uses a small additional
  request to the selected chat provider; manually renamed chats keep your name.
  Live-only models keep the cleaned-up fallback title.
- **Appearance:** EchoSpeak starts in the light theme. Choose Light, Dark or Match system in
  **Settings › General › Appearance** (or on the last setup step); every window switches together.
- **Think** and effort controls follow the selected model and adapter. Unsupported
  thinking controls are disabled. Models that always think cannot turn thinking off.

### Agents and group chats

- Built-in agents: **Echo** (everything), **Jarvis** (research and memory) and **Glados**
  (coding and terminal). Add your own in **Settings › Agents**: name, instructions,
  which tool sets it gets, and an optional model.
- Agents hand work to each other. Mention one with `@Jarvis`; mention several, or `@all`, to
  hear from each.
- **New group chat** creates a room with chosen agents. Turn on **Work together** to have them
  plan, split the work into tasks, do it, and keep going until a check confirms it's done
  (15, 30 or 60 turns).

### Learning from experience

- After each task, EchoSpeak grades what actually ran: **Claimed**, **Ran**, **Checked**,
  **Double-checked** or **You confirmed**. When no chat is running, the agent reviews checked
  work and failures with its own model and keeps up to two short lessons. It reads the relevant
  ones before similar tasks.
- Under a reply, **Worked** confirms it and **Didn't work** marks it failed, with an optional note
  the agent reads when it reviews the task. Press the button again to take it back.
- **Learning** in the sidebar shows track records, lessons with their source tasks and history,
  recent grades and tool reliability. Approve or reject lessons waiting for your review. You can
  edit, retire, restore, delete or **Undo** any change, and pause learning for one agent.
- Lessons are advice only. They never change permissions, approvals, tools or settings. Lessons
  learned while reading the web or email, or from a task that changed tests or EchoSpeak's own
  files, wait for your approval. Guests on Discord, Telegram, Twitch or Twitter never teach and
  never see lessons.
- **Settings › General › Learning from experience** turns it off, limits reviews per day (each
  is one call to the agent's model) and sets how many lessons an agent reads.

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
- **Voice** opens a full conversation view beside the sidebar, with Echo centered
  and the chat transcript and tool/approval activity fading in at the bottom. The
  composer returns when you choose **Back to chat**. **Pause / Listen** controls the
  microphone; pressing Pause also stops speech playback.
- Dictation remains a separate microphone shortcut for writing a message. Voice
  conversations use your selected local or cloud chat model with configured speech
  recognition and playback. Compatible Gemini Live models use native audio.
- **More chat controls** holds automatic read-aloud, sound, wake word, screen monitor
  and available reasoning effort. **Wake** listens for "Echo" while idle outside
  voice conversation mode. Wake checks run locally too.
- **Settings › Advanced › Companion** previews the new Echo avatar's listening,
  thinking, speaking and working states. Appearance changes apply to voice and the
  floating desktop companion, including the voice avatar size.
- Speech output uses Windows voices, Piper, or OpenAI audio if you add a key. Choose
  engines in **Settings › Voice › Engines**.

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

It holds your settings, chats, memories, agents, projects, routines and downloaded
models. What agents learned is in `learning/experience.db`; deleting it resets learning
without touching anything else. Back it up by copying the folder while EchoSpeak is closed. Treat that backup
as private: it can contain account credentials and personal conversations.
Echo's personality is editable in **Settings › Personality**.

## 9. Troubleshooting

| Problem | Try |
| --- | --- |
| "Model not reachable" | Start LM Studio or Ollama and load a model, then **Settings › Models › Test**. |
| Replies are slow at first | The first message loads the model; later ones are faster. |
| Commands fail in the sandbox | Start Docker Desktop, or switch **Terminal** to **This PC**. |
| Voice says unavailable | Download a Whisper model in **Settings › Voice**. |
| Something looks wrong | Check the status in Settings. If desktop startup fails, use **Open logs** on its recovery screen; review logs for personal data before sharing. |

## Research, native Live audio and local creations

Artifacts, research and tool activity share one right sidebar. Switch its tabs to
keep your preview, source passage and filter in place. The shared expand/close
controls work across all views; incoming tools do not switch the tab you are reading.
Artifact previews have a saved-version picker and **Edit with Echo**. Select a passage
in a text/source view for **Edit selection with Echo**. This prepares a chat draft;
you review and send it, and Echo is asked to preserve the original as an earlier version.
During voice mode the right panel is tucked away so Echo fills the conversation area;
returning to chat restores the panel you had open.

Open **Research & activity** in a chat, then **Research**, to see that chat's sources,
page passages and working notes. Search-result snippets are marked separately from
pages Echo actually read. The notebook keeps working evidence for seven days, apart
from permanent personal memory. Expired working evidence is cleaned up automatically.

Click a retained citation in an answer to open its source passage in that same panel.
**Edit working notes** lets you record findings, open questions and conflicts; **Export
report** downloads a Markdown copy. With a project attached, enter a **Project brief**
and use **Save findings to project** to keep notes and up to 20 read sources from the
current filter for later project chats. Web evidence stays separate from instructions
and personal memory; it is only retained beyond the notebook's expiry when you choose
to save it to a project or export it.

Browser refreshes and brief connection drops reattach to an existing chat run without
submitting the prompt twice.
**Stop** still stops that exact chat. Restarting the backend preserves partial output
and marks the run interrupted; it does not automatically repeat tools or resume a
chat request after a backend process restart.

In **Creations**, **Edit with Echo** opens the image's chat with an editable draft.
Describe your changes and send it. Gemini supports image-reference edits; the local
ComfyUI preset supports one image-to-image reference. Cloud references are uploaded
only through the existing approval flow. Originals remain saved, and **Related versions**
shows their variants. Video editing is not included. Interrupted Google Veo, MiniMax
and ComfyUI jobs with saved provider IDs reconnect without a new submission. If
automatic recovery fails, use **Reconnect to existing job**. A timed-out synchronous
image request without a remote ID cannot be safely retried automatically.

Windows installation repairs standard EchoSpeak shortcuts that point to recognized
installation paths. The app does not rewrite shortcuts each time it starts. For a pin
that still points to an independent older copy, unpin it and pin the current app from
Windows search. Reinstalling preserves the application's separate runtime data folder.

To try native Gemini audio, save a Gemini key and select an accessible Live model.
Choose **Voice** to open the voice conversation. Audio goes
to Google and API charges may apply. Gemini's PCM reply plays in the chat; **Read**
can also enable native playback for typed Live requests. In Voice mode the microphone
can listen while the reply plays; speaking interrupts playback. Live sessions attempt
bounded resumption without replaying completed tool actions. Generated tools still
go through EchoSpeak's normal permissions and approvals. Native mic currently supports
direct chats. Use dictation or a standard chat model for the transcription path, including
local Whisper when configured.

In **Creations › generation settings**, select the NVIDIA GPU for managed Windows
setup. Setup checks the driver/runtime combination, available disk space and CUDA
execution before downloading model weights. Failed or cancelled downloads retain
partial files and resume on retry; completed downloads are verified before use.
Server and workflow checks are distinct from a real render: after enabling generation,
use **Test local generation** from an existing chat to submit a small real creation.
The test uses the managed starter model and appears in Creations. Hardware beyond the
managed Windows/NVIDIA path can connect its own ComfyUI installation.
