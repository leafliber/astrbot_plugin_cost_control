// 加载 / 错误 / 空态占位
export function Loading({ message = "加载中…" }: { message?: string }) {
  return <div className="loading">{message}</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error">{message}</div>;
}

export function Empty({ text = "暂无数据" }: { text?: string }) {
  return <div className="empty">{text}</div>;
}
