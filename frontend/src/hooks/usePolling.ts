import { useEffect, useRef } from "react";

// 定时轮询。active 为 false 时不启动（切 tab 时停）。
export function usePolling(fn: () => void, intervalMs: number, active: boolean): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => fnRef.current(), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, active]);
}
