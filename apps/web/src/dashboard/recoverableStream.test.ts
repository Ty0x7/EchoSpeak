import { expect, it } from "vitest";
import { recoverableQuery } from "./recoverableStream";

const ndjson = (events: unknown[]) => new Response(events.map(e => JSON.stringify(e)).join("\n") + "\n");

it("dropped stream resumes by cursor without another prompt submission", async () => {
  const requests: { url: string; init?: RequestInit }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(url), init });
    return requests.length === 1 ? ndjson([{ type: "agent_token", data: "first", _replay_seq: 1 }]) : ndjson([
      { type: "agent_token", data: "duplicate", _replay_seq: 1 },
      { type: "final", response: "finished", _replay_seq: 2 }, { type: "journal_done" },
    ]);
  };
  try {
    const response = await recoverableQuery("http://backend", { thread_id: "chat", client_request_id: "run", message: "hello" }, new AbortController().signal);
    const output = await response.text();
    expect(output.includes("first")).toBe(true);
    expect(output.includes("finished")).toBe(true);
    expect(output.includes("duplicate")).toBe(false);
    expect(requests.length).toBe(2);
    expect(requests[0].init?.method).toBe("POST");
    expect(requests[1].url.includes("after=1")).toBe(true);
    expect(requests[1].init?.method).toBe(undefined);
  } finally { globalThis.fetch = original; }
});

it("reload attaches using GET and marks replayed output", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return ndjson([{ type: "final", response: "done", _replay_seq: 5 }, { type: "journal_done" }]);
  };
  try {
    const response = await recoverableQuery("http://backend", { thread_id: "chat", client_request_id: "run" }, new AbortController().signal, true);
    expect((await response.text()).includes('"_recovered":true')).toBe(true);
    expect(calls).toBe(1);
  } finally { globalThis.fetch = original; }
});
