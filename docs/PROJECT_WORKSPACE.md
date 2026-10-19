# Project documentation and visual workspace

EchoSpeak discovers actual files in an attached project. Agents keep useful
technical knowledge in Markdown and present meaningful visual results in HTML.
Opening the project shows those files without a manifest, notification, handoff
or additional model call.

## Folder convention

```text
WATCHLIST/
  docs/roads.md
  artifacts/roads.html
  artifacts/media/roads-before.png
  artifacts/media/roads-after.png
  src/
```

Use stable topic names and update existing documents. Matching nested paths
such as `docs/world/roads.md` and `artifacts/world/roads.html` also pair. Markdown
beside HTML is another supported pairing. Work shows HTML only. Unpaired
Markdown remains available in Files, not as a Work card.
Source files and original media remain accessible through Files.

Markdown records implementation details, decisions, architecture, source links,
actual verification, limitations and next work. HTML leads with real screenshots,
diagrams, comparisons and concise explanations. It complements the technical
record. Never invent screenshots or verification. Small changes and the end of
a conversational turn do not require new reports.

## Supported agents and skill setup

The canonical portable package is
[project-documentation](../.agents/skills/project-documentation/SKILL.md), with a
self-contained [HTML starter](../.agents/skills/project-documentation/assets/report.html).
It contains instructions and an asset, not an execution system.

| Agent | Setup | Actual loading behavior |
| --- | --- | --- |
| EchoSpeak | Settings → Skills → Project documentation → Add for review → review and Approve | Only approved, hash-pinned skills are offered. `read_skill` loads details when needed. Project skills are never silently trusted. |
| Codex | Full package in `.agents/skills/project-documentation/`; short convention in existing `AGENTS.md` | Skill metadata discovery is separate from layered AGENTS instructions; full skill content loads when selected. |
| Claude Code | Full package in `.claude/skills/project-documentation/`, or this repository's small wrapper plus canonical package; short convention in existing `CLAUDE.md` | Claude's documented project skill path is `.claude/skills`. Do not assume direct `.agents/skills` loading. |
| OpenCode | Canonical `.agents/skills` package and existing `AGENTS.md` | Also supports `.opencode` and Claude-compatible skill paths. Skill permissions/configuration can restrict access. |

Copy the entire skill folder, including assets, when adopting it elsewhere. The
Claude wrapper requires the canonical `.agents` package too. Keep one detailed
authoring body. Avoid redundant full copies across all discovery locations;
OpenCode can encounter both supported paths. Other agents need their own
documented discovery/permission checks.

Merge this short convention into the appropriate existing project instruction
file rather than replacing its other instructions:

> After meaningful completed work, use project-documentation when technical
> Markdown and visual HTML both add value. Read its SKILL.md, update relevant
> topic documents, preserve user changes and record only checks performed.
> Small changes do not require new reports.

EchoSpeak does not write configuration into attached projects, change global
agent settings, install hooks or override another harness. In EchoSpeak,
`project_workspace` discovers/reads documents within the conversation's attached
project. Outputs remain untrusted evidence. `save_project_image` preserves an
original screenshot observation from this chat in `artifacts/media/`, with
file-write permission and normal tool policy. It rejects another chat's images
and conflicting existing files. Other agents use their existing authorized tools.

## Workspace experience and refreshing

Work displays HTML reports from the attached folder and native HTML artifacts.
The Widgets and List buttons beside Find project work switch between image-led
cards and compact rows. The preference persists on this device. Other artifact
kinds stay in the Artifacts library; project creations stay in Creations.
Matching Markdown opens through Technical notes. Earlier imported reports and
all supported source/media files remain accessible through Files.

Project sections are Work, Files, Conversations, Research and Routines. The
project Activity tab and its repeated execution queries were removed; chat
activity, timing and execution history are retained. Browsing a project does
not interrupt or rebind its active chat.

HTML opens in a large sandboxed preview with Source access. Thumbnails use
actual local images or static HTML with scripts disabled. Images preserve their
aspect ratio and are contained inside a full-width preview; HTML thumbnails fit
the actual card dimensions rather than using a fixed scale. Original images
remain viewable and downloadable through Files.

