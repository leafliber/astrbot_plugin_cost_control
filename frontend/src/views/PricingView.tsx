import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtNum } from "../lib/format";
import type { PriceEntry } from "../lib/types";
import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { PresetSuggest } from "../components/PresetSuggest";
import { Loading, ErrorBox } from "../components/Feedback";

interface PriceRow {
  model: string;
  input: string;
  input_cached: string;
  output: string;
  cache_creation: string;
}

const NUM_FIELDS: (keyof Omit<PriceRow, "model">)[] = [
  "input",
  "input_cached",
  "output",
  "cache_creation",
];

function entryToRow(model: string, p: PriceEntry | undefined): PriceRow {
  return {
    model,
    input: p?.input != null ? String(p.input) : "",
    input_cached: p?.input_cached != null ? String(p.input_cached) : "",
    output: p?.output != null ? String(p.output) : "",
    cache_creation: p?.cache_creation != null ? String(p.cache_creation) : "",
  };
}

export function PricingView() {
  const res = useApi(() => api.getPricing(), []);
  const data = res.data;
  const [rows, setRows] = useState<PriceRow[]>([]);
  const [result, setResult] = useState("");

  useEffect(() => {
    if (!data) return;
    const pricing = data.pricing || {};
    setRows(Object.keys(pricing).sort().map((k) => entryToRow(k, pricing[k])));
  }, [data]);

  if (res.loading && !data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载定价失败：${res.error}`} />;

  const defaults = data?.defaults || {};
  const unpriced = data?.unpriced || [];

  const updateField = (i: number, field: keyof PriceRow, value: string) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  const delRow = (i: number) => setRows((prev) => prev.filter((_, idx) => idx !== i));
  const addRow = (model = "", p?: PriceEntry) =>
    setRows((prev) => [...prev, entryToRow(model, p)]);

  const pickPreset = (model: string) => {
    if (rows.some((r) => r.model.trim() === model)) return;
    addRow(model, defaults[model]);
  };

  const collect = (): Record<string, PriceEntry> => {
    const out: Record<string, PriceEntry> = {};
    rows.forEach((r) => {
      const model = r.model.trim();
      if (!model) return;
      if (Object.prototype.hasOwnProperty.call(out, model)) {
        throw new Error(`重复模型名：${model}`);
      }
      const entry: PriceEntry = {};
      NUM_FIELDS.forEach((f) => {
        const raw = r[f];
        if (raw === "") return;
        const n = parseFloat(raw);
        if (Number.isNaN(n) || n < 0) throw new Error(`${model} 的 ${f} 非法数值`);
        entry[f] = n;
      });
      out[model] = entry;
    });
    return out;
  };

  const save = async () => {
    let body;
    try {
      body = { pricing: collect() };
    } catch (e) {
      setResult(`❌ ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setResult("保存中…");
    try {
      await api.postSaveConfig(body);
      setResult("✅ 已保存，立即生效");
      res.refetch();
    } catch (e) {
      setResult(`❌ 保存失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const reset = async () => {
    if (!confirm("确定清空所有自定义定价、恢复内置默认？")) return;
    setResult("重置中…");
    try {
      await api.postSaveConfig({ pricing: {} });
      setResult("✅ 已重置为内置默认，立即生效");
      res.refetch();
    } catch (e) {
      setResult(`❌ 重置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div>
      {unpriced.length > 0 && (
        <Panel className="alert-panel">
          <h2>未定价模型告警</h2>
          <div className="alert-body">
            以下模型有用量但未配置单价，其成本被计为 <strong>$0</strong>
            ，会导致成本统计偏低。请在下方定价表补充单价。
          </div>
          <table>
            <thead>
              <tr>
                <th>模型</th>
                <th>用量 token</th>
                <th>调用</th>
              </tr>
            </thead>
            <tbody>
              {unpriced.map((u) => (
                <tr key={u.model}>
                  <td className="mono">{u.model}</td>
                  <td>{fmtNum(u.tokens)}</td>
                  <td>{fmtNum(u.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <Panel>
        <h2>模型定价</h2>
        <div className="muted small" style={{ marginBottom: 8 }}>
          单位 USD / 百万 token。修改后点「保存」即时热生效；某项留空表示沿用默认值（不覆盖）。
        </div>
        <div className="row" style={{ gap: 8, marginBottom: 8 }}>
          <Button onClick={() => addRow()}>+ 添加模型</Button>
          <Button onClick={reset} title="清空自定义定价，恢复内置默认">
            重置为内置默认
          </Button>
        </div>
        <PresetSuggest defaults={defaults} onPick={pickPreset} />
        <table>
          <thead>
            <tr>
              <th>模型</th>
              <th>输入</th>
              <th>缓存命中</th>
              <th>输出</th>
              <th>缓存写入</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>
                  <input
                    className="budget-input mono"
                    value={r.model}
                    onChange={(e) => updateField(i, "model", e.target.value)}
                    style={{ width: 180 }}
                  />
                </td>
                {NUM_FIELDS.map((f) => (
                  <td key={f}>
                    <input
                      className="budget-input"
                      type="number"
                      step="any"
                      min="0"
                      value={r[f]}
                      onChange={(e) => updateField(i, f, e.target.value)}
                      style={{ width: 90 }}
                    />
                  </td>
                ))}
                <td>
                  <button className="btn" title="删除该行" onClick={() => delRow(i)}>
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 8, gap: 10, alignItems: "center" }}>
          <Button variant="primary" onClick={save}>
            保存定价（热生效）
          </Button>
          <span className="muted">{result}</span>
        </div>
      </Panel>

      {Object.keys(defaults).length > 0 && (
        <details className="panel">
          <summary>
            内置默认单价（参考 OpenRouter，共 {Object.keys(defaults).length} 个模型，只读）
          </summary>
          <div className="muted small" style={{ margin: "6px 0" }}>
            随插件版本更新；作为上方编辑表的预填基准与「重置为内置默认」的目标。
          </div>
          <table>
            <thead>
              <tr>
                <th>模型</th>
                <th>输入</th>
                <th>缓存命中</th>
                <th>输出</th>
                <th>缓存写入</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(defaults)
                .sort()
                .map((k) => {
                  const p = defaults[k] || {};
                  return (
                    <tr key={k}>
                      <td className="mono">{k}</td>
                      <td>{p.input != null ? p.input : "-"}</td>
                      <td>{p.input_cached != null ? p.input_cached : "-"}</td>
                      <td>{p.output != null ? p.output : "-"}</td>
                      <td>{p.cache_creation != null ? p.cache_creation : "-"}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
