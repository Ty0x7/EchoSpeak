/**
 * Read replies aloud through EchoSpeak's shared speaker (backend agent/speech_output.py).
 *
 * On the desktop the backend runs on this computer, so speech plays through the same
 * queue as Echo Companion. The two never talk over each other, and the voice, speed,
 * volume and output device from Settings › Voice all apply. Live voice-conversation mode
 * keeps its own governed transport (barge-in, turn records).
 */
type Phase = (phase: "speaking" | "idle", detail?: string) => void;

class SystemSpeaker {
  private current = "";
  private apiBase = "";

  async speak(text: string, apiBase: string, onPhase: Phase, signal?: AbortSignal): Promise<boolean> {
    this.apiBase = apiBase;
    const response = await fetch(`${apiBase}/media-runtime/voice/speak`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, channel: "echo", source: "echo" }),
    });
    if (!response.ok) throw new Error("Read aloud is unavailable. Check Settings › Voice.");
    const queued = await response.json();
    if (queued.status !== "queued") return false; // muted or a repeat of what was just said
    this.current = String(queued.id || "");
    onPhase("speaking");
    try {
      // Follow this request until the shared queue reports it finished (polling only while speaking).
      while (this.current === queued.id && !signal?.aborted) {
        await new Promise(resolve => window.setTimeout(resolve, 400));
        const state = await fetch(`${apiBase}/media-runtime/voice/speak/state`).then(r => r.ok ? r.json() : null).catch(() => null);
        if (!state) break;
        const finished = (state.events || []).find((e: any) => e.type === "speech.finished" && e.request?.id === queued.id);
        if (finished) {
          if (finished.request.status === "failed") throw new Error(finished.request.error || "Speech failed.");
          return finished.request.status === "completed";
        }
      }
      return false;
    } finally {
      if (this.current === queued.id) this.current = "";
      onPhase("idle");
    }
  }

  stop(): void {
    const id = this.current;
    this.current = "";
    if (id && this.apiBase) {
      void fetch(`${this.apiBase}/media-runtime/voice/speak/cancel`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: id }),
      }).catch(() => undefined);
    }
  }
}

export const systemSpeaker = new SystemSpeaker();