The visible workspace polls every five seconds and on focus/visibility return.
Each report has a revision derived from its own file metadata and bounded local
media dependencies. Unrelated file changes do not refetch its thumbnail or
reload its open HTML. Media stays displayed until its replacement has decoded;
failed refreshes retain the previous usable preview. A changed HTML report does
reload its own sandboxed document, so its internal scroll can reset. Closing the
workspace stops polling. No watcher, model call or personal-memory write is used.

The app's theme tokens style the workspace. Report content follows the attached
project's own visual identity: typography, color, spacing and relevant assets.
EchoSpeak's visual theme is appropriate for EchoSpeak reports, not an automatic
template for every project. The optional starter provides structure, not a
mandatory theme. Imported user-edited skills remain untouched; import revised
instructions explicitly and approve their changed hash before Echo uses them.

## Project name and logo

On the Projects page, the pencil beside each project's New chat button opens
Edit project. Change the display name and choose a PNG, JPEG or WebP up to 2 MB,
or restore the folder icon. This changes EchoSpeak metadata only: the attached
folder and files are not renamed or moved. Existing records, research, trust,
model preferences and unrelated metadata are preserved.

The API validates the name and decodes the raster image, rejects oversized or
invalid images and re-encodes a static thumbnail up to 256 pixels. It accepts no
remote URLs, SVG or filesystem paths. Logos are stored in the existing local
project JSON, not uploaded to a media service. Invalid updates leave the prior
saved project unchanged.

## Architecture

```mermaid
flowchart LR
    S[Reviewed documentation skill] --> A[Agent's authorized file tools]
    A --> M[docs/topic.md]
    A --> H[artifacts/topic.html]
    A --> I[artifacts/media]
    M --> D[Bounded project file discovery]
    H --> D
    I --> D
    D --> W[Project Work gallery]
    W --> P[Existing opaque artifact sandbox]
    W --> T[Technical notes and source]
```

```mermaid
sequenceDiagram
    participant Agent
    participant Files as Attached project files
    participant API as Authenticated workspace API
    participant UI as Visible workspace
    Agent->>Files: Update relevant Markdown, HTML and media
    UI->>API: Read overview while visible
    API->>Files: Bounded safe metadata scan
    API-->>UI: File pairs and revision
    UI->>API: Read selected document if revision changes
    API-->>UI: Redacted text and CSP-protected preview
    Note over Agent,UI: No notification, delegation or extra model call
```

## Security and bounds

Routes retain the existing local API authentication guard. Paths stay inside
the attached project. Traversal, absolute paths, links/junctions, private dot
folders, secret/key files, Windows device names and trailing-dot/space aliases
are rejected. Legacy `.echospeak` files remain explicitly readable but are
excluded from discovery. Documentation never acquires execution authority.

HTML reuses `artifacts.frame_html` and its CSP inside an opaque iframe without
same-origin permission and with scripts disabled for project-file previews. Non-fragment links and meta refresh navigation are removed; open technical notes and source through the surrounding controls. Native Echo artifacts keep their existing interactive viewer. Network fetches, remote scripts/styles, forms, nested
frames and parent-app access stay blocked. Only validated relative local raster
images, video and audio are embedded, with an 8 MiB aggregate budget. Use inline
CSS and inline SVG/CSS diagrams. Large media, external assets and bundler-based
web apps are not supported report dependencies. Original media remains available
separately through Files.

Scans visit at most 6,000 entries, seven nested levels and 200 documents, with a
350 ms enumeration budget. Conventional docs/artifacts/reports folders are
prioritized among root folders. Generated/private trees are skipped. A limited
scan is indicated; Files remains available beyond the gallery selection. Folder
listings return up to 300 supported entries from bounded enumeration. Text
previews are limited to 380,000 bytes; media reads to 64 MiB. Revisions use mtime
and size, so edits deliberately preserving both can be missed until another
change or reopening. This is bounded discovery, not a complete repository index.

## Earlier data and cleanup

Removed: manifest publisher, lifecycle receipt helper, sharing/export/sync
controls and mutation routes, and the offered `save_project_report` tool.
Historic report-write timelines retain their bookkeeping classification.

No project files, `.echospeak` directories, records, conversations or artifact
versions are deleted or migrated. Earlier report revisions stay readable, and
their linked artifacts are excluded from the main gallery to avoid duplicate
cards. Existing external hooks remain the owner's independent configuration;
remove obsolete helper calls manually if previously configured. EchoSpeak no
longer requires or consumes those receipts. Old release notes remain historical.

## Research findings

