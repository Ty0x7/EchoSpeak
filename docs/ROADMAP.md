# EchoSpeak roadmap

What's next, in priority order. Each item says why it matters and roughly how big it is
(S, M, L). Older plans are in [archive/ROADMAP-v9.md](archive/ROADMAP-v9.md).

## Now: the 10.2 cleanup

- [x] Remove the retired pipeline: specialists, TaskRuns, execution graph, intent router,
      mode controller, pipeline plugins and ~44 API routes nothing called (about 30k lines).
- [x] `agent/core.py` is just the app object (model, memory, soul, workspace, Project scope).
- [x] `api/server.py` split into one router per area (`api/routes/`), with shared pieces in
      `api/deps.py` and access rules in `api/auth.py`.
- [x] Go TUI and the terminal setup wizard removed; setup happens in the app.
- [x] Docs: one guide, one architecture doc, this roadmap; the rest archived.
- [x] PyTorch out of the default install: embeddings from the model server or a small ONNX
      model; document search as an optional download.
- [x] `Dashboard` (`apps/web/src/index.tsx`) split into hooks and chat/composer components.
- [x] Security audit fixes: terminal destination checks, complete approval arguments,
      image click gating, structured-output redaction and document-index migration.

## Next

1. **Build releases on GitHub Actions.** A tag-triggered `windows-latest` workflow with the
   signing key as a secret, so a release is one `git tag` away and never depends on one PC. **M**
2. **Run the evaluation before every release.** `apps/backend/scripts/eval_gemma.py` needs a
   live model; run it from the release script or a self-hosted runner and block on regressions. **S**
3. **Approvals page on the lean approvals.** The Approvals tab still reads the old approval
   store, which the lean runtime doesn't write; point it at `/lean/approvals`. **S**
4. **Drop LangChain.** The lean runtime talks to models over plain HTTP. LangChain is left only
   in the model prewarm and the FAISS memory wrapper; replacing both shrinks the bundle and
   removes a large dependency tree. **M**
5. **One approval store.** `agent/state.py` still carries the 9.x approval and retry fields.
   Once nothing reads them, remove them with a schema migration. **M**

## Later

- **Cross-platform desktop builds** (macOS, Linux) once releases are built in CI. **L**
- **Plugin packaging for skills** with signed manifests, so third-party skills can be shared
  without running unreviewed Python at startup. **L**
- **Model routing per task** (small local model for chat, larger one for coding) chosen
  automatically from the agent and the request. **M**
