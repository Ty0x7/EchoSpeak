# EchoSpeak visual release atlas

This collection presents every existing versioned note in `docs/releases/v*.md`
as a complementary HTML report in `artifacts/releases/`. It currently covers
37 documented updates from 10.0.0 through 11.10.5. Earlier release notes remain
unchanged; the new 11.10.5 note records the screenshot-directed Chat, model picker, composer
and voice UI refinements and the separate build/test/publish steps.
The atlas is a source-history presentation, not evidence of installer publication.

Each page includes a curated visual narrative, a conceptual diagram, concise
changes extracted by area, source-note provenance and historical verification
kept separate from newly performed checks. Expandable references use existing
screenshots, with their provenance and version limitations labeled explicitly.
The HTML complements the Markdown instead of reproducing the full technical note.

## Historical distinctions

* 11.8.0 was integrated into 11.9.0 before a separate installer was published.
* The original 11.9.0 handoff/manifest workflow is historical. Later source work
  at 11.9.2 replaced it with direct file discovery and a reviewed documentation
  skill; existing imported records remain available.
* The Windows build workflow introduced in 10.4.0 was subsequently removed.
* No report is invented for the absent 11.9.1 note. 11.9.3 is the current project UI release; the A2A expansion previously associated with that number remains future work.
* README voice/artifact references originate from source commit c8d62b1 on
  2026-10-06; they are not verified exact-version installer captures.
* Current workspace screenshots show the isolated real-repository self-test
  during later 11.9.2 source work, not an installed-app or live-agent test.

## Workspace discovery

The matching paths `artifacts/releases/vX.Y.Z.html` and
`docs/releases/vX.Y.Z.md` pair automatically in the Work gallery. The atlas
index similarly pairs with this document. Project-file HTML previews disable
non-fragment navigation, so use the Work gallery to choose another report and
Technical notes to open its Markdown. Standalone reports support previous/next
and atlas navigation. No manifest, notification or model call is required.

## Source inventory

| Version | Focus | Markdown | HTML | Notes revision |
| --- | --- | --- | --- | --- |
| 10.0.0 | One agent foundation | [10.0.0](v10.0.0.md) | [Report](../../artifacts/releases/v10.0.0.html) | `61d3940` |
| 10.0.1 | Answers become usable outputs | [10.0.1](v10.0.1.md) | [Report](../../artifacts/releases/v10.0.1.html) | `020c879` |
| 10.0.2 | Pages and panels find their place | [10.0.2](v10.0.2.md) | [Report](../../artifacts/releases/v10.0.2.html) | `22db379` |
| 10.0.3 | Local model selection connects properly | [10.0.3](v10.0.3.md) | [Report](../../artifacts/releases/v10.0.3.html) | `0ada0ef` |
| 10.1.0 | Group work follows through | [10.1.0](v10.1.0.md) | [Report](../../artifacts/releases/v10.1.0.html) | `2b9efe3` |
| 10.2.0 | A smaller, leaner application | [10.2.0](v10.2.0.md) | [Report](../../artifacts/releases/v10.2.0.html) | `1c1a70e` |
| 10.3.0 | Create, research and get started | [10.3.0](v10.3.0.md) | [Report](../../artifacts/releases/v10.3.0.html) | `15710a4` |
| 10.3.1 | Four cloud providers, live catalogs | [10.3.1](v10.3.1.md) | [Report](../../artifacts/releases/v10.3.1.html) | `7ae73ad` |
| 10.3.2 | Move around without losing the run | [10.3.2](v10.3.2.md) | [Report](../../artifacts/releases/v10.3.2.html) | `6856ffe` |
| 10.4.0 | Recover work and follow the evidence | [10.4.0](v10.4.0.md) | [Report](../../artifacts/releases/v10.4.0.html) | `a4d7bdc` |
| 10.4.1 | Valid cloud tool continuations | [10.4.1](v10.4.1.md) | [Report](../../artifacts/releases/v10.4.1.html) | `032f313` |
| 10.4.2 | Chat actions and full conversation voice | [10.4.2](v10.4.2.md) | [Report](../../artifacts/releases/v10.4.2.html) | `22bc719` |
| 10.4.3 | Cleaner Windows distribution | [10.4.3](v10.4.3.md) | [Report](../../artifacts/releases/v10.4.3.html) | `648e987` |
| 10.5.0 | Quieter lists, clearer settings | [10.5.0](v10.5.0.md) | [Report](../../artifacts/releases/v10.5.0.html) | `62dad1f` |
| 11.0.0 | Learning from checked experience | [11.0.0](v11.0.0.md) | [Report](../../artifacts/releases/v11.0.0.html) | `1f51f15` |
| 11.1.0 | A shared light and dark theme | [11.1.0](v11.1.0.md) | [Report](../../artifacts/releases/v11.1.0.html) | `9b2dfce` |
| 11.1.1 | Remove stale backend files on update | [11.1.1](v11.1.1.md) | [Report](../../artifacts/releases/v11.1.1.html) | `275f5d0` |
| 11.1.2 | More room for conversation | [11.1.2](v11.1.2.md) | [Report](../../artifacts/releases/v11.1.2.html) | `dd7918b` |
| 11.2.0 | Useful memory and a real artifact library | [11.2.0](v11.2.0.md) | [Report](../../artifacts/releases/v11.2.0.html) | `a001a7f` |
| 11.3.0 | A polished composer and Git-aware work | [11.3.0](v11.3.0.md) | [Report](../../artifacts/releases/v11.3.0.html) | `39b2289` |
| 11.4.0 | Visible progress and inspected sources | [11.4.0](v11.4.0.md) | [Report](../../artifacts/releases/v11.4.0.html) | `47fa1fd` |
| 11.4.1 | Search dependencies reach the installed app | [11.4.1](v11.4.1.md) | [Report](../../artifacts/releases/v11.4.1.html) | `f17a25a` |
| 11.5.0 | Ask, approve and stay in control | [11.5.0](v11.5.0.md) | [Report](../../artifacts/releases/v11.5.0.html) | `4f7759d` |
| 11.5.1 | Harden the existing trust boundaries | [11.5.1](v11.5.1.md) | [Report](../../artifacts/releases/v11.5.1.html) | `66d8d1a` |
| 11.6.0 | Discover and approve new connections | [11.6.0](v11.6.0.md) | [Report](../../artifacts/releases/v11.6.0.html) | `216f7a2` |
| 11.7.0 | Enforced privacy and optional model choice | [11.7.0](v11.7.0.md) | [Report](../../artifacts/releases/v11.7.0.html) | `e4d6c15` |
| 11.7.1 | Screenshots the model can use | [11.7.1](v11.7.1.md) | [Report](../../artifacts/releases/v11.7.1.html) | `b6b9f99` |
| 11.8.0 | A stronger native harness | [11.8.0](v11.8.0.md) | [Report](../../artifacts/releases/v11.8.0.html) | `55ff0fc` |
| 11.9.0 | Visual project work and integrated 11.8 | [11.9.0](v11.9.0.md) | [Report](../../artifacts/releases/v11.9.0.html) | `55ff0fc` |
| 11.9.2 | Optional local speech and consistent setup | [11.9.2](v11.9.2.md) | [Report](../../artifacts/releases/v11.9.2.html) | `210fe72` |
| 11.9.3 | Your project work, clearly presented | [11.9.3](v11.9.3.md) | [Report](../../artifacts/releases/v11.9.3.html) | `0161d14` |
| 11.10.0 | Echo knows what it can do, and keeps working | [11.10.0](v11.10.0.md) | [Report](../../artifacts/releases/v11.10.0.html) | `b272112` |
| 11.10.1 | Echo finds things better, and can't fake a result | [11.10.1](v11.10.1.md) | [Report](../../artifacts/releases/v11.10.1.html) | `cd2814e` |
| 11.10.2 | Echo can remind you, even from the tray | [11.10.2](v11.10.2.md) | [Report](../../artifacts/releases/v11.10.2.html) | `b3661e1` (not published; in 11.10.3) |
| 11.10.3 | Echo notices when it's stuck on the screen | [11.10.3](v11.10.3.md) | [Report](../../artifacts/releases/v11.10.3.html) | Current source |

