import type { ReactNode } from "react";

import type { QueryState } from "../hooks/useReadOnlyQuery";

export function QueryBoundary<T>({
  state,
  empty,
  children,
}: {
  state: QueryState<T>;
  empty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") {
    return <div className="state-card" role="status">Loading authoritative observation…</div>;
  }
  if (state.status === "error") {
    return (
      <div className={`state-card state-${state.error.kind}`} role="alert">
        <strong>{state.error.kind === "invalid-query" ? "Invalid query" : "Observation unavailable"}</strong>
        <p>{state.error.message}</p>
        <code>{state.error.code}</code>
      </div>
    );
  }
  return (
    <>
      {state.status === "refreshing" && (
        <div className="stale-banner" role="status">Refreshing — showing the previous observation</div>
      )}
      {empty?.(state.data) ? (
        <div className="state-card">No authoritative records matched this query.</div>
      ) : (
        children(state.data)
      )}
    </>
  );
}
