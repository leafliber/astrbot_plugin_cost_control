import { useState } from "react";
import { shortTime } from "../lib/format";
import { cacheDetailRows, cacheDiffText, cacheEvtMeta } from "../lib/cacheMeta";
import type { CacheEvent } from "../lib/types";

// 缓存破坏事件行（点击展开前后 diff 详情）
export function CacheEventRow({ ev }: { ev: CacheEvent }) {
  const [open, setOpen] = useState(false);
  const sev = (ev.severity || "low").toLowerCase();
  const m = cacheEvtMeta(ev.type);
  const diff = cacheDiffText(ev.type || "", ev.before, ev.after);
  const detail = cacheDetailRows(ev);
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
