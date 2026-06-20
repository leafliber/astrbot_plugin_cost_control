import { StatCard, type StatCardProps } from "./StatCard";

export function StatCardGrid({ items }: { items: StatCardProps[] }) {
  return (
    <div className="cards">
      {items.map((it) => (
        <StatCard key={it.label} {...it} />
      ))}
    </div>
  );
}
