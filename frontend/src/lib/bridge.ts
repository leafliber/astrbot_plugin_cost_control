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
      if (endpoint === "pricing")
        return {
          success: true,
          data: {
            provider_models: [
              {
                id: "openai-gpt4o",
                model: "gpt-4o",
                type: "openai_chat_completion",
                candidates: ["gpt-4o", "gpt-4o-mini"],
                matched_default: {
                  model: "gpt-4o",
                  entry: { input: 2.5, input_cached: 1.25, output: 10, cache_creation: null },
                },
              },
              {
                id: "claude-sonnet",
                model: "claude-sonnet-4-5-20250929",
                type: "anthropic",
                candidates: ["claude-sonnet-4-5-20250929", "claude-haiku-4-5"],
                matched_default: {
                  model: "claude-sonnet-4-5",
                  entry: { input: 3, input_cached: 0.3, output: 15, cache_creation: 3.75 },
                },
              },
              {
                id: "deepseek-chat",
                model: "deepseek-chat",
                type: "deepseek",
                candidates: ["deepseek-chat"],
                matched_default: {
                  model: "deepseek-chat",
                  entry: { input: 0.27, input_cached: 0.07, output: 1.1, cache_creation: null },
                },
              },
              {
                id: "custom-finetune",
                model: "my-finetune-v2",
                type: "openai_chat_completion",
                candidates: ["my-finetune-v2"],
                matched_default: null,
              },
            ],
            user_pricing: {
              "deepseek-chat": { mode: "per_token", input: 0.14, input_cached: 0.014, output: 0.28 },
            },
            defaults: {
              "gpt-4o": { input: 2.5, input_cached: 1.25, output: 10 },
              "gpt-4o-mini": { input: 0.15, input_cached: 0.075, output: 0.6 },
              "claude-sonnet-4-5": { input: 3, input_cached: 0.3, output: 15, cache_creation: 3.75 },
              "deepseek-chat": { input: 0.27, input_cached: 0.07, output: 1.1 },
            },
            unpriced: [
              { provider_id: "custom-finetune", model: "my-finetune-v2", tokens: 15200, count: 8 },
            ],
          },
        };
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
