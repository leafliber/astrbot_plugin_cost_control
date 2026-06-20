import type { ReactNode } from "react";

export function Panel({
  title,
  children,
  className = "",
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`panel ${className}`.trim()}>
      {title != null && <h2>{title}</h2>}
      {children}
    </div>
  );
}
