import type { FallbackProvider, Provider } from "../lib/types";

export function FallbackProvidersPanel({
  providers,
  realProviders,
  onChange,
  onDelete,
  onAdd,
}: {
  providers: FallbackProvider[];
  realProviders?: Provider[];
  onChange: (i: number, patch: Partial<FallbackProvider>) => void;
  onDelete: (i: number) => void;
  onAdd: (id?: string) => void;
}) {
  return (
    <div className="fallback-providers">
      <div className="muted small" style={{ marginBottom: 8 }}>
        备用 Provider 库：被「局部阈值」规则的 on_exceeded=fallback 引用。
        可填与下方「实际 Provider」不同的标识（人工兜底 ID）；实际可调用性以
        <code> context.get_provider_by_id </code> 为准。
      </div>
      {realProviders && realProviders.length > 0 && (
        <div className="muted small" style={{ marginBottom: 6 }}>
          实际 Provider（点击快速添加）：
          {realProviders.map((p) => (
            <button
              key={p.id}
              type="button"
              className="chip"
              onClick={() => onAdd(p.id)}
              style={{ marginLeft: 6 }}
            >
              + {p.id}
              {p.model ? ` (${p.model})` : ""}
            </button>
          ))}
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th style={{ width: 30 }}></th>
            <th>Provider ID</th>
            <th>备注</th>
            <th style={{ width: 40 }}></th>
          </tr>
        </thead>
        <tbody>
          {providers.length === 0 ? (
            <tr>
              <td colSpan={4} className="muted small" style={{ textAlign: "center" }}>
                暂无备用 Provider
              </td>
            </tr>
          ) : (
            providers.map((p, i) => (
              <tr key={`${p.id}-${i}`}>
                <td>
                  <input
                    type="checkbox"
                    checked={p.enabled}
                    onChange={(e) => onChange(i, { enabled: e.target.checked })}
                  />
                </td>
                <td>
                  <input
                    className="budget-input mono"
                    value={p.id}
                    onChange={(e) => onChange(i, { id: e.target.value })}
                    style={{ width: "100%" }}
                  />
                </td>
                <td>
                  <input
                    className="budget-input"
                    value={p.note || ""}
                    onChange={(e) => onChange(i, { note: e.target.value })}
                    style={{ width: "100%" }}
                    placeholder="（可选）"
                  />
                </td>
                <td>
                  <button type="button" className="btn" onClick={() => onDelete(i)}>
                    ✕
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div style={{ marginTop: 8 }}>
        <button type="button" className="btn" onClick={() => onAdd()}>
          + 添加备用 Provider
        </button>
      </div>
    </div>
  );
}
