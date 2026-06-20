import { useState } from "react";
import type { PriceEntry } from "../lib/types";

function priceNum(v?: number): string {
  return v != null ? String(v) : "-";
}

// 定价预设搜索：关键词模糊匹配内置 defaults，点击触发 onPick（由父组件添加行）。
export function PresetSuggest({
  defaults,
  onPick,
}: {
  defaults: Record<string, PriceEntry>;
  onPick: (model: string) => void;
}) {
  const [q, setQ] = useState("");
  const ql = q.trim().toLowerCase();
  const keys = Object.keys(defaults)
    .filter((k) => !ql || k.toLowerCase().includes(ql))
    .sort()
    .slice(0, ql ? 30 : 10);
  return (
    <div style={{ marginBottom: 8 }}>
      <input
        className="budget-input"
        placeholder="搜索内置预设快速填充（输入 qwen / glm / doubao 等关键词）"
        style={{ width: "100%" }}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div
        style={{
          maxHeight: 170,
          overflow: "auto",
          marginTop: 4,
          border: "1px solid var(--border)",
          borderRadius: 6,
        }}
      >
        {keys.length === 0 ? (
          <div className="muted small" style={{ padding: 6 }}>
            无匹配预设
          </div>
        ) : (
          keys.map((k) => {
            const p = defaults[k] || {};
            return (
              <div
                key={k}
                className="pr-suggest-item"
                onClick={() => {
                  onPick(k);
                  setQ("");
                }}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 8,
                  padding: "5px 8px",
                  cursor: "pointer",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span className="mono">{k}</span>
                <span className="muted small">
                  输入 {priceNum(p.input)} · 输出 {priceNum(p.output)}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
