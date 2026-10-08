/**
 * Scripts for the "Watch a run" workspace on the front page. Each run is what
 * EchoSpeak really does for a task like it: a plan first, hand-offs between
 * agents, tool steps with live progress lines and a summary, an approval stop
 * before anything risky, and an answer you can inspect. Nothing here is fetched;
 * the steps are examples written to match the product's real labels.
 */

export type AgentId = "echo" | "jarvis" | "glados";
export type ToolId = "web" | "memory" | "files" | "terminal" | "gate";
export type Decision = "allow" | "deny";

export type RunStep = {
  id: string;
  agent: AgentId;
  kind: "plan" | "handoff" | "tool" | "memory" | "approval" | "answer";
  /** Tool node that lights up on the canvas. */
  tool?: ToolId;
  /** Agent that receives a hand-off. */
  to?: AgentId;
  /** Present tense while running, past tense once done (as in the app). */
  label: string;
  done: string;
  /** Live lines shown under the step while it runs. */
  live?: string[];
  /** One line under the finished step. */
  summary?: string;
  /** What the inspector shows for this step. */
  output?: string;
  /** How long the step runs, in ms. Approvals wait for the visitor instead. */
  ms: number;
  /** Plan item this step works on. */
  plan?: number;
  /** Only part of the run when the visitor chose this at the approval. */
  onlyIf?: Decision;
  /** Shown in place of `done` when the visitor said no. */
  deniedDone?: string;
};

export type Run = {
  id: string;
  tab: string;
  ask: string;
  plan: string[];
  steps: RunStep[];
  result: "table" | "diff" | "list";
};

export const AGENTS: Record<AgentId, { name: string; role: string; tone: "light" | "dark" }> = {
  echo: { name: "Echo", role: "Your agent", tone: "light" },
  jarvis: { name: "Jarvis", role: "Researcher", tone: "dark" },
  glados: { name: "Glados", role: "Builder", tone: "dark" },
};

