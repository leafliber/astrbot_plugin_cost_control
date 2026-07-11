import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { AiDiagResult, AiDiagRisk } from "../lib/types";
import { Panel } from "./Panel";

type DiagState = "idle" | "loading" | "done" | "error";

const LEVEL_LABEL: Record<string, string> = {
  high: "高危",
  medium: "中危",
  low: "低危",
  info: "提示",
};

function scoreColor(score: number): string {
  if (score >= 90) return "var(--ok)";
  if (score >= 75) return "var(--accent)";
  if (score >= 60) return "var(--warn)";
  return "var(--bad)";
}

function scoreLabel(score: number): string {
  if (score >= 90) return "优秀";
  if (score >= 75) return "良好";
  if (score >= 60) return "需关注";
  return "需修复";
}

function formatAge(seconds: number): string {
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return `${Math.floor(seconds / 3600)} 小时前`;
}

export function AiDiag() {
  const [state, setState] = useState<DiagState>("idle");
  const [result, setResult] = useState<AiDiagResult | null>(null);
  const [providerName, setProviderName] = useState<string>("");
  const [providerAvailable, setProviderAvailable] = useState(true);
  // stale cached result (age > 2h): show "view last result" button
  const [staleResult, setStaleResult] = useState<AiDiagResult | null>(null);
  const [staleAge, setStaleAge] = useState<string>("");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phaseTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // loading 阶段性提示词
  const PHASES = [
    "正在收集成本数据…",
    "正在分析缓存与归因…",
    "正在调用 LLM 努力计算…",
    "LLM 正在非常努力地计算…",
    "仍在拼尽全力分析中，请稍候…",
  ];
  const [phaseIdx, setPhaseIdx] = useState(0);

  // On mount: fetch provider info + last cached diagnosis
  useEffect(() => {
    api
      .getAiProvider()
      .then((info) => {
        setProviderName(info.provider_name || "");
        setProviderAvailable(info.available);
      })
      .catch(() => {
        setProviderAvailable(false);
      });

    api
      .getAiDiagLast()
      .then((cached) => {
        if (cached.result?.conclusion) {
          if (cached.stale) {
            // > 2h: stay idle, but offer "view last result"
            setStaleResult(cached.result);
            setStaleAge(
              cached.age_seconds != null
                ? formatAge(cached.age_seconds)
                : "",
            );
          } else {
            // < 2h: show result directly
            setResult(cached.result);
            setState("done");
          }
        }
      })
      .catch(() => {
        // no cached result — stay idle
      });

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (phaseTimerRef.current) clearInterval(phaseTimerRef.current);
    };
  }, []);

  const runDiag = useCallback(async () => {
    setState("loading");
    setResult(null);
    setStaleResult(null);
    setPhaseIdx(0);
    // 每 8 秒切换到下一阶段提示词
    if (phaseTimerRef.current) clearInterval(phaseTimerRef.current);
    phaseTimerRef.current = setInterval(() => {
      setPhaseIdx((i) => Math.min(i + 1, PHASES.length - 1));
    }, 8000);
    try {
      const res = await api.postAiDiag();
      if (phaseTimerRef.current) clearInterval(phaseTimerRef.current);
      if (res.error) {
        setState("error");
        setResult(res);
      } else {
        setState("done");
        setResult(res);
      }
    } catch (e) {
      if (phaseTimerRef.current) clearInterval(phaseTimerRef.current);
      setState("error");
      setResult({
        error: e instanceof ApiError ? e.message : String(e),
      });
    }
  }, [PHASES.length]);

  const showStaleResult = useCallback(() => {
    if (staleResult) {
      setResult(staleResult);
      setState("done");
    }
  }, [staleResult]);

  const conclusion = result?.conclusion;
  const risks = conclusion?.risks || [];
  const highlights = conclusion?.highlights || [];
  const score = conclusion?.overall_score;
  const isStaleView = staleResult && result === staleResult;

  return (
    <Panel
      title={
        <div className="aidiag-head">
          <span>AI 成本诊断</span>
          {providerName && state === "idle" && (
            <span className="aidiag-provider">{providerName}</span>
          )}
        </div>
      }
      className="aidiag-panel"
    >
      {state === "idle" && (
        <div className="aidiag-idle">
          <p className="aidiag-desc">
            一键收集成本/缓存/归因/预算/定价5个维度数据，交由 LLM
            综合分析，输出成本健康评分与优化建议。
          </p>
          <button
            className="btn primary aidiag-btn"
            onClick={runDiag}
            disabled={!providerAvailable}
            title={
              !providerAvailable
                ? "未找到可用的 LLM Provider"
                : "点击开始 AI 诊断"
            }
          >
            {providerAvailable ? "一键 AI 诊断" : "未配置 LLM Provider"}
          </button>
          {staleResult && (
            <button
              className="btn aidiag-btn-last"
              onClick={showStaleResult}
              title={`上次诊断于 ${staleAge}`}
            >
              查看上次结果（{staleAge}）
            </button>
          )}
        </div>
      )}

      {state === "loading" && (
        <div className="aidiag-loading">
          <div className="aidiag-spinner" />
          <div className="aidiag-loading-text">
            <div className="aidiag-loading-phase">{PHASES[phaseIdx]}</div>
            <div className="aidiag-loading-hint">
              刷新页面或关闭不会影响诊断结果，完成后可在此查看
            </div>
          </div>
        </div>
      )}

      {state === "error" && (
        <div className="aidiag-error">
          <span className="aidiag-error-icon">!</span>
          <span>{result?.error || "诊断失败，请重试"}</span>
          <button className="btn aidiag-retry" onClick={runDiag}>
            重试
          </button>
        </div>
      )}

      {state === "done" && conclusion && (
        <div className="aidiag-result">
          {isStaleView && (
            <div className="aidiag-stale-banner">
              以下为 {staleAge} 的诊断结果，数据可能已过时
            </div>
          )}
          <div className="aidiag-score-row">
            <div
              className="aidiag-score"
              style={score != null ? { color: scoreColor(score) } : undefined}
            >
              {score != null ? score : "—"}
            </div>
            <div className="aidiag-score-meta">
              <span
                className="aidiag-score-label"
                style={score != null ? { color: scoreColor(score) } : undefined}
              >
                {score != null ? scoreLabel(score) : "无评分"}
              </span>
              <span className="aidiag-overall">{conclusion.overall}</span>
            </div>
          </div>

          {risks.length > 0 && (
            <table className="aidiag-table">
              <thead>
                <tr>
                  <th>等级</th>
                  <th>问题</th>
                  <th>建议</th>
                </tr>
              </thead>
              <tbody>
                {risks.slice(0, 4).map((r: AiDiagRisk, i: number) => (
                  <tr key={i}>
                    <td>
                      <span className={`tag sev-${r.level}`}>
                        {LEVEL_LABEL[r.level] || r.level}
                      </span>
                    </td>
                    <td>{r.issue}</td>
                    <td className="aidiag-advice">{r.advice}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {highlights.length > 0 && risks.length === 0 && (
            <div className="aidiag-highlights">
              {highlights.map((h, i) => (
                <div key={i} className="aidiag-highlight">
                  <span className="aidiag-highlight-dot" />
                  {h}
                </div>
              ))}
            </div>
          )}

          {conclusion.summary && (
            <div className="aidiag-summary">{conclusion.summary}</div>
          )}

          <div className="aidiag-footer">
            <span className="aidiag-provider-small">
              {result?.provider_name || ""}
            </span>
            <button className="btn aidiag-retry" onClick={runDiag}>
              重新诊断
            </button>
          </div>
        </div>
      )}

      {state === "done" && !conclusion && (
        <div className="aidiag-error">
          <span>LLM 返回内容无法解析，请重试</span>
          <button className="btn aidiag-retry" onClick={runDiag}>
            重试
          </button>
        </div>
      )}
    </Panel>
  );
}
