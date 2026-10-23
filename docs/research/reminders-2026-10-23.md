# Reminders, scheduling and the system tray (11.10.2)

**Why this was built.** In a live test, Echo was asked "remind me on Sunday about the Raptors and Timberwolves". It correctly said it couldn't:
- EchoSpeak had a scheduler (Routines), but no tool for an agent to create one.
- Routines only repeated on cron schedules.
- Results landed silently in a new chat.
- Nothing ran once the window was closed.

This release adds reminders on the existing Routines infrastructure, Windows notifications, and an optional system-tray mode. It follows the design in the 2026-10-22 research.

**Visual report:** [artifacts/research/reminders-2026-10-23.html](../../artifacts/research/reminders-2026-10-23.html).

## How it works

```
"remind me Sunday at 4 about the game"
  -> Echo calls set_reminder(message, when="2026-10-25T16:00")     agent/reminders.py
     (the current date, time and zone are already in its prompt; the result says the time back in words)
  -> a Routine: metadata.kind="reminder"                           agent/routines.py
       one-time:  trigger_type="once", run_at, next_run=run_at
       repeating: trigger_type="schedule", cron from the first time (daily, weekdays, weekly, monthly)
       plain:     action_type="remind"   (no model call)
       with task: action_type="query"    (runs the agent, unattended)
  -> the existing scheduler thread checks every 60 s                RoutineManager.start_scheduler
  -> automations.routine_runner -> reminders.fire()                  plain reminders
       posts "⏰ Reminder: …" into the chat it was set in            reminders.post_to_chat
       delivers to the routine's channels                           automations.deliver
         desktop  -> agent/notifications.py feed
         discord / telegram -> the owner, under the outbound limits
  -> the desktop shell claims the feed every 5 s                    src-tauri/src/tray.rs
       and shows each item as a Windows notification
  -> the open window reads the feed every 20 s and refreshes that chat   web/src/app/reminderFeed.ts
```

### Decisions

- **Reuse, don't duplicate.** A reminder is a Routine with two small additions: the `once` trigger with `run_at`, and the `remind` action. The scheduler, persistence (one JSON per routine), the Routines page, Stop everything and the delivery channels are unchanged. A fired one-time reminder is disabled rather than deleted, so the Routines page shows it as "Once · done".
- **No model call for plain reminders.** They post their own text. A reminder still arrives when LM Studio is closed or the model is offline. Only a reminder with a `task` runs the agent, as `source="routine"`, so approval-gated actions are skipped and reported.
- **The reminder goes to the chat it was set in.** If that chat was deleted, it goes to a "Reminders" chat.
- **Never interrupts a reply.** Saving a message creates a turn, and a new turn supersedes one in progress. A reminder that comes due while its chat is mid-reply returns `deferred`; the scheduler leaves it due and retries on the next 60-second check.
- **Stop everything holds reminders.** Plain and task reminders that are due while paused are deferred, not lost. They fire after you resume.
- **Late reminders say so.** If EchoSpeak was closed at the due time, the scheduler fires the reminder at the next start (its `next_run` is in the past). It adds "This was due …; EchoSpeak wasn't running then."
- **Times are exact and said back.** `set_reminder` takes ISO local time (`2026-10-25T16:00`), a date alone (9:00), a clock time (`16:30`, the next occurrence), or `in_minutes`.
  - A past one-time time is refused with the current time in the error.
  - The tool description gives the defaults: 9:00 for a day; an hour before an event with a known start.
  - The result carries an execution-evidence effect (`{"kind":"reminder", …}`) and repeats the time in words, so the reply states what was actually saved.
- **Safety.**
  - The tools are for owners only, in every source except routine and heartbeat runs, so a routine can't schedule more routines.
  - Creating, listing and cancelling need no approval: they are local and the owner's own.
  - Under the Rule of Two, a reminder with a `task`, set in a turn that read outside content, asks first. It is refused where nobody can approve. A web page can't plant a task that runs later on its own.
- **Channels.** The defaults are this chat plus a Windows notification. A reminder set from a Discord DM or Telegram also goes back there. Discord and Telegram are used only when they're set up; otherwise the result says so.

## The system tray

**Setting:** Settings › General › **Keep running in the tray when closed**, stored as `desktop_close_to_tray`. It is off by default and shown only in the desktop app.

