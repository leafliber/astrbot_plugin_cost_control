// 从原 app.js 平移的格式化纯函数。React 默认转义文本，无需 esc。

// 货币代码 → 显示符号
export const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  CNY: "¥",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
  KRW: "₩",
  INR: "₹",
  HKD: "HK$",
  SGD: "S$",
  TWD: "NT$",
  RUB: "₽",
  BRL: "R$",
};

// 可选货币列表（下拉选项用）
export const CURRENCY_OPTIONS = Object.keys(CURRENCY_SYMBOLS);

// 模块级主货币代码，App 挂载时通过 setCurrencyCode 注入
let _currencyCode = "USD";

export function setCurrencyCode(code: string): void {
  const c = String(code || "").trim().toUpperCase();
  if (c) _currencyCode = c;
}

export function getCurrencyCode(): string {
  return _currencyCode;
}

export function currencyToSymbol(code?: string | null): string {
  const c = String(code || "").trim().toUpperCase();
  return CURRENCY_SYMBOLS[c] || c || "$";
}

export function fmtNum(n: number | undefined | null): string {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "0";
  return v.toLocaleString("zh-CN");
}

export function fmtCost(n: number | undefined | null, symbolOrCode?: string): string {
  const v = Number(n ?? 0);
  const sym = symbolOrCode ? currencyToSymbol(symbolOrCode) : currencyToSymbol(_currencyCode);
  if (!Number.isFinite(v)) return sym + "0.0000";
  return sym + (v < 0.01 && v > 0 ? v.toFixed(6) : v.toFixed(4));
}

export function fmtCompact(n: number | undefined | null): string {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return "0";
  if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(Math.round(v));
}

export function shortDate(iso: string | undefined | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function shortTime(iso: string | undefined | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortModelName(m: string | undefined | null): string {
  const s = String(m ?? "?");
  return s.length > 24 ? s.slice(0, 22) + "…" : s;
}

export function shortUmo(u: string | undefined | null): string {
  const s = String(u ?? "?");
  if (s.length <= 16) return s;
  return s.slice(0, 8) + "…" + s.slice(-6);
}

// 读取 CSS 变量（图表需要把主题色喂给 recharts）
export function cssVar(name: string, fallback = ""): string {
  try {
    const v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

export function windowToDays(w: Window | string): number {
  return w === "monthly" ? 90 : w === "weekly" ? 30 : 7;
}

export function windowLabel(w: Window | string): string {
  return w === "monthly" ? "近 30 天" : w === "weekly" ? "近 7 天" : "今日";
}
