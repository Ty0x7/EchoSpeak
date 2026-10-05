import { useCallback, useEffect, useRef, useState } from "react";
import { SessionLiveBuffer } from "./sessionLiveBuffer";
import type { LeanEvent, LeanLiveState } from "./types";

/** Batch frames without tying a running turn to the currently visible chat. */
export function useLeanLive(sessionId: string) {
  const [live, setLive] = useState<LeanLiveState | null>(null);
  const buffer = useRef(new SessionLiveBuffer());
  const selected = useRef(sessionId);
  const frame = useRef(0), timer = useRef(0);
  const select = useCallback((session: string) => {
    selected.current = session;
    setLive(buffer.current.flush(session));
  }, []);
  useEffect(() => { select(sessionId); }, [sessionId, select]);
  const flush = useCallback(() => {
    window.cancelAnimationFrame(frame.current);
    window.clearTimeout(timer.current);
    frame.current = timer.current = 0;
    buffer.current.flushAll();
    setLive(buffer.current.get(selected.current));
  }, []);
  const push = useCallback((event: LeanEvent, session: string) => {
    buffer.current.push(session, event);
    if (!frame.current) {
      frame.current = window.requestAnimationFrame(flush);
      timer.current = window.setTimeout(flush, 120);
    }
  }, [flush]);
  const start = useCallback((request: string, session: string) => {
    buffer.current.start(session, request);
    if (session === selected.current) setLive(buffer.current.get(session));
  }, []);
  const finish = useCallback((session: string) => {
    const result = buffer.current.finish(session);
    if (session === selected.current) setLive(null);
    return result;
  }, []);
  const get = useCallback((session: string) => buffer.current.get(session), []);
  useEffect(() => () => {
    window.cancelAnimationFrame(frame.current);
    window.clearTimeout(timer.current);
  }, []);
  return { live, select, push, start, finish, get };
}