**When it's on:**
- **Closing hides, not quits.** The window's close button, Alt+F4 and the taskbar's Close all hide every EchoSpeak window instead of quitting.
  - The custom close button goes through `control_desktop_window("close")`.
  - The OS close goes through `WindowEvent::CloseRequested` with `prevent_close`.
- **The tray icon.** EchoSpeak appears in the tray (under Show hidden icons):
  - **Left click** reopens the window.
  - **Right-click menu:** "Open EchoSpeak" and "Quit EchoSpeak".
- **Quitting for real.** "Quit EchoSpeak" calls `app.exit(0)`. The existing exit handler then stops the local service.
- **Launching again** (single instance) shows the hidden window.
- **While hidden,** the local service, scheduler and notification poller keep running, so reminders fire and notify.

**How the shell knows the setting.** The same 5-second claim (`POST /lean/notifications/claim`) returns `close_to_tray`, so the desktop shell follows the setting within seconds. That leaves one source of truth, and the renderer needs no new permission.
- **Turning it off** removes the tray icon. If the window was hidden at the time, the window is shown, so it can't be lost.
- **A failed tray icon** turns close-to-tray back off, so the window can't be hidden with no way back.

**Why the shell polls, not the webview.** A Rust thread polls the local service over loopback with the per-launch key, the same pattern as the existing readiness checks. Chromium throttles timers in hidden pages, and a renderer crash would otherwise stop notifications. The notification uses `tauri-plugin-notification` 2.4.0, the newest version that supports the pinned Tauri 2.11.5. It is called from Rust, so the renderer's capability is unchanged.

## Files

- **Backend:**
  - `agent/reminders.py` (new)
  - `agent/notifications.py` (new)
  - `agent/routines.py` (`once`, `run_at`, deferred runs)
  - `agent/lean/automations.py` (`remind` action, desktop channel, deferral)
  - `agent/lean/runtime.py` (tools for owners)
  - `agent/lean/toolbox.py` (core toolset)
  - `agent/lean/capabilities.py` (the agent knows it can)
  - `agent/lean/policy.py` (Rule of Two for task reminders)
  - `api/routes/lean.py` (`/lean/notifications`, `/lean/notifications/claim`, reminder fields)
  - `config.py` (`desktop_close_to_tray`)
- **Desktop:**
  - `src-tauri/src/tray.rs` (new)
  - `src-tauri/src/lib.rs` (close handling, plugin, poller)
  - `Cargo.toml` (`tray-icon` feature, notification plugin)
- **Web:**
  - `settings/SettingsPanel.tsx` (the tray setting; reminders on the Routines page with Cancel)
  - `app/reminderFeed.ts` and `index.tsx` (refresh the chat; browser notifications outside the desktop app)

## Verification

- `tests/test_reminders.py`, 26 tests:
  - time parsing, including a past time refused;
  - cron per repeat, and the time in words;
  - one-time, repeating, `in_minutes`, task, unconfigured channels, list and cancel;
  - a due reminder posting into its chat with no model call (the agent factory raises if called), then disabled, with one notification claimed once;
  - late reminders;
  - deferral while the chat is mid-reply, and under Stop everything;
  - a deleted chat;
  - the Rule of Two for task reminders;
  - the capability report;
  - the claim endpoint and its tray flag.
- `src-tauri` `cargo check`, plus Rust tests for the claim request and the response parsing against a local mock server.
- Web typecheck, plus tests for the feed (no old pop-ups on open, refresh of the open chat, notifications only outside the desktop app) and the Routines labels.
- Full suites: see the release notes.
- **Not verified here:** a packaged build with a real tray icon and a real Windows notification. Windows shows notifications only for installed apps; in development they appear under PowerShell's name.

## Limitations

- **The app must be running.** Reminders fire only while EchoSpeak runs. With the tray setting on, closing the window doesn't stop it. Shutting down or signing out does, and there is no start-with-Windows yet. A missed reminder arrives, marked late, at the next start.
- **Up to a minute late.** The scheduler checks every 60 seconds, and the desktop notification follows within about 5 seconds.
- **Clicking a notification doesn't open its chat.** On Windows, notification actions aren't available through the plugin. Use the tray icon; the reminder is in its chat.
- **Times are local.** A reminder set before a time-zone change fires at the stored moment (it keeps its UTC offset).
- **Editing.** Reminders can't be edited on the Routines page (only cancelled). Ask Echo to cancel and set a new one.
