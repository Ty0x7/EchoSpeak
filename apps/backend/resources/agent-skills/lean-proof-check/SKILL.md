---
name: lean-proof-check
description: Check and repair Lean 4 proposition proofs with an approved local proof checker when the user requests formal verification or mathematics.
---

# Lean proof checking

Use the approved, scoped `mcp__lean_proof__check_proof` capability. If needed,
discover it using `find_tools` for Lean proof checking. If the connector is
unavailable or its approval is denied, explain that the proof was not checked.

Pass the proposition as the statement and the proof term separately. Example:
`∀ n : Nat, n = n` and `by intro n; rfl`. Keep imports and project paths out
of the call. Do not use proof holes or native evaluation.

Read the diagnostic and unresolved goals on failure. Revise the proof term
within the original proposition and retry with approval. Report the exact
proposition, verified status, Lean version, and axioms from the tool receipt.
Describe any link to implementation behavior with separate runtime evidence.
