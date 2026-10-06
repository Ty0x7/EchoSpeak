<h1 align="center">
  <img src="apps/web/public/logo.png" alt="Echo avatar" width="48" height="48" />&nbsp; EchoSpeak
</h1>

<p align="center">
  <strong>A personal agent. A place to make things happen.</strong><br />
  Talk, research, build and create with your choice of local or cloud models.
</p>

<p align="center">
  <a href="https://github.com/Ty0x7/EchoSpeak/releases/latest"><img src="https://img.shields.io/github/v/release/Ty0x7/EchoSpeak?label=release&amp;color=222222" alt="Latest published release" /></a><br />
  Windows 10 / 11 · x64 · <a href="LICENSE">MIT license</a>
</p>

<p align="center">
  <a href="https://github.com/Ty0x7/EchoSpeak/releases/latest"><strong>Download for Windows</strong></a>
  &nbsp; · &nbsp;
  <a href="https://ty0x7.github.io/EchoSpeak/">Explore the website</a>
  &nbsp; · &nbsp;
  <a href="docs/GUIDE.md">Getting started</a>
</p>

EchoSpeak brings your conversations, projects, research and creations into one workspace. Echo can use tools to work on a request, keep the results, and help you continue later. Add Jarvis, Glados or your own agents when you want different perspectives or a team working together.

Run a compatible model on your computer, or connect **OpenAI, Google Gemini, Claude or Grok**. Choose the provider and model that fit your work; optional voice, generation and integrations can be set up when you need them.

<p align="center">
  <img src="assets/readme/workspace.png" alt="EchoSpeak desktop workspace with a research conversation, a saved artifact and message controls" width="1200" />
  <br />
  <sub>Interface preview with an example conversation and simulated provider responses. No private conversations or credentials are pictured.</sub>
</p>

## EchoSpeak is the harness around your model

The model supplies reasoning and responses. **EchoSpeak is the agent harness that turns those responses into work:** it prepares the context, provides tools, checks permissions, carries out approved actions and records what actually happened. Changing models keeps you in the same workspace with the same underlying tools and controls.

1. **Understand the request.** Echo combines your message with the active agent's instructions, relevant conversation context and the attached project's information.
2. **Choose the next action.** The selected model can answer directly or request an available tool. EchoSpeak checks the request against tool permissions, project scope and approval rules before execution.
3. **Work from results.** Tool results return to the model so it can read, revise, search again or continue building. The runtime bounds this loop and supports stopping the run.
4. **Keep the evidence.** Tool activity, saved outputs and execution records show what ran. A confident reply alone does not prove a task succeeded.
5. **Continue the work.** Saved chats, project context, artifacts and research let you return later. Active chat runs can continue while you move between pages, and brief connection drops can reconnect to the same backend run.

The harness includes the **React workspace, local Python backend and Rust desktop host**. Its lean agent runtime connects models to files, terminal commands, web research, memory, media generation and configured integrations. Permissions are enforced in code; text from a model or webpage cannot grant additional authority.

This gives Echo a consistent way to act across supported providers. Results still depend on the selected model, available tools, configuration and the checks performed on the work.

## One workspace, more ways to work

1. **Research a question.** Search, read webpages and PDFs, compare sources, and follow citations into the chat's Research panel. Keep passages, findings and open questions in a temporary notebook; export or explicitly save useful findings to a project.
2. **Build something.** Attach a project, work with its files, run commands using your configured terminal environment, and keep a project brief for later chats. Open saved apps, documents, code and diagrams as Artifacts.
3. **Create images and videos.** Ask in chat and keep the results in **Creations**. Use supported Gemini image, Google Veo video, MiniMax video or local ComfyUI generation. Edit supported images and keep their related versions.
4. **Talk with Echo.** Dictate a prompt, read a reply aloud, or enter a full voice conversation with the new Echo avatar and a fading live transcript. Compatible Gemini Live models use native audio; other chat models use your configured speech services.
5. **Bring a team.** Chat with Echo, Jarvis, Glados or custom agents. Use mentions and group chats for different perspectives, handoffs and shared work. Agents can have their own model and tools.
6. **Pick up where you left off.** Search earlier chats, keep saved personal memories and chat summaries, and switch chats or pages while active runs continue. Restarting the backend interrupts active chat work; saved output remains available, but interrupted tool actions are not automatically repeated.
7. **Set things in motion.** Create scheduled routines and connect supported messaging services and tools. Configure optional integrations through Settings, including MCP connections. Routines need the backend running.

### See it in action

<details>
<summary><strong>Artifacts: keep the result beside the conversation</strong></summary>
<br />
<img src="assets/readme/artifacts.png" alt="EchoSpeak's shared right panel showing artifact versions, preview and source tabs, download, restore and Edit with Echo" width="1200" />

Preview saved work, choose an earlier version, and use **Edit with Echo** to prepare a revision in chat. Artifacts, research and tool activity share the same right panel.

</details>

<details>
<summary><strong>Voice: Echo fills the conversation</strong></summary>
<br />
<img src="assets/readme/voice.png" alt="EchoSpeak voice conversation with the new white Echo avatar centered, microphone controls and a small transcript fading in below" width="1200" />

The sidebar stays available. Pause the microphone and speech, resume listening, or return to the text composer. Appearance settings are shared with the floating desktop companion.

</details>

*These interface previews use sample data. They demonstrate the UI, not a benchmark or a claim that a particular provider completed the example.*

## Learning from experience

The current development branch adds a learning layer to the existing harness. It records each agent's part in a finished request, grades the available execution evidence, and can propose short lessons for similar future tasks. **This is ongoing development, not a claim that the learning features are included in the latest published installer or have completed browser validation.**

