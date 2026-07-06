import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { useAutoSave } from "../hooks/useAutoSave";
import { fmtNum, CURRENCY_OPTIONS, currencyToSymbol } from "../lib/format";
import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { SaveToast } from "../components/SaveToast";
import { Loading, ErrorBox } from "../components/Feedback";

type FieldType = "bool" | "str" | "int" | "csv" | "select";

interface SettingField {
  k: string;
  label: string;
  type: FieldType;
  help?: string;
  /** input 宽度（px），仅对 int/str 生效；csv 默认撑满。 */
  width?: number;
  /** select 类型的可选值 */
  options?: string[];
}

interface SettingSection {
  key: string;
  title: string;
  desc?: string;
  fields: SettingField[];
}

const SECTIONS: SettingSection[] = [
  {
    key: "_master",
    title: "总开关与全局",
    desc: "插件的启停、用量「日」窗口的起算时刻，以及主货币等。",
    fields: [
      {
        k: "enabled",
        label: "启用插件",
        type: "bool",
        help: "关闭后插件完全停止：不采集用量、不拦截请求、不推送告警与日报。",
      },
      {
        k: "refresh_time",
        label: "日窗口起算时刻",
        type: "str",
        width: 100,
        help: "本地时区 HH:MM。预算计数与用量报表都按此划分「一天」。例如 09:00 表示 09:00 至次日 09:00 算作一天。",
      },
      {
        k: "currency_symbol",
        label: "主货币",
        type: "select",
        options: CURRENCY_OPTIONS,
        help: "所有费用最终换算并以此货币结算和显示。内置定价以 USD 计价，切换后自动按汇率交叉换算。",
      },
    ],
  },
  {
    key: "cache_diag",
    title: "缓存诊断",
    desc: "LLM 通常对重复上下文做缓存，命中后计费的 token 更少。下列检测用于发现缓存意外失效、导致成本上升的情形。",
    fields: [
      {
        k: "detect_context_reset",
        label: "对话历史被重置",
        type: "bool",
        help: "新一轮历史突变或被清空时标记——此前缓存的上下文失效。",
      },
      {
        k: "detect_system_prompt_change",
        label: "系统提示词变更",
        type: "bool",
        help: "system prompt 发生变化时标记——缓存 key 改变而失效。",
      },
      {
        k: "detect_tools_change",
        label: "工具定义变更",
        type: "bool",
        help: "function calling 的工具列表发生变化时标记——缓存失效。",
      },
      {
        k: "detect_order_drift",
        label: "消息顺序漂移",
        type: "bool",
        help: "历史消息顺序被打乱时标记——请求前缀与已缓存内容对不上。",
      },
      {
        k: "cache_hit_rate_alert_enabled",
        label: "启用命中率告警推送",
        type: "bool",
        help: "开启后，当本轮缓存命中率低于下方阈值时，会向当前会话推送一条告警消息。默认关闭，避免刷屏。",
      },
      {
        k: "cache_hit_rate_alert_threshold",
        label: "命中率告警阈值 (%)",
        type: "int",
        width: 100,
        help: "缓存命中率低于此值时告警；0 = 不告警。需先开启上方「启用命中率告警推送」开关。",
      },
    ],
  },
  {
    key: "alerts",
    title: "告警与日报",
    desc: "超预算提醒的推送策略，以及日报的推送时间与接收方。",
    fields: [
      {
        k: "enabled",
        label: "启用超预算主动推送",
        type: "bool",
        help: "超限时主动发消息提醒。关闭后仍会按策略拦截请求，只是不再推送提醒。",
      },
      {
        k: "cooldown_seconds",
        label: "告警冷却（秒）",
        type: "int",
        width: 100,
        help: "同一告警的最短重复间隔，避免刷屏；0 = 不冷却（每次超限都推）。",
      },
      {
        k: "daily_report_time",
        label: "日报推送时间",
        type: "str",
        width: 100,
        help: "本地时区 HH:MM。需同时开启「定时任务 → 启用每日用量日报」才会到点自动推送。",
      },
      {
        k: "daily_report_to",
        label: "日报接收方",
        type: "csv",
        help: "日报接收方的会话 / UMO ID 列表，逗号分隔。",
      },
    ],
  },
  {
    key: "prompt_optimizer",
    title: "提示词优化",
    desc: "/optimize 命令：分析并改写 system prompt，帮助降低 token 占用。",
    fields: [
      {
        k: "enabled",
        label: "启用 /optimize 命令",
        type: "bool",
        help: "关闭后 /optimize 命令不可用。",
      },
      {
        k: "provider_id",
        label: "改写 Provider ID",
        type: "str",
        width: 180,
        help: "执行改写所用的 LLM Provider；留空 = 使用当前会话的 Provider。",
      },
      {
        k: "max_static_analysis_length",
        label: "静态分析最大长度（字符）",
        type: "int",
        width: 120,
        help: "静态分析阶段读取 system prompt 的最大字符数。",
      },
    ],
  },
  {
    key: "attribution",
    title: "上下文归因",
    desc: "拆分每次请求的 token 来源占比（系统提示词 / 工具 / 历史对话 / 用户输入），看清上下文膨胀的构成。",
    fields: [
      {
        k: "enabled",
        label: "启用上下文归因分析",
        type: "bool",
        help: "开启后会估算并拆分每次 LLM 请求的 token 来源占比。",
      },
      {
        k: "sample_rate",
        label: "采样率 (%)",
        type: "int",
        width: 100,
        help: "归因分析的采样百分比，100 = 每次都分析；调低可减少开销。",
      },
    ],
  },
  {
    key: "schedule",
    title: "定时任务",
    desc: "每日用量日报与过期数据的自动清理。",
    fields: [
      {
        k: "enable_daily_report",
        label: "启用每日用量日报",
        type: "bool",
        help: "开启后，每天到「告警与日报 → 日报推送时间」自动推送一次用量汇总。",
      },
      {
        k: "retain_days",
        label: "历史保留天数",
        type: "int",
        width: 100,
        help: "补充记录的保留天数，到期后定时清理；0 = 永不清理。",
      },
    ],
  },
  {
    key: "advanced",
    title: "高级",
    desc: "非默认场景下的可选配置，普通用户无需调整。",
    fields: [
      {
        k: "platforms",
        label: "生效平台",
        type: "csv",
        help: "限定插件只处理这些平台的请求（如 aiocqhttp、telegram_official、lark）；留空 = 对所有平台生效。",
      },
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

export function SettingsView({
  onCurrencyChanged,
}: {
  onCurrencyChanged?: () => void;
}) {
  const res = useApi(() => api.getConfig(), []);
  const [edit, setEdit] = useState<Record<string, unknown>>({});
  const [ready, setReady] = useState(false);
  const [actionResult, setActionResult] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [ratesOpen, setRatesOpen] = useState(false);

  useEffect(() => {
    if (res.data) {
      setEdit(JSON.parse(JSON.stringify(res.data)));
      setReady(true);
    }
  }, [res.data]);

  const { status, error } = useAutoSave(
    edit,
    async (p) => {
      await api.postSaveConfig(p);
      // 主货币变更：通知父组件刷新全局货币代码与其它页面数据
      const oldCur = (res.data as Record<string, unknown> | null)?.currency_symbol;
      const newCur = p.currency_symbol;
      if (newCur !== oldCur && onCurrencyChanged) {
        onCurrencyChanged();
      }
    },
    { enabled: ready },
  );

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

  const syncRates = async () => {
    setSyncing(true);
    setSyncMsg("正在同步…");
    try {
      const r = await api.postSyncRates();
      setSyncMsg(
        `已同步 ${r.count} 种货币汇率（${r.exchange_rates_updated_at || "?"}）`,
      );
      // 刷新本地 config 副本（exchange_rates 已在后端持久化）
      const cfg = await api.getConfig();
      setEdit(JSON.parse(JSON.stringify(cfg)));
    } catch (e) {
      setSyncMsg(`同步失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  };

  const exchangeRates = (edit.exchange_rates || {}) as Record<string, number>;
  const updatedAt = String(edit.exchange_rates_updated_at || "");
  const sortedRates = Object.entries(exchangeRates)
    .filter(([k]) => k !== "USD")
    .sort((a, b) => a[0].localeCompare(b[0]));

  return (
    <div className="settings-view">
      <div className="settings-hint">
        此处为插件的全部详细配置，修改后自动保存、即时热生效（无需重载）。预算阈值与模型单价请在「预算」「定价」标签页调整。
      </div>

      {SECTIONS.map((sec) => (
        <Panel key={sec.key} className={sec.key === "advanced" ? "panel-advanced" : undefined}>
          <h2>
            {sec.title}
            {sec.key === "advanced" && <span className="badge-advanced">高级</span>}
          </h2>
          {sec.desc && <p className="section-desc">{sec.desc}</p>}
          <div className="set-fields">
            {sec.fields.map((f) => {
              const v = valOf(edit, sec.key, f.k);
              if (f.type === "bool") {
                return (
                  <div className="set-field" key={f.k}>
                    <div className="set-field-text">
                      <div className="set-field-label">{f.label}</div>
                      {f.help && <div className="set-field-help">{f.help}</div>}
                    </div>
                    <label className="switch set-field-control">
                      <input
                        type="checkbox"
                        checked={!!v}
                        onChange={(e) => setField(sec.key, f.k, "bool", e.target.checked)}
                      />
                      <span className="slider" />
                    </label>
                  </div>
                );
              }
              if (f.type === "csv") {
                return (
                  <div className="set-field" key={f.k}>
                    <div className="set-field-text">
                      <div className="set-field-label">{f.label}</div>
                      {f.help && <div className="set-field-help">{f.help}</div>}
                      <input
                        type="text"
                        className="budget-input set-csv-input"
                        defaultValue={Array.isArray(v) ? v.join(", ") : String(v || "")}
                        onBlur={(e) => setField(sec.key, f.k, "csv", e.target.value)}
                      />
                    </div>
                  </div>
                );
              }
              if (f.type === "select") {
                return (
                  <div className="set-field" key={f.k}>
                    <div className="set-field-text">
                      <div className="set-field-label">{f.label}</div>
                      {f.help && <div className="set-field-help">{f.help}</div>}
                    </div>
                    <select
                      className="budget-input set-field-control"
                      value={String(v || "")}
                      onChange={(e) => setField(sec.key, f.k, "select", e.target.value)}
                      style={{ width: f.width ?? 140 }}
                    >
                      {(f.options || []).map((opt) => (
                        <option key={opt} value={opt}>
                          {opt} ({currencyToSymbol(opt)})
                        </option>
                      ))}
                    </select>
                  </div>
                );
              }
              return (
                <div className="set-field" key={f.k}>
                  <div className="set-field-text">
                    <div className="set-field-label">{f.label}</div>
                    {f.help && <div className="set-field-help">{f.help}</div>}
                  </div>
                  <input
                    type={f.type === "int" ? "number" : "text"}
                    className="budget-input set-field-control"
                    value={v === "" ? "" : String(v)}
                    onChange={(e) => setField(sec.key, f.k, f.type, e.target.value)}
                    style={{ width: f.width ?? 140 }}
                  />
                </div>
              );
            })}
          </div>
        </Panel>
      ))}

      <Panel>
        <h2>手动操作</h2>
        <p className="section-desc">立即执行一次清理或推送，无需等待定时任务。</p>
        <div className="row">
          <Button onClick={cleanup}>清理过期数据</Button>
          <Button onClick={report}>推送日报</Button>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          {actionResult}
        </div>
      </Panel>

      <Panel>
        <h2>汇率同步</h2>
        <p className="section-desc">
          点击「立即同步」从免费 API（open.er-api.com）刷新最新汇率。同步后所有费用将按新汇率换算到主货币。无网时使用内置静态汇率兜底。
        </p>
        <div className="row">
          <Button onClick={syncRates} disabled={syncing}>
            {syncing ? "同步中…" : "立即同步汇率"}
          </Button>
          {updatedAt && (
            <span className="muted" style={{ alignSelf: "center" }}>
              上次同步：{updatedAt}
            </span>
          )}
        </div>
        {syncMsg && (
          <div className="muted" style={{ marginTop: 8 }}>
            {syncMsg}
          </div>
        )}
        {sortedRates.length > 0 && (
          <div className="rate-disclosure">
            <button
              type="button"
              className="rate-toggle"
              onClick={() => setRatesOpen((o) => !o)}
              aria-expanded={ratesOpen}
            >
              <span className="rate-toggle-caret">{ratesOpen ? "▾" : "▸"}</span>
              查看 {sortedRates.length} 个汇率
            </button>
            {ratesOpen && (
              <div className="rate-grid" style={{ marginTop: 10 }}>
                {sortedRates.map(([code, rate]) => (
                  <div key={code} className="rate-item">
                    <span className="rate-code">{code}</span>
                    <span className="rate-value">{rate.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Panel>

      <SaveToast status={status} error={error} />
    </div>
  );
}
