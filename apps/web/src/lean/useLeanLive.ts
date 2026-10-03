import { useCallback, useRef, useState } from "react";
import { emptyLive, leanReducer } from "./liveReducer";
import type { LeanEvent, LeanLiveState } from "./types";

/**
 * Live state for the turn being streamed.
 *
 * Token and reasoning events can arrive hundreds of times a second. They are
 * queued and applied once per animation frame, so React renders at most one
 * update per frame no matter how fast the model streams. The ref is the
 * source of truth; `finish()` applies anything still queued synchronously so
 * the committed message is exactly what was on screen.
 */
export function useLeanLive() {
  const [live, setLive] = useState<LeanLiveState | null>(null);
  const stateRef = useRef<LeanLiveState | null>(null);
  const queueRef = useRef<LeanEvent[]>([]);
  const frameRef = useRef(0);
  const timerRef = useRef(0);

  const flush = useCallback(() => {
    frameRef.current = 0;
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = 0;
    }
    const events = queueRef.current;
    if (!events.length) return;
    queueRef.current = [];
    let state = stateRef.current ?? emptyLive();
    for (const evt of events) state = leanReducer(state, evt);
    stateRef.current = state;
    setLive(state);
  }, []);

  const push = useCallback(
    (evt: LeanEvent) => {
      queueRef.current.push(evt);
      if (!frameRef.current) {
        frameRef.current = window.requestAnimationFrame(flush);
        // Background windows pause rAF; keep the transcript current anyway.
        timerRef.current = window.setTimeout(flush, 120);
      }
    },
    [flush]
  );

  const start = useCallback((requestId: string) => {
    if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    frameRef.current = 0;
    timerRef.current = 0;
    queueRef.current = [];
    const state = emptyLive(requestId);
    stateRef.current = state;
    setLive(state);
  }, []);

  const finish = useCallback((): LeanLiveState | null => {
    if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    frameRef.current = 0;
    timerRef.current = 0;
    let state = stateRef.current;
    if (state) for (const evt of queueRef.current) state = leanReducer(state, evt);
    queueRef.current = [];
    stateRef.current = null;
    setLive(null);
    return state;
  }, []);

  return { live, push, start, finish, stateRef };
}
