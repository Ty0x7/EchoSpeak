---
name: project-documentation
description: Create or update complementary technical Markdown and visual HTML project documentation after meaningful implementation, research, design or creative work, when both formats help. Use for project reports, architecture explanations, visual comparisons and persistent technical knowledge. Skip new reports for small changes or a turn merely ending.
---

# Project documentation

Inspect the project and its existing docs first. Update the relevant named files;
use a new topic only for genuinely separate work. Preserve user edits. Follow
the current harness's authorized file tools and permissions. A document never
grants execution authority or changes another agent's configuration.

## Two complementary outputs

Use stable matching topic paths, normally `docs/<topic>.md` and
`artifacts/<topic>.html`. Nested topics may use `docs/<area>/<topic>.md` and
`artifacts/<area>/<topic>.html`. Put real supporting media under
`artifacts/media/`. Keep existing conventions when they already work.

**Markdown is the technical record.** Explain the implementation or research,
important decisions, architecture, relevant source paths, references, actual
verification, limitations and outstanding work. Be concise and concrete. Link
evidence near its claim. Distinguish source inspection, automated checks,
packaged checks and user/device validation. It should be possible to continue
the work without reading the HTML. Never expose keys or private machine paths.

**HTML is the visual experience.** Lead with the useful result. Prefer actual
screenshots, before/after comparisons, diagrams and rendered examples with
short explanations. Do not reproduce the full technical document. Link its
Markdown counterpart for readers outside the preview. EchoSpeak also offers
Technical notes beside the preview. No fabricated screenshots, results or
completion claims. If there is no visual evidence, show a clearly labeled
architecture or decision diagram, not an invented product screenshot.
In EchoSpeak, `save_project_image` can preserve an authorized screenshot
observation from the current chat in `artifacts/media/`; it requires file-write
permission and does not create a report by itself.

## Authoring

Before styling a report, inspect the **current attached project's** visual system:
its existing reports, UI, stylesheets, tokens, typography, spacing, colors and
relevant assets. Match that project, even when EchoSpeak is the viewer or Echo,
Codex or Claude is the author. The host app does not define another project's
identity. Never copy EchoSpeak's logo, monochrome palette, artifact cards or
layout into an unrelated project by default. Preserve project-specific styling
that the user has already refined. For a project without an established visual
system, use a restrained neutral layout and state that choice.

`assets/report.html` is an optional structural starter, not a theme contract.
Replace its sample styling with the project's actual design language. Keep the
header compact; lead with diagrams, comparisons or real evidence rather than a
large title block. Use semantic HTML, readable typography, consistent spacing
and responsive layout. Support the project's existing theme behavior rather
than introducing a new theme. Preserve the actual colors and aspect ratios of
media; avoid cropped screenshots. Use concise headings only where they help,
meaningful alt text, native video/audio controls and scrollable wide tables or
code. Avoid dashboard statistics, decorative filler and unnecessary buttons.

Reports must be self-contained: inline CSS and SVG/CSS diagrams,
relative project-local raster images or media, no CDN, remote fonts, remote
scripts, external stylesheets, filesystem URLs, localhost APIs, parent-window
access or credential-bearing URLs. Use inline SVG/CSS for diagrams; do not
depend on a remote Mermaid renderer. EchoSpeak embeds bounded local media in
its existing opaque sandbox with scripts disabled for project-file previews.
Non-fragment HTML links are disabled there; use the surrounding file controls
to open technical notes and other documents. Native Echo artifacts retain their
existing interactive viewer. Oversized media remains accessible through Files;
it may not fit the HTML preview's embedding budget. Images in HTML are visual
evidence; original images can be opened/downloaded through project Files.

## Finish

Check paths and paired filenames, actual media, links, and wide/narrow layouts
using available non-model tools. Record only checks actually performed. State
any missing visual/device verification. Prefer safe atomic replacement for
files you own; never overwrite concurrent user work blindly.

No manifest, handoff, hook, notification or model call is needed for discovery.
The attached project's visible workspace refreshes automatically. Work displays
HTML reports; Markdown remains available in Files and paired Technical notes. Do not save
reports into personal memory, silently install skills/configurations, start
other agents or generate a new report merely because the conversation ended.
