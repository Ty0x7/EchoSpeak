# EchoSpeak research blog: structure and editorial policy (proposal, 27 Nov 2026)

Status: **proposal for review. Nothing is published, and the site is unchanged.**

## Where it lives

- **Route:** `#/research` (list) and `#/research/<slug>` (post) in the existing site (`apps/web/src/site`, hash routes,
  so GitHub Pages never 404s). It's a new page alongside Home and Docs, not a redesign.
- **Content:** Markdown files with front matter in `apps/web/content/research/<yyyy-mm-dd>-<slug>.md`, rendered with
  `react-markdown` (already a dependency). They're loaded at build time through `import.meta.glob(..., { query: "?raw" })`.
- **Front matter:** `title`, `date`, `summary`, `status` (one of `verified`, `measured-on-one-machine`, `hypothesis`,
  `negative-result`), `evidence` (links to `docs/research/*.md` reports, never to local paths), `tags`.
- An **evidence badge** on every post shows its `status`. Each claim in a post is either backed by a linked report or
  marked as a hypothesis or open question in the text.

## Editorial rules

1. **Lead with discoveries, not activity.** No per-test or per-commit posts.
2. **Say exactly what was measured and on what:** the model, the quantization, the GPU, the context window, the
   number of cases and repetitions, and the judge. One machine is one machine.
3. **Keep verified findings, hypotheses and open questions apart**, in separate sections.
4. **No comparative claims about other products** unless both were measured the same way (§ harness comparison plan).
   Vendor numbers are quoted as vendor numbers.
5. **Report negative results** (for example, the skill that was never invoked).
6. **Privacy.** No private paths, usernames, logs, prompts, conversation content, credentials, hostnames or device
   details beyond the hardware class. Screenshots and traces come from synthetic fixtures only.
7. **Review checklist before publishing:** links resolve to public repository files; numbers match the linked report;
   status badge is correct; privacy pass is done; the owner approves.

## First articles (sources already exist; nothing new to measure)

| # | Working title | Status | Built from |
|---|---|---|---|
| 1 | Why we build for an 8 GB GPU | philosophy | this plan, ROADMAP |
| 2 | Same model, better harness: 7.9 → 9.0 | verified (one machine) | harness-evaluation.md: Qwen 3.5 9B 7.94 → 8.95, held-out 7.41 → 9.38, execution success 48% → 83% |
| 3 | A 1.76-bit 27B model on an 8 GB card: what fits, what it costs | measured-on-one-machine | bonsai-vs-qwen §4–5, docs/local-models/bonsai.md |
| 4 | Quality vs speed: why our combined score disagreed with our quality score | verified | bonsai-vs-qwen §2 |
| 5 | "Done" isn't done: false success claims and the verification ladder | verified | verified-engineering.md, bonsai-vs-qwen §3 (9 vs 1 false successes), harness-evaluation |
| 6 | Prompt layout is a performance feature (5.7 s → 0.3 s) | verified | context-strategy.md §5 |
| 7 | Memory with consent: correcting what an agent remembers | verified | #239, PR #243 |
| 8 | Evaluating agents honestly on a desktop: private servers, held-out cases, pre-registration | method | agentic-evaluation.md, harness-evaluation.md, bonsai-vs-qwen |
| 9 | A local agent's security boundary: sandbox, injection, "never readable" | verified + open questions | security-audit-2026-10-25.md, environment-access.md, provider-and-isolation.md |
| 10 | What didn't work: a skill no small model opened (0 of 182 requests) | negative-result | check-integrity-experiment.md |
| next | How much is the model, how much the harness? (EchoSpeak vs Hermes vs raw) | hypothesis | harness-comparison-plan.md, posted only after the runs |
