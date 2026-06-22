import type { BudgetOverrideRow, Provider } from "../lib/types";
import { OverrideRow } from "./OverrideRow";
import type { FallbackProvider } from "../lib/types";

export function OverridesPanel({
  overrides,
  providers,
  fallbackProviders,
  onChange,
  onMove,
  onDelete,
  onAdd,
}: {
  overrides: BudgetOverrideRow[];
  providers?: Provider[];
  fallbackProviders: FallbackProvider[];
  onChange: (i: number, patch: Partial<BudgetOverrideRow>) => void;
  onMove: (i: number, dir: "up" | "down") => void;
  onDelete: (i: number) => void;
  onAdd: () => void;
}) {
  return (
    <div className="overrides-panel">
      <div className="muted small" style={{ marginBottom: 8 }}>
        按序匹配，第一条命中即生效：token / cost 任一超限 → 按本规则的<b> 超限处理 </b>执行（不走全局）；未命中或未超限 → 回落全局。
      </div>
      {overrides.length === 0 ? (
        <div className="muted small" style={{ textAlign: "center", padding: "20px 0" }}>
          暂无规则（仅按全局预算生效）
        </div>
      ) : (
        <div className="override-list">
          {overrides.map((o, i) => (
            <OverrideRow
              key={o.id || `ov-${i}`}
              row={o}
              index={i}
              total={overrides.length}
              providers={providers}
              fallbackProviders={fallbackProviders}
              onChange={(patch) => onChange(i, patch)}
              onMove={(dir) => onMove(i, dir)}
              onDelete={() => onDelete(i)}
            />
          ))}
        </div>
      )}
      <div style={{ marginTop: 8 }}>
        <button type="button" className="btn" onClick={onAdd}>
          + 添加规则
        </button>
      </div>
    </div>
  );
}
