import { afterEach, describe, expect, it, vi } from "vitest";
import { startUsageRefresh, USAGE_REFRESH_MS } from "./usageRefresh";
import { latestUsage } from "./RuntimeUsage";

afterEach(() => vi.useRealTimers());
describe("subscription freshness", () => {
  it("uses the newest observation regardless of whether it came from a session or an account check", () => {
    const old = { observed_at: 10, windows: [{label: "Weekly", used_percent: 12}] };
    const fresh = { observed_at: 20, unavailable: "Metric unavailable" };
    expect(latestUsage(old, fresh)).toBe(fresh);
    expect(latestUsage(fresh, old)).toBe(fresh);
    expect(latestUsage(undefined, old)).toBe(old);
  });
  it("refreshes after five minutes, coalesces focus, and cancels on unmount", async () => {
    vi.useFakeTimers();
    const target = new EventTarget();
    let finish!: () => void;
    const refresh = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const error = vi.fn();
    const stop = startUsageRefresh(refresh, error, target);
    target.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(USAGE_REFRESH_MS - 1);
    expect(refresh).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    target.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(USAGE_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(1);
    finish(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(USAGE_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(2);
    stop(); finish(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(USAGE_REFRESH_MS * 2);
    target.dispatchEvent(new Event("focus"));
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(error).not.toHaveBeenCalled();
  });
  it("reports failures and retries on the next interval", async () => {
    vi.useFakeTimers();
    const refresh = vi.fn().mockRejectedValue(new Error("Disconnected"));
    const error = vi.fn();
    const stop = startUsageRefresh(refresh, error, new EventTarget());
    await vi.advanceTimersByTimeAsync(USAGE_REFRESH_MS * 2);
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(error).toHaveBeenCalledTimes(2);
    stop();
  });
});
