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

### Talking to an agent while it works

- The box stays usable while an agent works. **Enter queues a follow-up**: it waits above the box
  and runs as its own message when the current reply ends. Edit or remove it before then.
- **Steer** (the button, or **Ctrl+Enter**) adds to the work in progress. The agent reads it at its
  next step (never in the middle of a command or search), keeps what it has already done, and the
  message shows "You added: …". If it had already finished, the steer is queued instead.
- **Stop** stops the reply and pauses the queue: queued messages wait for **Run next** or your next
  message instead of starting by themselves.

### Research workers (off by default)

- **Settings › General › Research workers** lets an agent send up to six read-only workers to look
  up separate questions at the same time ("compare these four laptops"), then write the answer
  itself from what they found. Workers can only search and read; they never edit files, send
  anything, appear in the chat or ask you for approval.
- A **Research workers** card under the reply shows each worker's question, status, steps, the
  sources it actually opened and its time. Open a row for its findings. Stop stops every worker.
- They share one token budget per request and run one or two at a time on a local model, up to
  four on a cloud model. They're off by default because they only pay off for broad research,
  and mostly on capable models. Run `python scripts/swarm_bench.py` with your model to see whether
  they help you.

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
- Now and then a matching lesson is held back at random (one time in five for unproven lessons,
  one in ten for proven ones, never your own). Those tasks show how the agent does without it. A
  lesson is proven only when tasks go at least as well with it, and it is retired or demoted when
  tasks go clearly better without it. The lesson's line shows both: "helped 3 · without it: 1 of 3
  worked".
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

- Selecting a project opens its visual workspace: HTML-only Work with Widgets/List views, Files, Conversations, Research and Routines. Opening a conversation restores that existing chat; browsing a project does not change the active chat's scope or stop its background work.
- Project workspaces discover HTML reports directly from the attached folder, pair their technical Markdown and refresh while visible without reloading unchanged previews. Other Markdown and earlier imported reports stay in Files. The pencil beside a project's new-chat button edits its display name and logo without moving its folder. Settings → Skills offers project-documentation for review and approval. See [Project documentation and visual workspace](PROJECT_WORKSPACE.md) for conventions and supported agent setup.
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
  a real browser, opening apps, desktop control, email, and editing EchoSpeak itself. A switch
  that is off removes the tools from the agent, and the agent is told what is off and where to
  turn it on, so it can tell you instead of looking for a way around it.
- If you decline an action, the agent won't ask you for that exact action again in the same
  request. After three failures in a row of one tool, it is told to change approach.
- **Always allow** on an approval card stops the questions for that kind of action, in every chat:
  desktop control, ordinary commands on this PC, sandbox internet, a particular connected-app action,
  or cloud generation. Settings › Permissions › **Always allowed** lists them; remove one and it asks
  again. Deleting, dangerous commands, sending messages, identity changes, tasks for other agents
  (A2A) and anything asked because outside content was read can't be always allowed.
- **Autonomous** (Settings › General › Ask before risky actions) is for walking away from a long task.
  Desktop control and ordinary commands outside the sandbox run without asking; everything else that
  would ask (deleting, dangerous commands, sends, cloud costs, A2A) is skipped and listed as
  **Waiting for you**, so the run keeps going. The status pill shows **Autonomous**. Permissions,
  Blocked apps, Private mode, project folders and Stop all still apply, and Discord or routines never
  get approvals from it.
- **Approvals** decide what asks first: **Risky only** (recommended), **Autonomous**, **Every action**, or **Never**.
  Risky means deleting files, dangerous commands, messages that leave the PC, desktop control,
  and MCP actions. An approval shows in the chat with **Allow once**, **For this chat** and **Decline**.
- After an agent reads a web page or email, anything that would send data out asks first, even
  with approvals off. A tool call that contains one of your API keys is always refused.
