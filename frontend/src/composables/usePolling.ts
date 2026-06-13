import type { useSessionsStore } from "@/stores/sessions";

type Store = Pick<ReturnType<typeof useSessionsStore>, "currentSession" | "pollTick">;

/**
 * 5s active-only 增量轮询。document.hidden（标签页不可见）暂停、可见恢复——省后端读。
 * 无内部生命周期钩子：由调用方在 onMounted(start)/onUnmounted(stop) 接管。
 */
export function usePolling(store: Store, intervalMs = 5000) {
  let timer: ReturnType<typeof setInterval> | null = null;

  const tick = () => {
    if (typeof document !== "undefined" && document.hidden) return;
    if (store.currentSession?.status !== "active") return;
    void store.pollTick();
  };

  function startTimer() {
    if (timer) return;
    timer = setInterval(tick, intervalMs);
  }
  function stopTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }
  const onVisibility = () => {
    if (document.hidden) stopTimer();
    else startTimer();
  };

  function start() {
    document.addEventListener("visibilitychange", onVisibility);
    startTimer();
  }
  function stop() {
    document.removeEventListener("visibilitychange", onVisibility);
    stopTimer();
  }

  return { start, stop, tick };
}
