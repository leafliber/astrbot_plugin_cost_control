// Bridge SDK 封装：window.AstrBotPluginPage 由 dashboard 注入，经父级 SPA 代发
// REST（自动补 /api/plug/<pluginName>/ 前缀 + 带 dashboard JWT）。

export interface BridgeContext {
  pluginName?: string;
  displayName?: string;
  pageName?: string;
  pageTitle?: string;
  locale?: string;
  isDark?: boolean;
}

export interface BridgePage {
  ready(): Promise<void>;
  getContext?(): BridgeContext | null;
  onContext?(fn: (ctx: BridgeContext) => void): void;
  apiGet(endpoint: string, params?: Record<string, unknown>): Promise<unknown>;
  apiPost(endpoint: string, body?: unknown): Promise<unknown>;
}

declare global {
  interface Window {
    AstrBotPluginPage?: BridgePage;
  }
}

export function getBridge(): BridgePage | null {
  return typeof window !== "undefined" ? window.AstrBotPluginPage ?? null : null;
}

// 轮询等待 bridge 注入，超时返回 null（与原 app.js waitForBridge 行为一致）
export function waitForBridge(timeoutMs = 5000): Promise<BridgePage | null> {
  return new Promise((resolve) => {
    const existing = getBridge();
    if (existing) return resolve(existing);
    const start = Date.now();
    const t = setInterval(() => {
      const b = getBridge();
      if (b) {
        clearInterval(t);
        resolve(b);
      } else if (Date.now() - start > timeoutMs) {
        clearInterval(t);
        resolve(null);
      }
    }, 50);
  });
}

// DEV 模式 mock：本地 vite dev 无真实 bridge，注入空数据 mock 供 UI 布局预览。
// 端到端验证须用真实 AstrBot（build → 软链 → 重载）。
function createDevMockBridge(): BridgePage {
  const emptyOverview = {
    cost: 0,
    usage: { count: 0, token_input_other: 0, token_input_cached: 0, token_output: 0 },
    cache_hit_rate: 0,
    cache_samples: 0,
    avg_injection: 0,
    injection_samples: 0,
    cost_by_model: [],
    top_sessions: [],
  };
  return {
    ready: async () => {},
    getContext: () => ({ isDark: false, locale: "zh-CN", displayName: "DEV" }),
    onContext: () => {},
    async apiGet(endpoint: string) {
      if (endpoint === "overview") return { success: true, data: emptyOverview };
      if (endpoint === "timeline")
        return { success: true, data: { series: [], bucket: "day", days: 7 } };
      return { success: true, data: null };
    },
    async apiPost() {
      return { success: true, data: null };
    },
  };
}

if (import.meta.env.DEV && typeof window !== "undefined" && !window.AstrBotPluginPage) {
  window.AstrBotPluginPage = createDevMockBridge();
}
