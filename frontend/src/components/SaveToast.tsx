import type { SaveStatus } from "../hooks/useAutoSave";

// 自动保存提示浮层（非阻塞 toast）。
// 自动保存场景下「每次改动都弹阻塞模态」不可用（一按键就挡住），故用浮层：
// saving/saved/error 时显示，idle 时不渲染。显示时长由 useAutoSave 的 toastMs 控制。
export function SaveToast({
  status,
  error,
}: {
  status: SaveStatus;
  error?: string;
}) {
  if (status === "idle") return null;
  const text =
    status === "saving"
      ? "正在保存…"
      : status === "saved"
        ? "✅ 已保存"
        : `❌ 保存失败：${error || "未知错误"}`;
  return (
    <div
      className={`save-toast save-toast-${status}`}
      role="status"
      aria-live="polite"
    >
      {text}
    </div>
  );
}
