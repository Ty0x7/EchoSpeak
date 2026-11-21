# Lean 4 verification and proof checking

EchoSpeak uses a local Lean 4 model for the canonical memory visibility predicate. Lean elaborates the proposition and tactics into a proof term. Its kernel checks the term. Lake builds the dependency-free project and `leanchecker --fresh` replays the compiled module in a separate invocation of the same kernel. The gate also audits each named theorem's transitive axioms. [Proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) · [Lake](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Lake/)

## Reviewed property and scope

The five statements in [MemoryVisibility.lean](../../tools/formal/MemoryVisibility.lean) require an active row and the current owner for a successful read. They exclude a modern project row after switching away from its project ID, even if paths coincide. Legacy project rows need the matching nonempty path. A different session cannot read the session row. Retirement revokes visibility in every context. IDs are normalized and empty means absent. The audited axiom allowlist is `propext`, `Classical.choice`, and `Quot.sound`.

The finite conformance check compares 11,664 Lean-generated decisions with `AgentMemory._record_visible`. Its identifiers represent equal, unequal and absent values. The proof is about this model. Runtime suites separately exercise parsing, storage, permissions, API routes, agent behavior, and scope changes. This obligation does not cover every recall path, concurrent state transitions, string normalization or all of Python.

## Reproduce

Use the official Linux distribution of Lean 4.36.0. The published x86-64 archive SHA-256 is `2b7ab1ed8b92d2e6abfaf41344823b510064dbc9c0260cac73eb496ab2dea6eb`. Verify it before extraction. Set `ECHOSPEAK_LEAN4_ROOT` to the extracted absolute path. From the repository root run the existing backend Python environment.

```sh
apps/backend/.venv/bin/python tools/formal/check.py
cd apps/backend
.venv/bin/python -m pytest tests/test_formal_verification.py tests/test_memory_scope_and_obsidian.py
```

The first command requires the pinned compiler, Lake build, theorem audit, independent replay and Python/Lean parity. It exits nonzero for missing tools, theorem errors, unapproved axioms or a mismatched decision. The second command separately verifies runtime behavior. [Pinned toolchain release](https://github.com/leanprover/lean4/releases/tag/v4.36.0)

## Optional MCP capability

Use the existing MCP settings and approval flow. Configure a new server named `lean_proof` with the absolute backend Python path, the argument `/absolute/path/to/tools/formal/lean_mcp.py`, and an environment entry `ECHOSPEAK_LEAN4_ROOT` pointing to the verified distribution. Set `allow_global: false` and list the authorized `project_ids`. Add `session_ids` to narrow that access further. Leave `accept_server_read_only_hints` disabled. Review and approve the configured server. In the authorized project, use an agent with the connector in its toolsets and approve each call under the existing MCP action policy. The bundled `lean-proof-check` Agent Skill is available for separate import and approval.

The adapter checks a single proposition and proof term. It accepts `∀ n : Nat, n = n` with `by intro n; rfl` and returns `verified: true`, Lean 4.36.0 and an empty axiom list. An invalid term returns Lean's diagnostics, including unresolved goals where available. The submitted strings are parsed as complete terms and inserted into a single generated theorem. Imports, paths and commands are outside the request schema.

The compiler runs with empty inherited environment, a new network and PID namespace, disabled nested user namespaces, read-only toolchain and system libraries, a bounded temporary directory, and no home or workspace mounts. The adapter limits input and diagnostics, runs one check at a time, uses a 9-second wall deadline and a 5-second CPU limit, and terminates the worker on cancellation. It logs proof digest, status and timing. The ordinary EchoSpeak runtime does not install or ship the Lean distribution.

## Alternatives reviewed

Mathlib provides a large library that is useful for deeper mathematical workloads. This invariant fits Lean's core, leaving no third-party Lake dependencies. The Lean LSP MCP project offers builds, source-file retrieval, imports, remote premise search and an interactive REPL. Those are useful in a designated Lean workspace and exceed the access needed for this connector. TLC can model-check finite state machines with richer interleavings. The initial obligation uses theorem checking for equality and ownership on arbitrary identifiers and uses finite vectors only for production conformance. [Mathlib](https://github.com/leanprover-community/Mathlib4) · [Lean LSP MCP tools](https://github.com/oOo0oOo/lean-lsp-mcp/blob/main/docs/tools.md) · [TLC](https://lamport.azurewebsites.net/tla/book-01-08-21.pdf)
