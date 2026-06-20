import type { ReactNode } from "react";

export interface StatCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: ReactNode;
}

export function StatCard({ label, value, sub, delta }: StatCardProps) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub != null && <div className="sub">{sub}</div>}
      {delta != null && <div className="delta-row">{delta}</div>}
    </div>
  );
}
