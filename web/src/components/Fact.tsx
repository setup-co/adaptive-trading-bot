import type { ReactNode } from "react";

type Availability = {
  availability: "AVAILABLE" | "UNAVAILABLE";
  value: unknown;
  reason: string | null;
};

export function Fact({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}

export function AvailableFact({ label, fact }: { label: string; fact: Availability }) {
  return fact.availability === "AVAILABLE" ? (
    <Fact label={label} value={formatValue(fact.value)} mono />
  ) : (
    <div className="fact unavailable-fact">
      <dt>{label}</dt>
      <dd>
        <span className="status status-unknown">Unavailable</span>
        <small>{fact.reason}</small>
      </dd>
    </div>
  );
}

function formatValue(value: unknown): string {
  return Array.isArray(value) ? value.join(" · ") : String(value);
}
