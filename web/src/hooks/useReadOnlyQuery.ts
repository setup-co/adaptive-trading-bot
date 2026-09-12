import { useEffect, useRef, useState } from "react";

import { DashboardApiError } from "../api/client";

export type QueryState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "refreshing"; data: T; error: null }
  | { status: "error"; data: null; error: DashboardApiError };

export function useReadOnlyQuery<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
): QueryState<T> {
  const [state, setState] = useState<QueryState<T>>({
    status: "loading",
    data: null,
    error: null,
  });
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    setState((previous) =>
      previous.data === null
        ? { status: "loading", data: null, error: null }
        : { status: "refreshing", data: previous.data, error: null },
    );
    void loader(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted && generation.current === current) {
          setState({ status: "ready", data, error: null });
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || generation.current !== current) return;
        const safeError =
          error instanceof DashboardApiError
            ? error
            : new DashboardApiError(
                "network",
                "The observation could not be loaded.",
                "OBSERVATION_FAILURE",
                null,
              );
        setState({ status: "error", data: null, error: safeError });
      });
    return () => controller.abort();
    // Callers provide the exact primitive request dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return state;
}
