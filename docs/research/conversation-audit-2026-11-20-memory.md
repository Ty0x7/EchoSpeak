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

## Changes and boundaries

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
