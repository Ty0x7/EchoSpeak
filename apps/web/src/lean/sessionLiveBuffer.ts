import { emptyLive, leanReducer } from "./liveReducer";
import type { LeanEvent, LeanLiveState } from "./types";

/** Each chat owns its live turn, including frames received while hidden. */
export class SessionLiveBuffer {
  private states = new Map<string, LeanLiveState>();
  private queues = new Map<string, LeanEvent[]>();
  get(session: string) { return this.states.get(session) ?? null; }
  start(session: string, request: string) {
    this.queues.delete(session);
    this.states.set(session, emptyLive(request));
  }
  push(session: string, event: LeanEvent) {
    const state = this.states.get(session);
    if (!state || (event.request_id && event.request_id !== state.requestId)) return;
    const queue = this.queues.get(session) ?? [];
    queue.push(event);
    this.queues.set(session, queue);
  }
  flush(session: string) {
    let state = this.states.get(session);
    if (state) for (const event of this.queues.get(session) ?? []) state = leanReducer(state, event);
    this.queues.delete(session);
    if (state) this.states.set(session, state);
    return state ?? null;
  }
  flushAll() { for (const session of this.queues.keys()) this.flush(session); }
  finish(session: string) {
    const state = this.flush(session);
    this.states.delete(session);
    return state;
  }
}
