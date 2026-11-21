# EchoSpeak owner conversation audit · 20 November 2026

The installed 11.15.0 conversation exposed a memory write that did not happen, a stored message that stopped at 4,000 characters, and tool evidence that was missing from the execution ledger. The candidate fixes the structured intake and receipts, preserves new full-length messages, and keeps tool evidence tied to the correct turn. The installation and historical user data were left in place. [Issue #135](https://github.com/Ty0x7/EchoSpeak/issues/135)

## Evidence and method

The audit read the four owner turns from the SQLite execution and message records, the reconnect stream journal, four Lean traces, and the canonical memory file. The source under test was installed 11.15.0 at `fb0849b`, unified Echo with local Ollama `qwen3.5:9b` and a 16,384-token window. The latest autonomous reminder was a separate execution. The raw messages, trace IDs and memory contents stay in a private local evidence directory. The table below reconstructs all four turns from the available records.

| Turn | Owner intent and visible result | What execution and storage show |
| --- | --- | --- |
| 1. Greeting | Echo used the owner's name. | An existing account memory supplied the name. No memory tool call or new record was needed. One model call used 7,846 prompt tokens. |
| 2. Offer to send a list | The owner said the next prompt would contain facts to remember. Echo acknowledged three times. | Two promise retries treated waiting as unfinished work. No list was present, and no memory write occurred. The turn stopped as `promise_unfulfilled` while the execution reported success. Three calls used 23,944 prompt tokens in total. |
| 3. Backup status | The next message was a 486-character operational note. Echo said it saved the important details. | Two unrequested upstream pulls/releases fetches returned 404. After a reasoning-only response recovered, `memory_save` wrote a single backup-status record. The account contained two active records, including the pre-existing name. No evidence says whether the owner intended the intervening note as part of the list. |
| 4. Numbered profile | The profile covered personal setup and work preferences. Echo diverted to a hardware menu, then said it had noted the specs and updated the backup status. | Two more release fetches returned 404. A reasoning-only response was retried. A memory repair chose the old backup key and passed the same fact back to the store. Its ID and source execution still belonged to turn 3. No profile facts were written. The stale readback was labeled a fresh save. A file-related phrase in the profile also triggered a missing-file notice. The four model calls used 52,718 prompt tokens in total. |

The fourth durable user item and its execution preview both ended mid-word at exactly 4,000 characters. The runtime passed the current request into the model separately from the preview. Trace counters show a peak of 13,674 prompt tokens and 14,456 combined tokens and no recorded context overflow or compaction during the turn. The traces contain no request body for recovering the absent transcript tail. No memory recall or correction of the profile was demonstrated in the original conversation.

The stream traces show four fetches across turns 3 and 4. SQLite contains only the first two fetch tool runs. The second turn reused their fixed IDs, and the store kept the first terminal records. The execution-level tool lists were empty. The hardware claims had no lookup or other independent evidence in the turn. The profile's text and any hypotheses in the reply are not verified hardware facts.

## Changes and boundaries in the first candidate

- Echo recognizes an explicit offer to send a list and gives one acknowledgement. The staged offer belongs to that owner conversation. A short backup-status paste may intervene once. Each transition is bounded to 30 minutes. A new thread, cancellation or other intervening prompt clears the staged path. An explicit `Remember this list:` also works in a direct owner conversation.
- Recognized short facts enter the existing `memory_save` tool, with its scope checks, secret filters, tool policy and disk readback. The receipt reports new records, existing records, failed verification and unsupported lines. Inputs within a fenced block stay in the conversation. An exact save of the same key and value keeps its original provenance and produces no new save event. Contradictory values for a single key are left for clarification. A batch attempts at most 24 entries.
- Recalled-fact repair checks change cues against related text in the same clause and skips structured lists. An identical readback cannot back an update claim. A same-turn action that remains a promise after bounded retries now fails with one visible result.
- The user-message item holds the complete incoming message. The 4,000-character execution preview remains available for summary callers. Release prefetch requires an explicit update request. Prefetched tool IDs are unique to the message, and the execution lists its observed tools. Profile intake avoids the spurious missing-file assessment.
- Scope stays with direct owner agents. Guests, delegated turns and group chats receive no staged intake. No data migration, model setting change or installation is part of the patch.

## Local Qwen comparison

The existing native harness and local model recorder ran both synthetic conversations on the installed baseline source and candidate code `4cba578`. Both consisted of greeting, staged request, a distractor status paste, profile, fresh-chat recall, correction, and another fresh-chat recall. The longer example changed vocabulary and pronouns and put two expected facts beyond character 4,000. Each arm used isolated fixture data, a 16K local backend, and disabled learning. No separately billed model API was used. Each final row is one run. Latency depends on the runner and cache.

| Check | Short profile baseline → candidate | Long profile baseline → candidate |
| --- | ---: | ---: |
| Model calls | 12 → 8 | 13 → 6 |
| End-to-end wall time | 70.8 s → 17.0 s | 58.1 s → 15.7 s |
| Summed prompt tokens | 64,583 → 42,529 | 73,996 → 31,864 |
| Promise retries | 2 → 0 | 2 → 0 |
| Unrequested release fetches | 2 → 0 | 4 → 0 |
| Intended facts recalled from a fresh chat | 3/3 → 3/3 | 3/3 → 3/3 |
| Extra status facts persisted | 0 → 0 | 1 → 0 |
| Durable user-message characters | 147 → 147 | 4,000 → 5,077 |

Verification separately read the SQLite message and search index and the memory store after the isolated backend exited. Both candidate profiles produced exactly three newly verified entries tied to the profile execution. The corrections produced one active current value and one linked superseded record. A fresh chat returned the current value in both final runs. The older long-profile baseline said it had saved facts on the status turn with zero writes in that turn, then saved the status during intake. A pre-final candidate run also showed a model-generated correction carrying the old value into the text of the new record. The final runs did not reproduce that variant. It remains tracked.

The focused backend regression suite passed **255 tests**. It included failed-write receipts, an unchanged keyed record, staging across turns, cancellation, public and cross-thread boundaries, transcript storage, separate durable tool runs, ordinary single-fact correction and context-window behavior. This supplies independent evidence for the bounded workflow. Owner acceptance and packaged testing of the candidate are pending.

## Open work

- Mixed prose and copied documents need explicit intake and per-fact grounding. The candidate saves supported fields and reports omissions. The original profile contains contextual prose and technical lists outside the supported grammar. A read-only dry replay recognized the offer, the intervening note and the available profile prefix. It made no changes to the actual memory store. The source-bound update check also remains open for corrections whose new record repeats the retired value. [Issue #136](https://github.com/Ty0x7/EchoSpeak/issues/136)
- The explicit release-history fetch still needs a configured source appropriate for a private upstream. The local running version remains available offline. [Issue #137](https://github.com/Ty0x7/EchoSpeak/issues/137)
- Reasoning-only responses recovered in the original model traces. The deterministic intake removes those calls from this workflow. General model behavior and the original missing transcript tail remain outside this fix.

## PR #138 acceptance and remediation

Independent review of `5f0bdec` reproduced unwanted saves after a refusal, promotion of chat- or project-only facts to the account, and two active values after a labeled correction. The new failing-first regressions exercise these cases plus unconfirmed staging, an unavailable project and legacy field records. Adjacent checks found that recall could match both projects when they shared a path, and chat search could return the original scoped source. [Pinned review](https://github.com/Ty0x7/EchoSpeak/pull/138#issuecomment-7050115365)

A past offer now provides conversational context. The current message must authorize its own write. Start a structured list with `Remember this list:`. Use `Remember this list for this chat only:` or `Remember this list for this project only:` for a restricted scope. A project write requires the active project, and a conflicting scope leaves the store alone. The same restriction applies to memory tools and to assistant recalls of scoped source turns. Scoped text stays out of portable chat summaries. The conversation records remain available to the owner.

Both labeled values and supported personal fields use a stable key. Corrections link the new active record to the previous values inside the same owner and scope. Exact legacy field identities participate in supersession. The lookup checks canonical owner, context, active state and text before exposing lexical or vector matches. Legacy project records with a known ID require that ID. Older path-only records retain their path match.

### Verification on the revised code

The original review fixture passed **15/15** on the final code. The focused backend suite passed **367 tests** with **5 skips**. Two scripted chat-search regressions reproduced the archive bypass before the fix and now pass. The vector tests use fixture results with shared paths and stale content. The live machine used lexical recall.

The native harness ran both versions against local Qwen 3.5 9B at 16K using disposable chats and two projects. Read-only checks inspected `records.json` and SQLite after the isolated runtime exited.

| Check | Main `fb0849b` | Revised code `59e4b68` |
| --- | --- | --- |
| Persistent records after an explicit read-only request | 0 | 0 and the summary was delivered |
| Records after an unconfirmed follow-up list | 2 account records | 0 |
| Project-only entry visible in the other project | Returned the working label | No scoped label returned |
| Retired project label present in active record text | Present | Absent |
| Active records by scope | 6 account, 0 project, 0 session | 2 account, 2 project, 2 session |
| Matched 12-turn wall time | 127.9 s | 41.5 s |

The acceptance run added two held-out turns. The chat-only alias was recalled in its own conversation. The other project exercised the real `chat_search` tool and received none of the restricted values. Canonical readback verified original and correction execution IDs, both supersession links, scoped record counts and the archive scope on replies derived from restricted context.

The actual owner store and all eight audited message items matched the earlier capture in a read-only check. The installed application reported 11.15.0. This change is source-only and awaits owner approval on PR #138. No separately billed model API was used. Mixed-prose memory changes and private release history remain tracked on #136 and #137. [PR #138](https://github.com/Ty0x7/EchoSpeak/pull/138)

## Issue #136 · Grounding a conversational schedule save

The six-turn installed-app smoke test explicitly asked Echo to remember a Tuesday ferry time and later correct it. An earlier turn mentioned reading on the ferry, and the save request also mentioned a one-day drink. Both model tool calls added “for reading on the ride” and “AM” to the schedule. The corrected record carried those additions even though the SQLite user item contained only the new time and schedule. Consent and correction linking worked. The write path accepted the model's paraphrase after checking consent, scope and basic fact quality. The subsequent disk readback verified the write without checking that the saved wording came from the authorized text.

The conversational write path now selects one statement from the current save request and saves that wording with ordinary whitespace and punctuation normalization. A tool proposal identifies the requested subject. It cannot supply wording for the stored fact, choose an unrelated key, or authorize a separate statement. Unclear references are refused. New writes link the canonical record to the source execution and SQLite user item, and keyed corrections supersede the old value within the same scope. Recall asks Qwen to leave unspecified details such as AM/PM unspecified. Existing records remain in place.

### Matched local verification

Each arm used the native harness with local Ollama Qwen 3.5 9B, a 16,384-token window, and separate disposable state. Each case contained six turns covering unrelated conversation, an uncued correction, an explicit save, an explicit correction and two fresh-chat queries. The shuttle case changed the schedule, day and transient detail. An independent readback checked canonical JSON, source items in SQLite, active status, both directions of supersession and the actual replies.

| Observed result | Main `1b9e992` | Candidate code `d282b70` |
| --- | --- | --- |
| Ferry records and source text | Added a commute purpose and AM. | Saved only the requested 7:45 schedule and then “My Tuesday Marlow ferry leaves at 8:10 now.” |
| Shuttle records and source text | Saved two umbrella records and embedded the retired 6:50 time in the replacement. | Saved only the requested 6:50 schedule and then “My Thursday North Loop shuttle leaves at 7:05 now.” |
| Canonical records tied to the exact SQLite user item | No source item IDs on these writes. | All four writes point to the authorizing user item. |
| Active records after correction | Ferry 1, shuttle 3. | One per case with reciprocal supersession links. |
| Fresh chats | Recalled both current times with AM. The ferry reply supplied a next date. The shuttle reply also volunteered the old time and a separate query retrieved the umbrella. | Recalled 8:10 and 7:05 without AM or the retired time. The transient queries found no saved drink or umbrella. The shuttle reply added an update-date aside outside the saved fact. |
| Writes on casual and unconfirmed correction turns | 0 in both cases. | 0 in both cases. |
| Wall time over six turns | Ferry 47.8 s, shuttle 56.3 s. | Ferry 71.8 s, shuttle 79.8 s. One sample per arm measures these runs only. |

Five of the six initial failing-first cases exposed a defect on main, and the ambiguous-reference control already passed. The final backend run passed 375 tests with 5 skips. The independent consent, scope and correction suite from PR #138 passed 15/15. The focused regressions are in `apps/backend/tests/test_lean_soul.py` and can be run from `apps/backend` with `python -m pytest tests/test_lean_soul.py -k prose_memory`. Private run traces and original messages stay in the local evidence directory linked from the vault.

This covers a single explicitly authorized conversational statement and its replacement. Multi-fact profiles, copied notes and richer mixed-prose authorization remain open on Issue #136. The evaluations wrote only to disposable state with no separately billed model APIs. The installed release remains 11.15.0 at `1b9e992`.

### Clause-boundary follow-up on PR #139

A failing-first runtime test exposed a gap at `08c8e7a`. The original single-statement selector skipped a semicolon followed by a subject such as “the deck crew,” so it stored the authorized ferry time together with an unrelated seltzer detail. In the opposite position, “Small correction; my Tuesday Marlow ferry leaves at 8:10 now. Please remember that instead,” the selector treated the requested correction as an aside and kept the preamble. The matched local baseline consequently refused the correction. The final assistant reply still claimed success, while fresh-chat recall returned the old 7:45 schedule with the seltzer detail.

Candidate `e6bd644` uses a semicolon as a clause boundary even before a lowercase subject and after a clock abbreviation. It keeps punctuation inside quoted titles. The prefix save selects the first clause. A trailing save instruction selects the last clause. The existing consent, scope, and proposal checks still apply. The regression exercises persistence, both directions of supersession, and source attribution through the real tool. The targeted backend suite passed **377 tests with 4 skips**, and the independent PR #138 acceptance fixture passed **15/15**.

With the same six prompts on local Qwen 3.5 9B at 16K, the baseline took 103.5 s and the candidate took 87.9 s. The candidate saved “I take the 7:45 Marlow ferry on Tuesdays.” and “My Tuesday Marlow ferry leaves at 8:10 now.” Both canonical records point to the correct SQLite user item, and their reciprocal links leave only the correction active. The fresh schedule chat returned 8:10 without the prior value or seltzer, and the separate drink query found no saved drink. The schedule reply added “AM” and an update-date aside absent from the saved statement. That reply behavior remains tracked with #136. Both runs yielded zero writes on casual or unconfirmed correction turns. The live user's canonical memory and SQLite item hashes matched before and after this follow-up.

### Packaged correction release check

Remote main `4c96f03` matches the accepted tree of PRs #134, #133 and #139. Its built AppImage passed the packaged backend self-check. A six-turn smoke against that extracted backend found two active ferry times. Qwen omitted the optional key on the original save, then supplied a key on the correction. Both texts matched their authorizing SQLite items. With the original row unkeyed, the store had no replacement to link, and a fresh chat combined the two times into an invented arrival and departure. The installer held the live service on the previous verified revision. Fingerprints for 12 protected files and four databases matched the prior installation.

The regression replays an absent or changed key in both sentence and semicolon forms. Six cases failed before the change. The fix recognizes an explicit “remember that instead” request with one clock value and uses a unique, previously authorized match in the same conversation and scope. It preserves ordered subject terms, direction and negation, and retains the established key when one exists. A conflicting keyed record or more than one eligible prior statement requires clarification. The correction writes the replacement and retirement link together through the existing store. The broader suite passed 446 tests with four skips, plus all 15 independent memory acceptance checks.

The same six turns passed against the rebuilt candidate sidecar at `03eea05`, using local Ollama Qwen 3.5 9B at 16K. Qwen again omitted the original key and supplied one in the correction. The saved items again matched their authorizing SQLite messages. Only the exact 8:10 statement was active and the old entry was linked as superseded. A fresh chat recalled 8:10 without the prior value, and the separate drink query found no saved fact. The model still supplied “AM” and a recency aside in its reply. These text-generation limits remain open under #136. No separately billed API was used.