1. **Evidence before confidence.** Task records distinguish an unsupported claim, successful tool execution, a check after the work, corroborating checks and your confirmation.
2. **Feedback you control.** The new **Worked** and **Didn't work** reply controls feed the experience records. The Learning page provides agent track records, lessons, review actions and change history.
3. **Relevant lessons.** A small selection of advisory lessons can accompany similar tasks. Checked outcomes and owner feedback determine whether lessons are promoted, demoted or retired. Track records also inform routing and delegation.
4. **Separate authority.** Learning stores lessons and statistics separately from personal memory. It does not retrain model weights or change permissions, approval rules or tool access. Lessons derived from outside content or work that changed its own checks wait for owner review.
5. **Bounded reflection.** Reflection runs in the background while chats are quiet, with a daily limit. It uses the agent's selected model, so cloud reflection can send task context to that provider and incur API charges. Learning can be paused per agent.

These records help guide future work; they do not guarantee correctness or establish a measured improvement in model capability.

## Your first few minutes

1. **Install.** Download the Windows setup EXE from the [latest published release](https://github.com/Ty0x7/EchoSpeak/releases/latest).
2. **Meet Echo.** Initial setup helps you choose a model and check that it responds. Choose an installed local runtime or configure a cloud provider in Settings. Optional features can wait.
3. **Start your first chat.** Setup finishes before opening the conversation. Ask a question, attach a project or choose Voice.

For local chat, EchoSpeak supports **LM Studio, Ollama and compatible model servers**. Supported starter models can be downloaded and loaded through a running LM Studio or Ollama installation during setup. Local image/video generation has a separate optional setup and hardware requirements.

For cloud chat, Settings loads the provider's available model catalog and separately tests the selected model's response and tool exchange. Use an API model ID available to your account; a consumer chat subscription does not automatically include API access. A successful chat check does not validate image generation, video generation or every voice capability.

## Try asking Echo

> **Research:** “Compare three approaches to growing herbs on a small balcony. Read several sources and explain what still needs checking.”
>
> **Build:** “In this attached project, make a small habit tracker. Show me the result and explain how to run it.”
>
> **Create:** “Create an image of a quiet, rainy street at night.”
>
> **Work together:** “@Jarvis research the options, then @Glados help build a prototype.”

These are starting prompts, not recorded results. Available tools, model support and your settings determine what Echo can do.

## Recent updates

The source version is **10.5.0**, with newer learning work on the current development branch. The download button always points to the latest **published** release, which can differ from the source checkout.

1. **Tidier lists.** Group chats, Projects, Artifacts, Routines and Creations show three items with **Show more** / **Show less**.
2. **Clearer model choice.** Local and cloud providers are grouped, each provider's saved model is restored when you switch, and missing keys or models have clearer messages.
3. **Settings where you'd look.** Voice, channel, search and heartbeat options sit with their features. Advanced keeps rarely changed options, and Settings search finds options by name.
4. **Earlier reliability improvements.** Recoverable chats, research citations, image editing, message actions, full voice conversations with the new Echo avatar, and an installer with Echo branding.

[10.5.0 release notes](docs/releases/v10.5.0.md) · [10.4.3 release notes](docs/releases/v10.4.3.md) · [10.4.2 release notes](docs/releases/v10.4.2.md) · [10.4.0 reliability update](docs/releases/v10.4.0.md) · [Full release history](docs/releases/)

## Your data and your choices

EchoSpeak stores chats, memories, project information and saved creations on your machine. **Local storage does not mean every feature is offline:** cloud models receive the conversation context they need, online search makes network requests, and cloud generation sends the approved prompt and any selected references to its provider.

Personal memory, temporary research notes, project findings and learning lessons serve different purposes. Web research is not automatically saved as permanent personal memory. You explicitly choose when useful findings should become project context.

Review tool permissions and approval requests before allowing changes or uploads. Docker can provide an isolated terminal environment when available; check the selected terminal mode before running commands. Download releases from this repository and keep the app updated. If Windows reports a threat, stop and report the warning through GitHub issues. Keep Windows protection enabled.

Never paste API keys into an issue, chat screenshot or public document. Configure them in Settings. Provider access, API billing and local GPU compatibility depend on the services and hardware you choose.

## Learn more

1. [User guide](docs/GUIDE.md): installation, models, voice and everyday use.
2. [Architecture](docs/ARCHITECTURE.md): how the existing systems fit together. Check current code for newer development beyond the document's stated baseline.
3. [Roadmap](docs/ROADMAP.md): planned work and remaining validation.
4. [Release notes](docs/releases/) and [Changelog](CHANGES.md): changes across versions.
5. [GitHub issues](https://github.com/Ty0x7/EchoSpeak/issues): report a problem or suggest a feature.

<details>
<summary><strong>For developers: the existing system</strong></summary>

The Windows desktop host lives in `apps/desktop`; the React browser app lives in `apps/web`, and the Python backend in `apps/backend`. The lean agent runtime lives in `apps/backend/agent/lean`, with the new experience layer in `apps/backend/agent/learning`.

Use Python 3.11 or 3.12 and Node.js 22 or newer. See the [guide](docs/GUIDE.md) for running from source and optional dependencies. Extend the existing harness and UI; keep credentials, personal data and build artifacts out of commits.

</details>

<p align="center">
  <img src="apps/web/public/logo.png" alt="" width="24" height="24" /><br />
  <strong>Bring Echo home.</strong><br />
  <a href="https://github.com/Ty0x7/EchoSpeak/releases/latest">Download EchoSpeak</a> ·
  <a href="https://ty0x7.github.io/EchoSpeak/">Website</a> ·
  <a href="LICENSE">MIT license</a>
</p>
