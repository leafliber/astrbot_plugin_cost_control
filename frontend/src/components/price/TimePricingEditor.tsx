import type { PricingSchedule } from "../../lib/types";
import { CURRENCY_OPTIONS } from "../../lib/format";
import {
  type DraftEntry,
  draftToEntry,
  entryToDraft,
} from "../ProviderPricingCard";
import { PerTierEditor } from "./PerTierEditor";
import { TieredExprEditor } from "./TieredExprEditor";

export interface TimePeriodDraft {
  id: string;
  name: string;
  enabled: boolean;
  weekdays: number[];
  allDay: boolean;
  start: string;
  end: string;
  adjustmentType: "multiplier" | "override";
  multiplier: string;
  rule: DraftEntry;
}

export interface TimeScheduleDraft {
  enabled: boolean;
  timezone: string;
  periods: TimePeriodDraft[];
}

const WEEKDAYS = [
  [1, "一"],
  [2, "二"],
  [3, "三"],
  [4, "四"],
  [5, "五"],
  [6, "六"],
  [7, "日"],
] as const;

function emptyRule(defaultCurrency?: string): DraftEntry {
  return entryToDraft(undefined, defaultCurrency);
}

function emptyPeriod(defaultCurrency?: string): TimePeriodDraft {
  return {
    id: `period_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    name: "新时段",
    enabled: true,
    weekdays: [1, 2, 3, 4, 5, 6, 7],
    allDay: false,
    start: "09:00",
    end: "18:00",
    adjustmentType: "multiplier",
    multiplier: "1",
    rule: emptyRule(defaultCurrency),
  };
}

export function emptyTimeSchedule(timezone = "Asia/Shanghai"): TimeScheduleDraft {
  return { enabled: false, timezone, periods: [] };
}

export function scheduleToDraft(
  schedule: PricingSchedule | undefined,
  defaultTimezone: string,
  defaultCurrency?: string,
): TimeScheduleDraft {
  if (!schedule) return emptyTimeSchedule(defaultTimezone);
  return {
    enabled: schedule.enabled !== false,
    timezone: schedule.timezone || defaultTimezone,
    periods: (schedule.periods || []).map((period) => ({
      id: period.id,
      name: period.name || period.id,
      enabled: period.enabled !== false,
      weekdays: period.weekdays?.length ? [...period.weekdays] : [1, 2, 3, 4, 5, 6, 7],
      allDay: !!period.all_day,
      start: period.start || "00:00",
      end: period.end || "00:00",
      adjustmentType: period.adjustment.type,
      multiplier:
        period.adjustment.type === "multiplier"
          ? String(period.adjustment.value)
          : "1",
      rule:
        period.adjustment.type === "override"
          ? entryToDraft(period.adjustment.rule, defaultCurrency)
          : emptyRule(defaultCurrency),
    })),
  };
}

export function scheduleDraftHasData(draft: TimeScheduleDraft): boolean {
  return draft.enabled || draft.periods.length > 0;
}

export function draftToSchedule(draft: TimeScheduleDraft): PricingSchedule {
  const timezone = draft.timezone.trim();
  if (!timezone) throw new Error("分时定价时区不能为空");
  return {
    enabled: draft.enabled,
    timezone,
    periods: draft.periods.map((period) => {
      if (!period.name.trim()) throw new Error("时间段名称不能为空");
      if (!period.weekdays.length) throw new Error(`${period.name} 至少选择一个星期`);
      if (!period.allDay && (!period.start || !period.end || period.start === period.end)) {
        throw new Error(`${period.name} 的起止时间无效`);
      }
      let adjustment: PricingSchedule["periods"][number]["adjustment"];
      if (period.adjustmentType === "multiplier") {
        const value = Number(period.multiplier);
        if (!Number.isFinite(value) || value < 0 || value > 100) {
          throw new Error(`${period.name} 的倍率必须在 0–100 之间（0 表示该时段免费）`);
        }
        adjustment = { type: "multiplier", value };
      } else {
        const rule = draftToEntry(period.rule);
        if (!rule) throw new Error(`${period.name} 的替换定价不能为空`);
        adjustment = { type: "override", rule };
      }
      return {
        id: period.id,
        name: period.name.trim(),
        enabled: period.enabled,
        weekdays: [...period.weekdays].sort(),
        all_day: period.allDay,
        start: period.allDay ? "00:00" : period.start,
        end: period.allDay ? "00:00" : period.end,
        adjustment,
      };
    }),
  };
}

function resetRuleMode(rule: DraftEntry, mode: DraftEntry["mode"]): DraftEntry {
  const next = emptyRule(rule.currency);
  next.mode = mode;
  next.currency = rule.currency;
  return next;
}

function RuleEditor({
  draft,
  onChange,
}: {
  draft: DraftEntry;
  onChange: (draft: DraftEntry) => void;
}) {
  const patch = (value: Partial<DraftEntry>) => onChange({ ...draft, ...value });
  const tokenFields = [
    ["input", "输入"],
    ["input_cached", "缓存命中"],
    ["output", "输出"],
    ["cache_creation", "缓存写入"],
  ] as const;
  return (
    <div className="time-rule-editor">
      <div className="time-rule-head">
        <select
          className="budget-input"
          value={draft.mode}
          onChange={(e) => onChange(resetRuleMode(draft, e.target.value as DraftEntry["mode"]))}
        >
          <option value="per_token">按 Token</option>
          <option value="per_tier">阶梯定价</option>
          <option value="tiered_expr">表达式</option>
          <option value="per_turn">按调用轮次</option>
          <option value="per_request">按请求次数</option>
        </select>
        <select
          className="budget-input"
          value={draft.currency}
          disabled={draft.mode === "tiered_expr"}
          onChange={(e) => patch({ currency: e.target.value })}
        >
          <option value="">USD</option>
          {CURRENCY_OPTIONS.filter((c) => c !== "USD").map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>
      {draft.mode === "tiered_expr" ? (
        <TieredExprEditor
          expr={draft.expr}
          lockedSource=""
          onChange={(expr) => patch({ expr })}
          onUnlock={() => {}}
        />
      ) : draft.mode === "per_turn" || draft.mode === "per_request" ? (
        <label className="pricing-field">
          <span className="muted small">每{draft.mode === "per_turn" ? "轮" : "次请求"}</span>
          <input
            className="budget-input"
            type="number"
            min="0"
            step="any"
            value={draft.price}
            onChange={(e) => patch({ price: e.target.value })}
          />
        </label>
      ) : (
        <>
          <div className="time-rule-token-grid">
            {tokenFields.map(([key, label]) => (
              <label className="pricing-field" key={key}>
                <span className="muted small">{label}{draft.mode === "per_tier" ? "（基础）" : ""}</span>
                <input
                  className="budget-input"
                  type="number"
                  min="0"
                  step="any"
                  value={draft[key]}
                  onChange={(e) => patch({ [key]: e.target.value })}
                />
              </label>
            ))}
          </div>
          {draft.mode === "per_tier" && (
            <PerTierEditor
              contextTiers={draft.contextTiers}
              serviceTiers={draft.serviceTiers}
              onChange={(value) => patch(value)}
            />
          )}
        </>
      )}
    </div>
  );
}

export function TimePricingEditor({
  draft,
  defaultCurrency,
  onChange,
}: {
  draft: TimeScheduleDraft;
  defaultCurrency?: string;
  onChange: (draft: TimeScheduleDraft) => void;
}) {
  const updatePeriod = (index: number, patch: Partial<TimePeriodDraft>) => {
    const periods = draft.periods.map((period, i) =>
      i === index ? { ...period, ...patch } : period,
    );
    onChange({ ...draft, periods });
  };
  return (
    <div className="time-pricing-editor">
      <div className="time-pricing-title-row">
        <label className="time-pricing-toggle">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
          />
          <strong>启用分时定价</strong>
        </label>
        <label className="time-timezone">
          <span className="muted small">IANA 时区</span>
          <input
            className="budget-input"
            value={draft.timezone}
            placeholder="Asia/Shanghai"
            onChange={(e) => onChange({ ...draft, timezone: e.target.value })}
          />
        </label>
      </div>
      <div className="muted small time-pricing-help">
        未覆盖时段沿用基础定价；时间段左闭右开，跨午夜的凌晨部分归属于前一天。
      </div>
      {draft.periods.map((period, index) => (
        <div className="time-period-card" key={period.id}>
          <div className="time-period-head">
            <input
              type="checkbox"
              checked={period.enabled}
              onChange={(e) => updatePeriod(index, { enabled: e.target.checked })}
            />
            <input
              className="budget-input time-period-name"
              value={period.name}
              onChange={(e) => updatePeriod(index, { name: e.target.value })}
            />
            <button
              type="button"
              className="pricing-clear"
              onClick={() => onChange({
                ...draft,
                periods: draft.periods.filter((_, i) => i !== index),
              })}
            >
              删除时段
            </button>
          </div>
          <div className="time-weekdays">
            {WEEKDAYS.map(([day, label]) => (
              <label key={day} className={period.weekdays.includes(day) ? "is-active" : ""}>
                <input
                  type="checkbox"
                  checked={period.weekdays.includes(day)}
                  onChange={(e) => updatePeriod(index, {
                    weekdays: e.target.checked
                      ? [...period.weekdays, day]
                      : period.weekdays.filter((d) => d !== day),
                  })}
                />
                {label}
              </label>
            ))}
          </div>
          <div className="time-period-controls">
            <label>
              <input
                type="checkbox"
                checked={period.allDay}
                onChange={(e) => updatePeriod(index, { allDay: e.target.checked })}
              /> 全日
            </label>
            {!period.allDay && (
              <>
                <input
                  className="budget-input"
                  type="time"
                  value={period.start}
                  onChange={(e) => updatePeriod(index, { start: e.target.value })}
                />
                <span>至</span>
                <input
                  className="budget-input"
                  type="time"
                  value={period.end}
                  onChange={(e) => updatePeriod(index, { end: e.target.value })}
                />
              </>
            )}
            <select
              className="budget-input"
              value={period.adjustmentType}
              onChange={(e) => updatePeriod(index, {
                adjustmentType: e.target.value as TimePeriodDraft["adjustmentType"],
              })}
            >
              <option value="multiplier">基础价倍率</option>
              <option value="override">替换定价规则</option>
            </select>
            {period.adjustmentType === "multiplier" && (
              <label className="time-multiplier">
                ×
                <input
                  className="budget-input"
                  type="number"
                  min="0"
                  max="100"
                  step="any"
                  value={period.multiplier}
                  onChange={(e) => updatePeriod(index, { multiplier: e.target.value })}
                />
              </label>
            )}
          </div>
          {period.adjustmentType === "override" && (
            <RuleEditor
              draft={period.rule}
              onChange={(rule) => updatePeriod(index, { rule })}
            />
          )}
        </div>
      ))}
      <button
        type="button"
        className="pricing-clear time-add-period"
        onClick={() => onChange({
          ...draft,
          enabled: true,
          periods: [...draft.periods, emptyPeriod(defaultCurrency)],
        })}
      >
        ＋ 添加时间段
      </button>
    </div>
  );
}
