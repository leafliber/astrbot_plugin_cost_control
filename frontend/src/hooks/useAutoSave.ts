import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

interface UseAutoSaveOptions {
  /** 防抖延迟（ms），默认 800。 */
  delay?: number;
  /** 「已保存 / 失败」浮层停留时长（ms），默认 1500（失败 ×3）。 */
  toastMs?: number;
  /** 门控：false 期间不保存、不 seed。用于等待初始数据 hydrate 后再启用，
   * 避免首屏 state 初始化触发一次无意义保存。默认 true。 */
  enabled?: boolean;
}

interface UseAutoSaveResult {
  status: SaveStatus;
  error?: string;
  /** 强制立即保存当前待保存的 payload（若有变化）。 */
  flush: () => Promise<void>;
}

// 防抖自动保存：监听 payload 的 JSON 序列化值，变化时延迟 delay ms 调 onSave。
// 设计要点：
// - 用 enabled 门控首屏：view 在 hydrate 完成后才置 true，避免初始化触发保存；
//   enabled 由 false→true 的瞬间在 layout effect 里 seed lastSaved = 当前 serialized，
//   紧随其后的 debounce effect 看到 serialized===lastSaved 即跳过。
// - 串行保存：inFlight 标志阻止并发，避免「旧请求后 resolve 覆盖新请求」的竞态。
// - 卸载 flush：切 tab / 关页面时若有未保存改动，尽力 fire-and-forget 保存。
export function useAutoSave<T>(
  payload: T,
  onSave: (payload: T) => Promise<void>,
  opts: UseAutoSaveOptions = {},
): UseAutoSaveResult {
  const { delay = 800, toastMs = 1500, enabled = true } = opts;

  const serialized = useMemo(() => JSON.stringify(payload), [payload]);
  const serializedRef = useRef(serialized);
  serializedRef.current = serialized;
  const payloadRef = useRef(payload);
  payloadRef.current = payload;
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | undefined>(undefined);

  const lastSaved = useRef<string | null>(null); // null = 尚未 seed
  const prevEnabled = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);

  const clearReset = useCallback(() => {
    if (resetRef.current !== null) {
      clearTimeout(resetRef.current);
      resetRef.current = null;
    }
  }, []);

  const runSave = useCallback(async () => {
    // 已无变化（可能 reschedule 后被其它保存清掉）→ no-op。
    if (lastSaved.current !== null && serializedRef.current === lastSaved.current) {
      return;
    }
    if (inFlightRef.current) {
      // 一次保存进行中，延后重试（避免并发覆盖）。
      timerRef.current = setTimeout(() => {
        void runSave();
      }, delay);
      return;
    }
    inFlightRef.current = true;
    setStatus("saving");
    try {
      await onSaveRef.current(payloadRef.current);
      lastSaved.current = serializedRef.current;
      setStatus("saved");
      setError(undefined);
      clearReset();
      resetRef.current = setTimeout(() => setStatus("idle"), toastMs);
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
      clearReset();
      resetRef.current = setTimeout(() => setStatus("idle"), toastMs * 3);
    } finally {
      inFlightRef.current = false;
    }
  }, [delay, toastMs, clearReset]);

  // enabled 由 false→true 时 seed lastSaved（此时 serializedRef 已反映 hydrate 后的值）。
  useLayoutEffect(() => {
    if (enabled && !prevEnabled.current) {
      lastSaved.current = serializedRef.current;
    }
    prevEnabled.current = enabled;
  }, [enabled]);

  // 监听变化 → 防抖保存。
  useEffect(() => {
    if (!enabled) return;
    if (lastSaved.current === null) return;
    if (serialized === lastSaved.current) return;
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void runSave();
    }, delay);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [serialized, enabled, delay, runSave]);

  // 卸载 flush：若有未保存改动且无在途保存，尽力保存（fire-and-forget）。
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      clearReset();
      if (
        enabled &&
        lastSaved.current !== null &&
        !inFlightRef.current &&
        serializedRef.current !== lastSaved.current
      ) {
        void onSaveRef.current(payloadRef.current).catch(() => {});
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const flush = useCallback(async () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (
      enabled &&
      lastSaved.current !== null &&
      !inFlightRef.current &&
      serializedRef.current !== lastSaved.current
    ) {
      await runSave();
    }
  }, [enabled, runSave]);

  return { status, error, flush };
}