export const RUNS: Run[] = [
  {
    id: "research",
    tab: "Research",
    ask: "Compare three budget streaming mics under $100.",
    plan: ["Search for reviews", "Read the best sources", "Check today's prices", "Compare and answer"],
    result: "table",
    steps: [
      { id: "r1", agent: "echo", kind: "plan", label: "Planning", done: "Planned 4 steps", ms: 1300,
        output: "I'll compare three mics by reading reviews and checking current prices.\n\n1. Search for reviews\n2. Read the best sources\n3. Check today's prices\n4. Compare and answer" },
      { id: "r2", agent: "echo", kind: "handoff", to: "jarvis", label: "Handing research to Jarvis", done: "Handed research to Jarvis", ms: 1100,
        output: "Jarvis is the research agent.\nTools it may use: web search, reading pages, product prices." },
      { id: "r3", agent: "jarvis", kind: "tool", tool: "web", plan: 0, ms: 3200,
        label: "Searching “best budget streaming mic”", done: "Searched “best budget streaming mic”",
        live: ["Searching…", "14 results · reading the top 2", "Reading rtings.com (1 of 2)", "Reading soundguys.com (2 of 2)"],
        summary: "14 results · read 2 pages",
        output: "1. rtings.com/microphone/reviews/best/streaming\n2. soundguys.com/best-usb-microphones\n3. youtube.com · Budget mic shootout\n…11 more\n\n## Pages read from the top results\n### rtings.com/microphone/reviews/best/streaming\nThe Fifine AM8 offers USB and XLR…" },
      { id: "r4", agent: "jarvis", kind: "tool", tool: "web", plan: 1, ms: 2000,
        label: "Reading soundguys.com/best-usb-microphones", done: "Read soundguys.com/best-usb-microphones",
        live: ["Opening the page…", "Reading 3,840 words"], summary: "Read 3,840 words",
        output: "HyperX SoloCast: plug-and-play cardioid, no gain knob.\nSamson Q2U: dynamic capsule, rejects room noise well.\nFifine AM8: USB now, XLR later." },
      { id: "r5", agent: "jarvis", kind: "tool", tool: "web", plan: 2, ms: 2400,
        label: "Checking prices for 3 mics", done: "Checked prices for 3 mics",
        live: ["Fifine AM8 · $59", "HyperX SoloCast · $49", "Samson Q2U · $69"], summary: "3 products · prices from today",
        output: "Fifine AM8        $59   in stock\nHyperX SoloCast   $49   in stock\nSamson Q2U        $69   in stock" },
      { id: "r6", agent: "echo", kind: "answer", plan: 3, ms: 1600, label: "Writing the answer", done: "Answered with 3 sources",
        summary: "Comparison table · 3 sources" },
    ],
  },
  {
    id: "code",
    tab: "Code",
    ask: "Fix the failing test in my calculator project.",
    plan: ["Find the failing test", "Fix the bug", "Run the tests again", "Push the fix (asks you)"],
    result: "diff",
    steps: [
      { id: "c1", agent: "echo", kind: "plan", label: "Planning", done: "Planned 4 steps", ms: 1200,
        output: "I'll find the failing test, fix it, re-run the tests, then ask before pushing.\n\n1. Find the failing test\n2. Fix the bug\n3. Run the tests again\n4. Push the fix (asks you)" },
      { id: "c2", agent: "echo", kind: "handoff", to: "glados", label: "Handing the code to Glados", done: "Handed the code to Glados", ms: 1000,
        output: "Glados is the builder.\nTools it may use: files, terminal, git." },
      { id: "c3", agent: "glados", kind: "memory", tool: "memory", ms: 1300, label: "Checking notes on this project", done: "Recalled 2 project notes",
        summary: "Tests run with pytest · pushes to main",
        output: "• calculator: tests run with `pytest -q`\n• You push this repo straight to main" },
      { id: "c4", agent: "glados", kind: "tool", tool: "terminal", plan: 0, ms: 2400, label: "Running `pytest -q`", done: "Ran `pytest -q`",
        live: ["collected 12 items", "test_calc.py::test_divide FAILED"], summary: "Exit code 1 · 1 failed, 11 passed",
        output: "FAILED test_calc.py::test_divide - ZeroDivisionError: division by zero\n1 failed, 11 passed in 0.41s" },
      { id: "c5", agent: "glados", kind: "tool", tool: "files", plan: 1, ms: 1800, label: "Editing calc.py", done: "Edited calc.py",
        live: ["Reading calc.py", "Writing the fix"], summary: "+3 −1 lines",
        output: "@@ def divide(a, b):\n-    return a / b\n+    if b == 0:\n+        raise ValueError(\"can't divide by zero\")\n+    return a / b" },
      { id: "c6", agent: "glados", kind: "tool", tool: "terminal", plan: 2, ms: 2200, label: "Running `pytest -q`", done: "Ran `pytest -q`",
        live: ["collected 12 items", "12 passed"], summary: "Tests: 12 passed",
        output: "............\n12 passed in 0.38s" },
      { id: "c7", agent: "glados", kind: "approval", tool: "gate", plan: 3, ms: 0, label: "Asking to run `git push origin main`",
        done: "You allowed `git push origin main`", deniedDone: "You said no to `git push origin main`",
        output: "Sends 1 commit to GitHub:\n“Fix divide by zero in calc.py”" },
      { id: "c8", agent: "glados", kind: "tool", tool: "terminal", plan: 3, ms: 1600, onlyIf: "allow", label: "Running `git push origin main`", done: "Ran `git push origin main`",
        live: ["Pushing 1 commit…"], summary: "Pushed · main is up to date", output: "To github.com/you/calculator.git\n   4e1a2c0..9b7d3f1  main -> main" },
      { id: "c9", agent: "echo", kind: "answer", ms: 1500, label: "Writing the answer", done: "Answered with the diff", summary: "1 file changed · 12 tests passing" },
    ],
  },
  {
    id: "team",
    tab: "Team",
    ask: "Plan dinner tonight and put the shopping list in my notes.",
    plan: ["Check what you like", "Find a quick recipe", "Write the shopping list", "Wrap up"],
    result: "list",
    steps: [
      { id: "t1", agent: "echo", kind: "plan", label: "Planning", done: "Planned 4 steps", ms: 1200,
        output: "I'll check what you like, have Jarvis find a recipe and Glados write the list.\n\n1. Check what you like\n2. Find a quick recipe\n3. Write the shopping list\n4. Wrap up" },
      { id: "t2", agent: "echo", kind: "memory", tool: "memory", plan: 0, ms: 1400, label: "Checking what you like", done: "Recalled 2 preferences",
        summary: "Vegetarian · weeknights under 30 minutes",
        output: "• You're vegetarian\n• Weeknight dinners: under 30 minutes" },
      { id: "t3", agent: "echo", kind: "handoff", to: "jarvis", label: "Asking Jarvis for a recipe", done: "Asked Jarvis for a recipe", ms: 1000,
        output: "“Find a vegetarian dinner under 30 minutes. Read the recipes, not just the titles.”" },
      { id: "t4", agent: "jarvis", kind: "tool", tool: "web", plan: 1, ms: 2800, label: "Searching “vegetarian dinner under 30 minutes”", done: "Searched “vegetarian dinner under 30 minutes”",
        live: ["9 results · reading the top 3", "Reading seriouseats.com (1 of 3)", "Reading bbcgoodfood.com (2 of 3)"], summary: "9 results · read 3 recipes",
        output: "Picked: garlic lemon pasta, 20 minutes, 7 ingredients.\nAlso considered: chickpea curry (35 min), veggie tacos (25 min)." },
      { id: "t5", agent: "echo", kind: "handoff", to: "glados", label: "Asking Glados to write the list", done: "Asked Glados to write the list", ms: 1000,
        output: "“Save a shopping list for garlic lemon pasta to my notes.”" },
      { id: "t6", agent: "glados", kind: "tool", tool: "files", plan: 2, ms: 1800, label: "Writing shopping-list.md", done: "Wrote shopping-list.md",
        live: ["Writing 7 items"], summary: "+9 lines · 7 items",
        output: "# Shopping list\n- Spaghetti\n- Garlic\n- 2 lemons\n- Parmesan\n- Spinach\n- Chili flakes\n- Olive oil" },
      { id: "t7", agent: "echo", kind: "answer", plan: 3, ms: 1500, label: "Writing the answer", done: "Answered with the plan", summary: "Recipe · list saved to Notes" },
    ],
  },
];

/** The steps a visitor sees, given their answer at the approval (if any yet). */
export function stepsFor(run: Run, decision: Decision | null): RunStep[] {
  return run.steps.filter((step) => !step.onlyIf || step.onlyIf === decision);
}
