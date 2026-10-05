/** Reattach transport only. Never resubmit a prompt after an ambiguous failure. */
export async function recoverableQuery(base: string, request: Record<string, unknown>, signal: AbortSignal, recovery = false): Promise<Response> {
  const id = String(request.client_request_id);
  const session = String(request.thread_id);
  let cursor = 0;
  let reconnect = recovery;
  const fetchReplay = () => fetch(`${base}/query/runs/${encodeURIComponent(id)}/events?thread_id=${encodeURIComponent(session)}&after=${cursor}`, { signal });
  let response: Response | undefined;
  try {
    response = recovery ? await fetchReplay() : await fetch(`${base}/query/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal });
  } catch (error) {
    if (signal.aborted) throw error;
    reconnect = true;
  }
  if (response && !response.ok) return response;
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      let attempts = 0;
      try {
        while (!signal.aborted) {
          try {
            if (!response) response = await fetchReplay();
            if (!response.ok || !response.body) throw new Error(`Could not reconnect to this run (HTTP ${response.status}).`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            try {
              while (!signal.aborted) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let end;
                while ((end = buffer.indexOf("\n")) >= 0) {
                  const line = buffer.slice(0, end).trim(); buffer = buffer.slice(end + 1);
                  if (!line) continue;
                  const event = JSON.parse(line);
                  if (event.type === "journal_done") { controller.close(); return; }
                  const seq = Number(event._replay_seq || 0);
                  if (seq && seq <= cursor) continue;
                  cursor = Math.max(cursor, seq);
                  attempts = 0;
                  controller.enqueue(encoder.encode(JSON.stringify({ ...event, _recovered: reconnect || event._recovered }) + "\n"));
                }
              }
            } finally { reader.releaseLock(); }
            if (signal.aborted) break;
            throw new Error("Connection ended before this run finished.");
          } catch (error) {
            if (signal.aborted) break;
            if (++attempts > 5) throw error;
            response = undefined; reconnect = true;
            await new Promise<void>((resolve, reject) => {
              const onAbort = () => { clearTimeout(timer); reject(signal.reason); };
              const timer = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, Math.min(8000, 500 * 2 ** attempts));
              signal.addEventListener("abort", onAbort, { once: true });
            });
          }
        }
        controller.close();
      } catch (error) { controller.error(error); }
    },
  });
  return new Response(body, { headers: { "Content-Type": "application/x-ndjson" } });
}
