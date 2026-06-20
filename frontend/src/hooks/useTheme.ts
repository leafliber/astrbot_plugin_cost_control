import { useLayoutEffect } from "react";
import type { BridgeContext } from "../lib/bridge";

// 由 ctx.isDark 驱动 body[data-theme]，CSS 变量系统据此切换主题。
// 用 useLayoutEffect 避免首屏主题闪烁（DOM 在 paint 前更新）。
export function useTheme(ctx: BridgeContext | null): void {
  const isDark = !!ctx?.isDark;
  useLayoutEffect(() => {
    document.body.dataset.theme = isDark ? "dark" : "light";
  }, [isDark]);
}