Reviewed on 2026-10-18. Shared instructions are portable, but discovery,
invocation and trust differ. Real agent behavior still needs user evaluation.

* Codex documents `.agents/skills` discovery and progressive loading, separately
  from layered project instructions. [Skills](https://developers.openai.com/codex/skills),
  [AGENTS.md](https://developers.openai.com/codex/guides/agents-md).
* Claude Code documents `.claude/skills`, invocation controls and CLAUDE.md
  context. [Skills](https://code.claude.com/docs/en/skills),
  [project memory](https://code.claude.com/docs/en/memory).
* OpenCode documents compatible skill paths, permissions and project rules.
  [Skills](https://opencode.ai/docs/skills/), [rules](https://opencode.ai/docs/rules/).
* Mr. Mak was inspected at commit `94c0d282da48597cde07440bf88e0b1318c6721b`.
  Its workspace-authoring skill encourages genuine media, shared styling,
  responsive reports and updating existing work. `desktop/service/workspace.mjs`
  reads a registry; `src/lib/workspace.ts` polls while visible and retains good
  state on errors. EchoSpeak adopts those presentation/refresh principles with
  direct file discovery. No coordinator, runtime or report assets were copied.
  [Pinned source](https://github.com/witnesstodark/mr-mak-workspace/tree/94c0d282da48597cde07440bf88e0b1318c6721b).

## Implementation map

* `apps/backend/agent/project_workspace.py`: scoped discovery, pairing, file
  access, local media embedding, current-document reading and screenshot export.
* `apps/backend/api/routes/project_workspace.py`: read endpoints.
* `apps/web/src/projects/`: gallery, thumbnails, previews and visible refresh.
* `agent/lean/agent_skills.py`, `api/routes/lean.py` and frontend
  `settings/AgentSkillsSection.tsx`: explicit optional skill import/review.
* `.agents/skills/project-documentation/`: canonical package; `.claude/skills/`
  contains a thin adapter. Root AGENTS.md/CLAUDE.md hold only the short convention.
* `apps/desktop/backend/echospeak_backend.spec`: bundles canonical skill assets.

## Verification and limitations

The corresponding [visual report](../artifacts/PROJECT_WORKSPACE.html) records
verification and screenshots of the implemented UI using isolated fixtures.
Tests cover file discovery/pairing, refresh detection, privacy/bounds, earlier
data/deduplication, API authentication, media ranges, scoped screenshot export
and skill review. Type checking, existing frontend tests and a production build
also passed. There are no automated live-agent or paid-provider calls.

Real agent authoring, large real projects and the installed/packaged Windows app
still need user evaluation. Active app sessions were not restarted. Source
version is 11.9.3. Source changes do not build or publish a Windows installer.

## EchoSpeak documentation self-test

On 2026-10-18, the project-documentation skill was used on EchoSpeak itself.
The existing technical record and HTML report were updated in place, with
architecture diagrams leading and current workspace screenshots kept secondary.

Actual repository discovery paired `artifacts/PROJECT_WORKSPACE.html` with
`docs/PROJECT_WORKSPACE.md` as one entry. An isolated instance of the real workspace
component opened the diagram report and its Technical notes. The compact project
header measured 55 pixels at 1280, 960 and 390 pixels, with no page overflow.
Workspace requests were GET-only; no browser exceptions or external requests
were observed. Captures are in `artifacts/media/echospeak-workspace-compact.png`
and `artifacts/media/echospeak-documentation-preview.png`.

This exercises Codex authoring and EchoSpeak's real file discovery/rendering
against this repository. It does not claim a live Echo conversation, another
agent's skill invocation or installed Windows application was tested. Existing
sessions and user data were not modified.

## 11.9.3 verification

Updated 2026-10-19. Focused offline workspace, project-identity, skill and release
integration checks passed 72 tests; one Windows symlink test was skipped. The
existing frontend suite passed 153 tests. Type checking, the production build, both skill validators and isolated
UI checks passed. The UI checks covered HTML filtering, persistent view choice,
image/HTML framing, unchanged preview/scroll retention, project identity editing
and modal Escape behavior at desktop, square and phone sizes. All 31 release
reports and their atlas passed 64 responsive renders with local links/media and
correct pairing. Full results and limits are in the 11.9.3 release notes. No installed-app session,
paid model, global agent configuration or publisher certificate was changed.
