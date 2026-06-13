import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { usePolling } from "@/composables/usePolling";

function setHidden(v: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => v });
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("usePolling", () => {
  it("active 会话每 5s 调一次 pollTick", () => {
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).toHaveBeenCalledTimes(3);
    p.stop();
  });

  it("paused 会话不调 pollTick", () => {
    const store = { currentSession: { status: "paused" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).not.toHaveBeenCalled();
    p.stop();
  });

  it("document.hidden 时 tick 不调 pollTick", () => {
    setHidden(true);
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.tick();
    expect(store.pollTick).not.toHaveBeenCalled();
  });

  it("stop 后清理定时器", () => {
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    p.stop();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).not.toHaveBeenCalled();
  });

  it("无 currentSession 时 tick 不调 pollTick", () => {
    const store = { currentSession: undefined, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.tick();
    expect(store.pollTick).not.toHaveBeenCalled();
  });
});
