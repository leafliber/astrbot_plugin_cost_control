import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { Loading, ErrorBox } from "../components/Feedback";

type FieldType = "bool" | "str" | "int" | "csv";

interface SettingField {
  k: string;
  label: string;
  type: FieldType;
}

interface SettingSection {
  key: string;
  title: string;
  fields: SettingField[];
}

const SECTIONS: SettingSection[] = [
  {
    key: "_master",
    title: "总开关",
    fields: [
      { k: "enabled", label: "启用插件", type: "bool" },
      { k: "refresh_time", label: "每日重置时间 (HH:MM)", type: "str" },
      { k: "match_unique_session", label: "匹配唯一会话", type: "bool" },
      { k: "platforms", label: "生效平台（逗号分隔，空=全部）", type: "csv" },
    ],
  },
  {
    key: "cache_diag",
    title: "缓存诊断",
    fields: [
      { k: "detect_context_reset", label: "上下文重置检测", type: "bool" },
      { k: "detect_system_prompt_change", label: "system prompt 变更检测", type: "bool" },
      { k: "detect_tools_change", label: "工具定义变更检测", type: "bool" },
      { k: "detect_order_drift", label: "上下文顺序漂移检测", type: "bool" },
      { k: "cache_hit_rate_alert_threshold", label: "命中率告警阈值 (%)，0=不告警", type: "int" },
    ],
  },
  {
    key: "alerts",
    title: "告警",
    fields: [
      { k: "enabled", label: "启用超预算主动推送", type: "bool" },
      { k: "cooldown_seconds", label: "冷却时间（秒）", type: "int" },
      { k: "daily_report_time", label: "日报推送时间 (HH:MM，空=不推)", type: "str" },
      { k: "daily_report_to", label: "日报目标 UMO（逗号分隔）", type: "csv" },
    ],
  },
  {
    key: "prompt_optimizer",
    title: "提示词优化",
    fields: [
      { k: "enabled", label: "启用 /optimize", type: "bool" },
      { k: "provider_id", label: "改写 Provider ID（空=当前会话）", type: "str" },
      { k: "max_static_analysis_length", label: "静态分析最大长度（字符）", type: "int" },
    ],
  },
  {
    key: "attribution",
    title: "上下文分析",
    fields: [
      { k: "enabled", label: "启用上下文注入归因", type: "bool" },
      { k: "sample_rate", label: "采样率 (%)，100=全采样", type: "int" },
    ],
  },
  {
    key: "schedule",
    title: "定时任务",
    fields: [
      { k: "enable_daily_report", label: "启用每日报告 CronJob", type: "bool" },
      { k: "retain_days", label: "历史保留天数（0=永不清理）", type: "int" },
    ],
  },
];

function valOf(cfg: Record<string, unknown>, sec: string, k: string): unknown {
  const v =
    sec === "_master"
      ? cfg[k]
      : (cfg[sec] as Record<string, unknown> | undefined)?.[k];
  return v === undefined || v === null ? "" : v;
}

export function SettingsView() {
  const res = useApi(() => api.getConfig(), []);
  const [edit, setEdit] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState("");
  const [actionResult, setActionResult] = useState("");

  useEffect(() => {
    if (res.data) setEdit(JSON.parse(JSON.stringify(res.data)));
  }, [res.data]);

  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载设置失败：${res.error}`} />;

  const setField = (sec: string, k: string, type: FieldType, raw: string | boolean) => {
    setEdit((prev) => {
      const next: Record<string, unknown> = { ...prev };
      let value: unknown = raw;
      if (type === "bool") value = !!raw;
      else if (type === "int") value = Math.max(0, parseInt(String(raw), 10) || 0);
      else if (type === "csv")
        value = String(raw)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      else value = String(raw);
      if (sec === "_master") {
        next[k] = value;
      } else {
        const scope = (next[sec] as Record<string, unknown> | undefined) || {};
        next[sec] = { ...scope, [k]: value };
      }
      return next;
    });
  };

  const save = async () => {
    setResult("保存中…");
    try {
      const r = await api.postSaveConfig(edit);
      setResult(`✅ 已保存（${(r.saved || []).join(", ")}），立即生效`);
      res.refetch();
    } catch (e) {
      setResult(`❌ 保存失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const cleanup = async () => {
    setActionResult("执行中…");
    try {
      const r = await api.postCleanup();
      setActionResult(`已清理 ${fmtNum(r.deleted || 0)} 条记录`);
    } catch (e) {
      setActionResult(`失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const report = async () => {
    setActionResult("执行中…");
    try {
      await api.postReport();
      setActionResult("日报已触发推送");
    } catch (e) {
      setActionResult(`失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div>
      {SECTIONS.map((sec) => (
        <Panel key={sec.key}>
          <h2>{sec.title}</h2>
          {sec.fields.map((f) => {
            const v = valOf(edit, sec.key, f.k);
            if (f.type === "bool") {
              return (
                <div className="set-row" key={f.k}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!!v}
                      onChange={(e) => setField(sec.key, f.k, "bool", e.target.checked)}
                    />{" "}
                    {f.label}
                  </label>
                </div>
              );
            }
            if (f.type === "csv") {
              return (
                <div className="set-row" key={f.k}>
                  <label style={{ flex: 1 }}>
                    {f.label}{" "}
                    <input
                      type="text"
                      className="budget-input"
                      defaultValue={Array.isArray(v) ? v.join(", ") : String(v || "")}
                      onBlur={(e) => setField(sec.key, f.k, "csv", e.target.value)}
                      style={{ width: "100%" }}
                    />
                  </label>
                </div>
              );
            }
            return (
              <div className="set-row" key={f.k}>
                <label style={{ flex: 1 }}>
                  {f.label}{" "}
                  <input
                    type={f.type === "int" ? "number" : "text"}
                    className="budget-input"
                    value={v === "" ? "" : String(v)}
                    onChange={(e) => setField(sec.key, f.k, f.type, e.target.value)}
                    style={{ width: 160 }}
                  />
                </label>
              </div>
            );
          })}
        </Panel>
      ))}

      <Panel>
        <h2>手动操作</h2>
        <div className="row">
          <Button onClick={cleanup}>清理过期数据</Button>
          <Button onClick={report}>推送日报</Button>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          {actionResult}
        </div>
      </Panel>

      <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 4 }}>
        <Button variant="primary" onClick={save}>
          保存（热生效）
        </Button>
        <span className="muted">{result}</span>
      </div>
    </div>
  );
}
