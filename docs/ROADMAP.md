# EchoSpeak roadmap

What's next, in order. Sizes: **S** under a day, **M** a few days, **L** a week or more. Earlier plans: [10.x and 11.0](archive/ROADMAP-v11.0-10.x.md), [9.x](archive/ROADMAP-v9.md).

## Where things stand (source, October 2026)

11.7.1 adds screenshots the model sees and computer control. The 11.8 harness work in source adds Claude's own computer tool, MCP sign-in, MCP Apps and Tasks, a quarantined reader for outside content, per-reply traces, long results kept whole, outbound A2A with signed Agent Cards, the Echo bench, and removes LangChain. See the [draft 11.8.0 notes](releases/v11.8.0.md) and the status table in the [README](../README.md#where-each-feature-stands).

## 1. Prove what exists

1. **Real runs before release.** Minecraft or another game with Claude's computer tool and with a local vision model; `scripts/echo_bench.py --desktop` on each model you ship for. **S**
2. **Learning ablation.** `scripts/eval_learning.py` with learning on, off and control. Keep learning on by default only if it wins without more false successes. **S**
3. **Upgrade check with real data.** Start a build from 11.7 data: memory and document indexes convert once; search still finds things. **S**
4. **Bench in the release script.** `release-windows.ps1` runs the bench against a loaded model and blocks on regressions. **S**

## 2. Close the gaps

1. **Native computer use for GPT.** OpenAI's computer tool needs the Responses API; add a Responses path to the provider and map its actions onto the same executor. **M**
2. **Screenshot size per model.** Claude 5.5 reads up to 2576 px; today every model gets 1280×768. Size the coordinate space from the model that will see it. **S**
3. **Optional sandboxed desktop.** A VM or Windows Sandbox target for computer use, as Anthropic and OpenAI recommend for untrusted sites. **L**
4. **Host a client metadata document** so MCP servers that support it skip registering EchoSpeak (template in `docs/oauth/`). **S**
5. **Answers mid-task.** MCP Tasks and MCP Apps that ask a question (input_required, elicitation) are cancelled today; show the question as an ask_user card instead. **M**
6. **Local model list per feature.** Which local models pass tools, vision and computer use on the bench. **S**

## 3. Clean up

1. **State migration.** Remove the dormant 9.x approval fields from `agent/state.py` with a migration checked against real chat state. **M**
2. **Drop faiss.** After one release of conversions, remove the faiss dependency (it only reads 11.7 indexes). **S**
3. **Partitioned memory rebuild.** With `memory_partition_enabled`, per-chat indexes aren't rebuilt from records; make rebuild cover them. **S**

## 4. Later

- **Local realtime voice.** Decide the shape first: a local speech-to-speech stack runs its own model, so it either replaces Echo's model and tools in voice or must act only as speech in and out. **L**
- **Quarantined reader with typed outputs.** Return values in a schema (dates, numbers, names) instead of prose, the stronger half of CaMeL. **M**
- **Cross-platform desktop builds** once releases are built in CI. **L**
- **Signed skill packages**, so third-party skills can be shared without running unreviewed code. **L**
