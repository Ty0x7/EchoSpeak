# Assistant mode, environments, approval modes: what each controls (24 November 2026)

Campaign workstream 9. This is a review for the owner's decision. No permission policy was changed here.

## What each setting really controls

EchoSpeak has five separate gates. The approval mode is only one of them.

| Setting | Question it answers | Where it is decided |
|---|---|---|
| **Assistant mode** (`unified` / `environments`) | Which capability profile a conversation gets | `agent/environments.py` |
| **Project** (attached, trusted) | Where Echo can change files and run commands | `has_trusted_project`; in unified mode it is the switch between Chat and Code |
| **Permission switches** (`allow_file_write`, `allow_terminal_commands`, …) | Which kinds of action exist at all | `config.py`, enforced at the tool boundary |
| **Approval mode** (`smart` / `autonomous` / `always` / `never`) | Which existing actions wait for the owner | `approvals.tool_needs_approval` |
| **Folder access grants** (`read:<folder>`) | Reading outside the conversation's folders | `agent/lean/file_access.py`, asks in every approval mode |
| **Privacy mode** (`PRIVACY_MODE`) | Network and outside services | web tools, providers |

### Approval modes, measured

`apps/backend/scripts/approval_matrix.py` runs the real decision function for representative calls:

| call | always | smart | autonomous | never |
|---|---|---|---|---|
| read a file | runs | runs | runs | runs |
| write / edit a file | ask | runs | runs | runs |
| delete a file | ask | ask | held | runs |
| run `pytest -q` (sandbox) | ask | runs | runs | runs |
| run `ls` on this PC | ask | ask | auto-approved | runs |
| run `rm -rf build` on this PC | ask | ask | held | runs |
| run `rm -rf ~` | ask | ask | held | **ask** |
| send an email | ask | ask | held | runs |
| MCP action | ask | ask | held | runs |
| click on the desktop | ask | ask | auto-approved | runs |
| change its own soul | ask | ask | held | runs |
| open a pull request / ask a remote agent | ask | ask | held | **ask** |
| web search | runs | runs | runs | runs |

"Held" means autonomous mode doesn't wait: the call is parked for the owner, and the run goes on and reports it.

## Where the owner's experience comes from

The owner runs **unified + never**, with every permission switch on. The GTA_CLONE session (24 November) shows four interactions:

1. **No project, so Chat.** A new conversation that had lost the terminal's project (#183, fixed in #184) ran as Chat: no file writes and no commands. Approval mode doesn't apply to tools that aren't offered.
2. **"Never" still asked.** Listing `/home/dev/Work` asked for approval because folder-access grants are a separate gate. In a conversation without a project, the allowed folders are only `~/Documents` and `~/Desktop`.
3. **"Never" is ambiguous.** The terminal says "Never ask: actions run without approval (dangerous)". Short labels elsewhere read "never" and can be taken as "never allowed".
4. **Echo's own questions.** "Should I proceed?" is the model asking, not a gate. Approval mode can't stop it. That is instruction and harness work (workstreams 3 and 6).

## Proposal (for decision)

Separate the axes the owner asked about, without weakening any gate.

| Axis | Proposed control | Change from today |
|---|---|---|
| How independently Echo works | **Autonomy**: *Ask first* (always) · *Ask for risky* (smart, default) · *Work independently* (autonomous) · *Full access* (never) | Rename only. "Full access" says what `never` does. |
| Where it works | **Workspace**: the attached project (Code) or none (Chat) | Show it in the footer and banner. When the owner asks for file or command work without a project, Echo offers to attach the folder in one card instead of producing artifacts. |
| What it can use | Permission switches | Unchanged |
| What it can read beyond the workspace | Folder grants | Decision A below |
| Network and privacy | Privacy mode | Unchanged |
| Profile routing | Assistant mode | Decision B below |

### Decisions needed

- **A. Folder reads under Work independently and Full access.** Today every outside-folder read asks, in every mode.
  - Option 1, keep as is.
  - Option 2, recommended: in those two modes, allow **reading** non-sensitive folders under the home directory without a card, still logged. Sensitive places (credentials, other programs' data) stay hidden whatever the grant. Writing still needs a project.
- **B. Assistant mode `environments`.** Unified is the default, and the terminal and desktop already route by project.
  - Option 1: keep both modes.
  - Option 2, recommended: keep `environments` as a legacy setting, out of the first settings page, with no behavior change.
- **C. Rename `never` to "Full access"** in the terminal and desktop labels. This changes wording only.

None of A–C is implemented. A is a security-boundary change and needs the owner's explicit approval.
