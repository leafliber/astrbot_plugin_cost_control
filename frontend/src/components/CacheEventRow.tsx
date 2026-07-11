import { useState } from "react";
import { shortTime } from "../lib/format";
import {
  buildCollapsedView,
  cacheDetailRows,
  cacheDiffText,
  cacheEvtMeta,
  type CollapsedLine,
} from "../lib/cacheMeta";
import type { CacheEvent } from "../lib/types";

// 缓存破坏事件行（点击展开前后 diff 详情）
export function CacheEventRow({ ev }: { ev: CacheEvent }) {
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  const sev = (ev.severity || "low").toLowerCase();
  const m = cacheEvtMeta(ev.type);
  const diff = cacheDiffText(ev.type || "", ev.before, ev.after);
  const detail = cacheDetailRows(ev);
  // 折叠状态仅在 diff 存在时使用
  const isCollapsed = !!detail.diff && collapsed;
  const view: CollapsedLine[] = detail.diff
    ? buildCollapsedView(detail.diff.segments, isCollapsed)
    : [];
  return (
    <>
      <tr className="cache-event-row" onClick={() => setOpen((o) => !o)}>
        <td>
          <span className="cache-evt-title">{m.title}</span>
          <div className="muted small">{ev.type || ""}</div>
        </td>
        <td>
          <span className={`tag sev-${sev}`}>{ev.severity || "-"}</span>
        </td>
        <td className="mono" title={ev.umo || ""}>
          {ev.umo || "-"}
        </td>
        <td>{shortTime(ev.created_at)}</td>
        <td className="mono small">{diff}</td>
        <td className="toggle">{open ? "▼" : "▶"}</td>
      </tr>
      {open && (
        <tr className="cache-event-detail">
          <td colSpan={6}>
            <div className="diff-grid">
              {detail.rows.map((r, i) => (
                <div
                  key={i}
                  className={`diff-row ${r.changed ? "diff-changed" : ""}`}
                >
                  <span className="diff-label">{r.label}</span>
                  <span className="diff-before mono">{r.before}</span>
                  <span className="diff-arrow">→</span>
                  <span className="diff-after mono">{r.after}</span>
                </div>
              ))}
              {detail.firstDiv != null && (
                <div className="diff-row diff-changed">
                  <span className="diff-label">首个分歧</span>
                  <span className="diff-before mono"></span>
                  <span className="diff-arrow"></span>
                  <span className="diff-after mono">#{detail.firstDiv}</span>
                </div>
              )}
            </div>
            {detail.toolsCompare && (
              <div className="tools-compare">
                <div className="tools-compare-col">
                  <div className="tools-compare-label">变更前</div>
                  <pre className="tools-compare-body">
                    {detail.toolsCompare.before || "（无）"}
                  </pre>
                </div>
                <div className="tools-compare-arrow">→</div>
                <div className="tools-compare-col">
                  <div className="tools-compare-label">变更后</div>
                  <pre className="tools-compare-body">
                    {detail.toolsCompare.after || "（无）"}
                  </pre>
                </div>
              </div>
            )}
            {detail.diff && view.length > 0 && (
              <div className="gitdiff">
                <div className="gitdiff-label">{detail.diff.label}</div>
                <pre className="gitdiff-body">
                  {view.map((l, i) => {
                    if ("kind" in l && l.kind === "placeholder") {
                      return (
                        <div
                          key={i}
                          className="diff-line diff-collapsed"
                          onClick={(e) => {
                            e.stopPropagation();
                            setCollapsed(false);
                          }}
                        >
                          <span className="dl-sign">⋯</span>
                          <span className="dl-text">
                            隐藏 {l.count} 行未变更上下文，点击展开
                          </span>
                        </div>
                      );
                    }
                    const line = l as { op: "+" | "-" | " "; text: string };
                    const cls =
                      line.op === "+" ? "add" : line.op === "-" ? "del" : "ctx";
                    return (
                      <div key={i} className={`diff-line dl-${cls}`}>
                        <span className="dl-sign">{line.op}</span>
                        <span className="dl-text">{line.text}</span>
                      </div>
                    );
                  })}
                  {isCollapsed && (
                    <div
                      className="diff-line diff-collapsed diff-expand-all"
                      onClick={(e) => {
                        e.stopPropagation();
                        setCollapsed(false);
                      }}
                    >
                      <span className="dl-sign">▾</span>
                      <span className="dl-text">展开全部</span>
                    </div>
                  )}
                </pre>
              </div>
            )}
            {detail.tip && (
              <div className="diff-tip">
                <strong>处置建议：</strong>
                {detail.tip}
              </div>
            )}
            {detail.detail && <div className="muted small">{detail.detail}</div>}
          </td>
        </tr>
      )}
    </>
  );
}