## Verification scope

The collection is checked for one report per canonical version note, unchanged
note hashes, valid local links/media, absence of external assets/scripts, correct
workspace pairing and readable responsive rendering. Existing release test
counts are historical and are not claimed as rerun for this documentation work.
No application code, runtime configuration, installer version, user data or
active application session is changed by generating the reports themselves.

The real-repository check found that the existing cold scan could exhaust its
350 ms enumeration budget before visiting nested reports. The accompanying
discovery fix uses cached directory-entry metadata and finishes prioritized
document trees before source trees. Every queued folder and every content read
still passes path validation; symlinks and Windows reparse points are excluded.
The original depth, entry, document and time limits are unchanged.

Validation on 2026-10-19: all 30 version reports and the overview were discovered
and paired with the correct Markdown. All original version-note hashes remained
unchanged. Local links and media resolved, no scripts or external assets were
present, and 62 dark-desktop/light-mobile renders passed without overflow,
browser exceptions or external requests. The focused workspace suite passed
41 tests with one Windows symlink test skipped. Installed-app and real-provider
behavior were not exercised by this documentation check.

The final visual pass keeps compact headings, uses editorial release rows in
the overview, softens diagram surfaces and removes unnecessary boxes around
change lists. Historical context folds by default. The same 62 responsive
previews passed again after this styling-only refinement; no new product
screenshots or application behavior changes were needed.

## 11.9.3 continuation

The current workspace report and a new [11.9.3 visual report](../../artifacts/releases/v11.9.3.html)
cover HTML-only browsing, widget/list mode, stable preview revisions, project
identity editing and project-specific documentation styling. All historical HTML
pages were reviewed for local links/media, sandbox compatibility and responsive
layout. Prior version narratives remain historical; their features are not
silently re-described as the current design. 11.9.2 links forward to 11.9.3.

11.9.3 validation: all 31 version reports plus the atlas passed 64 responsive
renders at 1280 dark and 390 light, with no overflow, browser exceptions or
external requests. File pairs, local media and links resolved. Original older
version-note hashes remain unchanged. The current workspace report also opened
with Technical notes through actual repository discovery in the isolated UI.
