import type { ReactNode } from "react";

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary";
  disabled?: boolean;
  title?: string;
}) {
  const cls = variant === "primary" ? "btn primary" : "btn";
  return (
    <button className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}
