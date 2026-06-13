import type { useSessionsStore } from "@/stores/sessions";

type Store = Pick<ReturnType<typeof useSessionsStore>, "live" | "pollTick">;

/**
 * 5s active-only 增量轮询。document.hidden（标签页不可见）暂停、可见恢复——省后端读。
 * 无内部生命周期钩子：由调用方在 onMounted(start)/onUnmounted(stop) 接管。
 */
export function usePolling(store: Store, intervalMs = 5000) {
  let timer: ReturnType<typeof setInterval> | null = null;

  const tick = () => {
    if (typeof document !== "undefined" && document.hidden) return;
    // 读 live.status（每 tick 由 pollTick 刷新），而非由 loadSessions 一次性填充、此后不刷新的
    // 列表行状态——否则会话运行中 active→paused 后门控仍读到 active，轮询永不自停。
    if (store.live?.status !== "active") return;
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
    if (document.hidden) {
      stopTimer();
    } else {
      tick(); // 重新可见立即补一拍，消除重聚焦后的 5s 陈旧窗（仅 resume，不在 start() 首挂时，避免与 selectSession 首拉重复）
      startTimer();
    }
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
