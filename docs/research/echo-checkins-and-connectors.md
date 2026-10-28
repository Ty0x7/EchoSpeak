# Echo checks in, and one-click connectors (11.10.10)

Two changes that make Echo reach further. It can now **come to you** about things that need you, and you can **connect apps in one click**, the reviewed way. [Visual report](../../artifacts/research/echo-checkins-and-connectors.html).

Evidence labels: **Source** (read in the code), **Test** (a deterministic test in the repository), **Live** (checked on the owner's PC or against the real service on 28 Oct 2026), **Owner** (only the installed app can show it).

## 1. Echo checks in

### What was there

A heartbeat (`agent/heartbeat.py`) existed but never ran from the app:

- `_reconcile_heartbeat_runtime` refused to start without `HEARTBEAT_PROJECT_ID` and `HEARTBEAT_SESSION_ID`, which nothing in the app sets. Turning it on in Settings only logged an error. **Source.**
- Its "pulse" saw only todos, git commits and a Twitter queue.
- Every tick was a model call, and its prompt showed up in the chat as a message from the owner.

### The design: two layers over one source of truth

`agent/attention.py` collects what needs the owner from EchoSpeak's own state, with **no model call**. Each source is read independently, so one failing never hides the others (**Test**).

| Source | Items | Urgency |
|---|---|---|
| Approval broker and coordination ledger | approvals and questions waiting | now |
| Coordination works | stopped (unknown), blocked, waiting for input, interrupted; failed in the last 24 h | now / soon |
| Deliveries | a message whose delivery is unknown | soon |
| Routines | coming up within the hour (context only); failed last run | fyi / soon |
| MCP manager, connection registry | sign-in needed, changed launch awaiting review, failing server, unhealthy account | soon |
| Coding agents (`code_accounts.remembered`) | Codex or Claude Code signed out | soon |
| Recent turns | failed with a model or API error (401/403/429, invalid key, quota, can't reach) | soon |

**Layer 1, "What needs me"** (the default). `agent/checkins.py:tick` runs on the routine scheduler's existing tick, next to the delivery outbox upkeep. New items with urgency *now* or *soon* become one message from Echo:

- It's posted with `reminders.post_to_chat` in the **Check-ins with Echo** chat, so the owner can reply and Echo continues like any conversation.
- It also goes out through `automations.deliver`: a desktop notification, plus Discord or Telegram if chosen.

Guards, all **Test**:

- Each item is announced at most once per 12 hours.
- Notices are at least 10 minutes apart, with at most 6 items in one.
- Nothing goes out in quiet hours (local; default 22:00–08:00; start = end turns them off) or while Stop everything is on. Held items go out afterwards.

**Layer 2, "Also friendly check-ins"** (opt-in, model calls). The heartbeat now:

- starts without the two settings, using the check-ins chat
- skips quiet hours and the half hour after the owner's last message, so it never interrupts
- adds the full attention list, upcoming items included, to its pulse
- uses a prompt that asks for a real check-in: what matters, what's coming up, follow-ups, or simply what's up, without inventing facts

Its pulse is Echo's own context. The turn is recorded with `hidden_prompt`, and the timeline's fallback no longer shows a turn's query as the owner's message when that flag is set (**Test**). A silent reply (`NO_HEARTBEAT`) is removed with `StateStore.discard_turn`, which only removes finished turns, along with their items and search entries (**Test**).

### Settings

Settings › Automations › **Echo checks in**:

- **Check in with me:** Off / What needs me / Also friendly check-ins. This writes `checkin_mode` and `heartbeat_enabled` together.
- **Friendly check-in every:** 1 h to once a day.
- **What Echo asks itself:** optional.
- **Quiet hours** and **Also send to.**

The old Heartbeat group is gone.

### Limits

- Unread email and new channel messages aren't sources yet. They need a cheap, privacy-respecting unread check per channel.
- Quiet hours use the PC's local time.
- The friendly layer's quality depends on the chosen model. It's off by default.

## 2. One-click connectors

### What was there

Settings › Connections listed 10 provider cards. All but Obsidian said sign-in "isn't available yet", as letter tiles without logos (**Source**). Agents could already *suggest* MCP connections from 21 setup guides (`agent/integration_guides.py`), approved in Settings. Three hosted guides (Notion, Linear, Atlassian) were marked "can't sign in yet", though `agent/mcp_oauth.py` already does OAuth 2.1 sign-in through the owner's browser.

### Add apps

`agent/connector_gallery.py` and `GET /lean/integrations/gallery` list every guide with:

- its category, publisher, what it needs and the steps
- its live state (connected, starting, signing in, sign-in needed, problem, built in) and tool count

In Settings › Connections › **Add apps** (search and kind-of-work chips), **Connect** shows exactly what's being approved: steps, cautions and required fields. Then `POST /lean/integrations/gallery/{id}/connect` runs the same reviewed path as an agent's suggestion, through the shared `_approve_proposal`:

1. A proposal is made, with the version pinned.
2. The owner's click is the approval.
3. The server is saved through the DPAPI settings path, and its launch is pinned with `mcp_trust.approve`.
4. The server starts.
5. For hosted sign-in servers, the vendor's own sign-in opens in the browser.

**Disconnect** signs out, unpins (new `mcp_trust.forget`), removes the settings entry and stops the server. The connect and disconnect run is **Test**: a real end-to-end run with the local fixture MCP server.

### The 10 new connectors

Each is the vendor's own entry in the official MCP registry (`registry.modelcontextprotocol.io`, filtered to the vendor's namespace), probed live on 28 Oct 2026 with Echo's own HTTP client:

- an unsigned `initialize` received the OAuth challenge (`WWW-Authenticate` with resource metadata)
- the authorization server advertised dynamic client registration, which Echo's sign-in uses

| App | Address | Sign-in |
|---|---|---|
| Todoist | ai.todoist.net/mcp | OAuth |
| Zapier | mcp.zapier.com/api/v1/connect | OAuth |
| Airtable | mcp.airtable.com/mcp | OAuth |
| Canva | mcp.canva.com/mcp | OAuth |
| monday.com | mcp.monday.com/mcp | OAuth |
| Sentry | mcp.sentry.dev/mcp | OAuth |
| Vercel | mcp.vercel.com | OAuth |
| Supabase | mcp.supabase.com/mcp | OAuth; SQL and migrations treated as destructive |
| Hugging Face | huggingface.co/mcp | none needed |
| Context7 | mcp.context7.com/mcp | none needed |

Zoom's hosted servers were dropped: their authorization server doesn't offer client registration, so Echo couldn't sign in. **Live.**

### Logos

`components/brandMarks.ts` holds paths from Simple Icons 16.34.0 (CC0), extracted for the connectors that have one, with no runtime dependency. `BrandMark` draws a mark in its brand color, but near-black marks (GitHub, Notion, Vercel) take the text color so they show in both themes. Brands Simple Icons doesn't carry, mostly removed at the owners' request (Canva, monday.com, Adobe, Slack, Microsoft), keep a letter tile.

### Limits

- Some hosted servers need a paid plan or an admin's approval in their own product. The sign-in page says so.
- Signing in to a connector is the owner's acceptance step (**Owner**). No sign-in was completed during development.
