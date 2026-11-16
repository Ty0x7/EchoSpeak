# One harness, two interfaces: what the desktop app and the terminal expose

> **Status, 15 November 2026.** This is a review, not a plan that has been built. It covers the
> desktop/web UI (`apps/web`) and the Echo terminal (`apps/terminal`) on branch
> `harness-first-rearchitecture`. Both run against **one backend and one data folder**
> (`~/.local/share/ai.echospeak.desktop/runtime`).
>
> How it was checked:
> - a read-only inventory of `apps/web/src`, with file:line references;
> - the terminal's command table;
> - live runs on qwen3.5:9b, where the desktop app attached to the terminal's backend and showed its
>   conversation.
>
> Related: [harness-first.md](harness-first.md), [cli-design.md](cli-design.md),
> [edit-preview.md](edit-preview.md).

## What a user can do today

| Area | Desktop app | Terminal (`echospeak`) |
|---|---|---|
| **Projects** | Attach a folder from the composer, sidebar, Projects page, Code tree or workbench. Detach with ×. The Projects page lists, edits, removes, and starts chats in a project. The project workspace has Work / Files / Conversations / Research / Routines. **Every attach is silently `trusted`** (`index.tsx:697`); trust is never shown. | Asks "Work in this folder?" at start-up and remembers a "no". `/project [PATH \| -]` attaches with an explicit trust question, or detaches. The banner and footer show the project. No file browser beyond Echo's own tools. |
| **Environments** | A Chat / Agents / Code switch decides pages and labels. **The UI never reads or sets `assistant_mode`.** | `/mode unified\|environments`. The footer shows the agent; the profile follows the attached project. |
| **Models** | Per-chat model browser over every provider, with local and API labels. Effort and thinking controls, a context meter. Defaults in Settings › Models. Free-text per-agent model. Smart model choice. | First-run picker with **GPU-fit hints** ("fits your GPU" / "slow: spills to RAM"). `/model` for this chat, the default, or both. Context % in the footer. |
| **Agents** | Roster, create, edit and delete agents (personality, tools, model). Direct chats, group chats (one reply / work together), routines, durable work and proposals (inside the Activity tab). | `/agents`, `/agent NAME` (a direct session, the same rooms as the app). Agent header per answer. **No create/edit, groups or routines.** |
| **Settings** | 17 sections. Permission switches, approval mode (`lean_approval_mode`), privacy modes, memory and learning, voice, channels, connections, automations… | `/permissions` (switches, each change confirmed). Shift+Tab cycles smart → autonomous → always. `/mode`. **No privacy, voice, channels or connections.** |
| **Activity** | Live status pill with Stop. Steer and queue, approvals (allow once / this chat / always / decline), questions, verification notes and outcome. Right panel: Activity, Research, Artifact, Screen. Workbench: files, changes and Git review, terminal tabs, scripts, preview. Stop everything. | Spinner with live status. Enter steers, Tab queues, Esc stops. Approvals (allow once / this chat / always / no / **no, and tell Echo what to do instead**), questions, verification and outcome. Ctrl+O transcript, `/approvals` incl. held items, `/resume` follows a running task, `/status`, `/usage`. Edit diffs and `/preview`: see edit-preview.md. |

## Verified across both, live

- **One backend, shared data.** The desktop app attached to the backend the terminal started (same pid, port 49097) and listed the terminal's conversation in its chat list. Closing the app left the backend running for the terminal.
- **Stopping a run** reads "Stopped" in the terminal. The desktop showed the same run as a red **"ERROR · cancelled"**. **Fixed** (`11e3987`): an owner's stop is no longer rendered as a failure, live or after reload.

## What is confusing or incomplete

1. **The answering profile and the label disagree in unified mode.** The desktop labels a conversation by its tab (Chat / Code / Agents). In unified mode the backend decides per turn from the trusted project. A "Chat" with a folder attached answers as the engineer. Neither UI shows which profile answered.
2. **Trust is invisible in the desktop.** Attaching any folder grants engineering tools in unified mode, with no question asked. The terminal asks; the desktop doesn't.
3. **`assistant_mode` exists only in the backend and the terminal.** No Settings control, and the desktop's tab copy still describes the old three-environment and hosted-runtime model.
4. **Model defaults.** On a fresh install the backend auto-detects the *first* Ollama model; here that was the 27B, which doesn't fit the GPU. The terminal asks with GPU-fit hints; the desktop just uses it. The local context length is assumed (32K), never read from Ollama.
5. **Live sync between clients.** A run started in one client is picked up by the other only when that conversation is opened (`GET /query/runs`). There is no per-session follow stream, so an already-open desktop window doesn't show a terminal run live. (From source, `index.tsx:1299-1316`; not tested live.)
6. **Approval parity.**
   - Desktop: "Decline" only. Terminal: "No, and tell Echo what to do instead", now working end to end after the denial fix `7e51e59`.
   - Desktop has a visible "Always allowed" grants list; the terminal doesn't show grants.
7. **Durable work and "needs you".** In the desktop these sit inside the Activity tab of the right panel; in the terminal they are in `/approvals`. Neither has one obvious "Needs you" inbox.
8. **Legacy hosted-runtime UI is still visible:** Settings › Coding agents, the Code runtime picker, external session views, usage-plan widgets. In unified mode they show "Not found" or do nothing.
9. **No file view without a project.** In a source checkout, the backend's file root falls back to the EchoSpeak repository itself. The terminal's folder prompt avoids this; the backend default should change too.

## Highest-priority next changes

| # | Change | Why | Size |
|---|---|---|---|
| 1 | **Per-turn profile in the event stream** (`profile: chat \| engineering \| agent`), shown in both UIs | Fixes the label/profile mismatch and makes unified mode understandable | S backend + S each UI |
| 2 | **Desktop trust question on attach**, matching the terminal, with trust shown on the project chip; `trust_state` editable | Engineering tools should never appear silently | S–M |
| 3 | **Desktop navigation for unified mode**: Conversations · Agents · Projects · Library with a project chip (harness-first §6); `assistant_mode` in Settings; hide legacy hosted-runtime UI when unified | The one-assistant design isn't visible in the GUI yet | M–L |
| 4 | **Backend model defaults**: prefer a model that fits the GPU; read the context window from Ollama (`/api/show`, or the loaded model's context); move the terminal's fit hints into a backend route both UIs use | Fresh installs picked a model that can't run interactively here | S–M |
| 5 | **Per-session follow stream** (`GET /sessions/{id}/follow`), used by both clients | Live cross-client visibility; removes `/query/runs` polling | M |
| 6 | **Approval parity**: desktop "Decline and tell Echo…"; terminal shows and revokes "always allowed" grants | Same decisions everywhere | S |
| 7 | **One "Needs you" view** (approvals, held work, questions, failed deliveries) in both | Durable work is hard to find | M |
| 8 | **Terminal coverage**: create and edit agents, routines and privacy mode from the terminal (`/agent new`, `/routines`, `/privacy`) | Lets the terminal fully control EchoSpeak | M |
| 9 | **Backend file root without a project**: not the source checkout | Avoids Echo reading its own code by default | S |
