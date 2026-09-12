const critical = new Set(["SAFE_MODE", "DOWN", "BROKEN", "CORRUPT"]);
const blocked = new Set(["REJECTED", "BLOCKED_BY_RISK", "NO_TRADE", "LOSS"]);
const warning = new Set(["DEGRADED", "UNKNOWN", "UNAVAILABLE", "DIRTY"]);

export function Status({ value }: { value: string }) {
  const upper = value.toUpperCase();
  const tone = critical.has(upper)
    ? "critical"
    : blocked.has(upper)
      ? "blocked"
      : warning.has(upper)
        ? "warning"
        : "normal";
  return <span className={`status status-${tone}`}>{value.replaceAll("_", " ")}</span>;
}