- **Separate reader for outside content** (Settings › Agent; **Email and apps** by default): an email,
  Discord or connected-app result goes first to a reader with no tools, and the agent sees only the
  facts it found. If the content tried to instruct an AI, the chat says so. Choose **Also web pages**
  to cover pages and documents too, at the cost of one extra model call per result.
- People who reach Echo through Discord, Telegram, Twitch or Twitter get look-up tools only:
  no files, terminal, memory or past chats.

### Screenshots and computer use

Ask Echo to capture a monitor or a visible window. The screenshot appears in the conversation, and a vision-capable chat model receives the actual image. Click it to open the original in the same right panel used for artifacts, research and activity, or save it. Echo can reopen a screenshot by its observation ID and crop it to inspect details. Text-only models cannot interpret pixels; OCR remains a separate fallback and requires Tesseract.

For input, enable **Allow actions on this computer** and **Control the desktop** in Settings › Permissions. Right below, **Add to Echo** gives Echo (or another agent) Desktop control and screenshots in one click; you can also do it under **Agents** in the left sidebar with the agent's **Edit** button. **Screen & images** (Vision) provides screenshots; **Desktop control** provides input. Echo reports which is available instead of treating a screenshot as permission to click. Desktop control is optional and is not enabled on agents automatically.

To launch an app, also enable **Open apps**. Echo can then open any installed app by its Start-menu name (for example, **Minecraft Launcher**), the name of a shortcut on your desktop (for example, **26.2** for `26.2.lnk`, or a Steam game's `.url` shortcut) or an executable path. **Blocked apps** in Settings › Permissions lists what it may never open; new installs start with Windows' system-settings tools (Registry Editor, Group Policy, System Configuration, diskpart, bcdedit). **Favorite apps** are optional names Echo can suggest, not a limit. The old **Allowed apps** list moved into Favorite apps when you upgraded. Shortcuts open exactly as saved. Shells and script hosts such as PowerShell, cmd or Python can be opened but never receive arguments from Echo; commands go through the terminal and its approvals. Windows Store apps are resolved through their installed Windows registration. Echo checks the window or a screenshot after requesting a launch; launching does not prove the app is ready or that game input will work.

Echo can click, right-click, double-, triple- and middle-click, move, drag, scroll up, down and sideways, type, press shortcuts and hold keys or a mouse button, including together. Relative mouse movement lets it attempt camera movement in games that capture the pointer. Holds and waits last at most five seconds, with up to ten seconds per batch. With a Claude 5 model, Echo uses **Claude's own computer tool** (the one Claude was trained on, including zoom), through the same checks; turn it off in Settings › Permissions to compare with Echo's general tool. Each batch uses a recent screenshot and returns a new capture so Echo can check the result. Changed window or monitor geometry requires another capture. Desktop control always asks first, even when ordinary approvals are off. Choose **For this chat** to let Echo keep acting on what it sees for the rest of that chat, so a game or a long task doesn't stop at every step. If the same request also read web pages, email or other outside content, every action asks again and **For this chat** isn't offered. Keys are sent with hardware scan codes, which more games accept.

If Echo can capture the screen but cannot click, check the agent's **Desktop control** toolset and both permissions above. Desktop controls use the computer-action tool. Shell text such as `where="host" click ...` is not a desktop command, and installing a package or enabling sandbox networking will not turn it into one. Echo now receives a specific correction for these misplaced terminal options.

Use **Stop everything** to cancel work. Held keys and mouse buttons are released when a new computer-action batch ends, fails or is cancelled. Other apps can still change the screen while Echo works; check its results, particularly around destructive controls. Some games reject simulated input, and these short, model-driven batches are suited to slower interactions rather than real-time gameplay.

Images stay in this chat's temporary observation store, separate from personal memory and Creations, for up to seven days or until its 512 MB limit evicts older captures. Chat references remain afterward and show an unavailable message. At most three recent observations are included in model context. With a cloud model, those images are sent to that provider under your existing model privacy settings; Private and Offline modes retain their restrictions.

### Connected apps, other agents and timing

Settings › Advanced › Connections & skills:

- **Sign in.** Hosted MCP servers that need an account (Notion, Linear, Atlassian and others) show
  **Sign-in needed**. **Sign in** opens their page in your browser; the reply comes back to EchoSpeak on
  this PC, and tokens stay encrypted for your Windows account and refresh by themselves. **Sign out** forgets them.
- **App views.** When a connected tool ships its own interactive view (a chart, a form, a viewer), it
  appears in the chat after the tool runs. It can reach only the addresses its server declared, and none
  in Private or Offline mode. A view can run its server's read-only tools; anything that changes things
  goes through Echo with its usual approvals. If a view offers to say something, the text waits in your composer.
- **Long tasks.** Renders and exports from connected apps can run for minutes; their progress shows under
  the step, and **Stop** cancels them on the app's side.
- **Other agents (A2A).** Add another AI agent, such as a friend's EchoSpeak, by its address. Echo checks
  that its card is signed by a key it publishes and shows that key's fingerprint; compare it with what
  the agent's owner gave you, then **Trust**. Echo asks you before every message it sends, refuses to
  send anything if the agent's key changes, and treats replies as outside content.

**Activity › Timing** shows where the last reply's time went: each model step, each tool, and time
spent waiting for you. To keep traces in your own tools, set an OpenTelemetry collector address in
Settings › Advanced › Traces. Traces never contain message text.

A tool result too long for the conversation (a big log, a long page) is kept whole for seven days;
Echo sees its beginning and end and reads the rest by searching it, instead of losing the middle.

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
- **Hear a reply**: the speaker button next to **Copy** under each of Echo's replies reads it
  aloud; press it again to stop. Automatic read-aloud and voice conversations use the same voice.
- Speech output uses Windows voices, Piper, **ElevenLabs** (cloud), or optional **Drama-3** (local). Choose one under
  **Settings › Voice › Read aloud** and press **Play sample** to hear it. For ElevenLabs,
  add your API key in the ElevenLabs group, then pick a voice, model and speed. Each reply read
  aloud sends its text to ElevenLabs and uses characters on your plan; Private and Offline modes
  turn it off. Gemini Live conversations keep their own native voice.

### Optional Drama-3 speech

In **Settings › Voice**, choose **Drama-3** for read-aloud. Your existing local or cloud AI model still handles the conversation. **Built with Fish Audio.** Read the linked Fish Audio Community License before importing or downloading a model; these model terms differ from EchoSpeak's MIT license.

1. Choose a trusted `audiocpp_cli.exe` with its DLLs and press **Verify runtime**. It must include the `fish_drama3` speech loader. If you already have the Windows C++ toolchain, the optional build action prepares a pinned compatible runtime for you. Regular GGUF chat runtimes and older audio.cpp binaries do not work here.
2. Accept the model license and explicitly download a quantization, or **Link model** to use a compatible GGUF already on your PC. Downloads are about 16–24 GiB, show progress and can be cancelled/resumed. Linking does not copy your file.
3. Choose CPU or NVIDIA CUDA and press **Play sample**. This is the actual speech check; a verified runtime alone does not prove the model fits your hardware. More CPU expert layers can reduce GPU demand but need RAM and can be slower. Your local chat model shares those resources.
4. Optionally add a short local PCM reference WAV and its exact transcript under **Memory and voice reference**. Use only a voice you have the right to use. Leaving it blank uses the model's default voice.

The speaker button, automatic read-aloud and standard voice conversations use this selection. Native Gemini Live uses its own audio. Drama models load only when speech is requested and unload afterward; long replies use bounded clips and may pause between them. **Cancel sample** or the existing playback Stop cancels a pending Drama worker. Missing voice files never prevent normal chat or force setup to run again.

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
