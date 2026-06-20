import { useEffect, useState } from "react";
import { waitForBridge, type BridgeContext, type BridgePage } from "../lib/bridge";

export interface BridgeState {
  page: BridgePage | null;
  ready: boolean;
  ctx: BridgeContext | null;
  failed: boolean;
}

// waitForBridge → ready → onContext 订阅，返回 bridge 状态与上下文
export function useBridge(): BridgeState {
  const [page, setPage] = useState<BridgePage | null>(null);
  const [ready, setReady] = useState(false);
  const [ctx, setCtx] = useState<BridgeContext | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    waitForBridge(5000).then(async (p) => {
      if (cancelled) return;
      if (!p) {
        setFailed(true);
        return;
      }
      setPage(p);
      try {
        await p.ready();
      } catch {
        /* ready 失败也继续尝试调用 */
      }
      if (cancelled) return;
      setReady(true);
      try {
        const c = p.getContext ? p.getContext() : null;
        if (c) setCtx(c);
        if (p.onContext) {
          p.onContext((next) => {
            if (!cancelled && next) setCtx(next);
          });
        }
      } catch {
        /* 上下文读取失败不阻断 */
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return { page, ready, ctx, failed };
}
