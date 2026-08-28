import { useState } from "react";
import { api } from "../../lib/api";
import type { ExprValidateResult } from "../../lib/types";
import { Button } from "../Button";

// New API 兼容表达式编辑器（F3 tiered_expr）：
// 多行输入 + 变量说明 + 后端验证 + 示例试算预览；来自 New API 候选时只读，可解锁。

const VAR_LEGEND: { name: string; desc: string }[] = [
  { name: "p", desc: "非缓存输入 token" },
  { name: "c", desc: "输出 token" },
  { name: "len", desc: "总输入长度（含缓存读写）" },
  { name: "cr", desc: "缓存读 token" },
  { name: "cc", desc: "缓存写 token（5min）" },
  { name: "cc1h", desc: "缓存写 token（1h）" },
  { name: 'tier("名", 值)', desc: "记录命中阶梯并返回该值" },
  { name: 'param("service_tier")', desc: "读请求参数" },
];

export function TieredExprEditor({
  expr,
  lockedSource,
  onChange,
  onUnlock,
}: {
  expr: string;
  lockedSource: string; // 非空 = 来自该 New API 源，只读
  onChange: (expr: string) => void;
  onUnlock: () => void;
}) {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<ExprValidateResult | null>(null);
  const locked = !!lockedSource;

  const validate = async () => {
    setChecking(true);
    try {
      setResult(await api.postPricingExprValidate(expr));
    } catch (e) {
      setResult({
        valid: false,
        error: e instanceof Error ? e.message : String(e),
        samples: [],
      });
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="expr-editor">
      {locked && (
        <div className="expr-locked-bar">
          <span className="pricing-badge pricing-badge--blue">
            来自 {lockedSource}，只读
          </span>
          <button type="button" className="btn" onClick={onUnlock}>
            解锁编辑
          </button>
        </div>
      )}
      <textarea
        className="budget-input expr-input"
        rows={4}
        value={expr}
        readOnly={locked}
        placeholder={
          'p <= 200000 ? tier("standard", p * 1.5 + c * 7.5) : tier("long_context", p * 3.0 + c * 11.25)'
        }
        onChange={(e) => {
          onChange(e.target.value);
          setResult(null); // 内容变化后旧验证结果失效
        }}
      />
      <div className="muted small expr-legend">
        系数单位 $ / 百万 token；可用变量：
        {VAR_LEGEND.map((v) => (
          <span key={v.name} className="expr-var" title={v.desc}>
            {v.name}
          </span>
        ))}
      </div>
      {!locked && (
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <Button onClick={validate} disabled={checking || !expr.trim()}>
            {checking ? "验证中…" : "验证并试算"}
          </Button>
          {result &&
            (result.valid ? (
              <span className="pricing-badge pricing-badge--blue">✓ 表达式有效</span>
            ) : (
              <span className="pricing-badge pricing-badge--red">
                ✗ {result.error || "无效"}
              </span>
            ))}
        </div>
      )}
      {result?.valid && (result.samples?.length ?? 0) > 0 && (
        <div className="muted small expr-samples">
          试算：
          {result.samples!.map((s) => (
            <span key={`${s.p}/${s.c}`} className="expr-sample">
              {s.p.toLocaleString()} 输入 / {s.c.toLocaleString()} 输出 ≈ $
              {s.usd.toFixed(4)}
              {s.tier ? `（阶梯 ${s.tier}）` : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
