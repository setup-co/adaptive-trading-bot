import { render, screen } from "@testing-library/react";
import { useCallback } from "react";
import { describe, expect, it } from "vitest";

import { useReadOnlyQuery } from "./useReadOnlyQuery";

type Pending = { signal: AbortSignal; resolve: (value: string) => void };

function Probe({ value, pending }: { value: string; pending: Pending[] }) {
  const loader = useCallback(
    (signal: AbortSignal) =>
      new Promise<string>((resolve) => {
        pending.push({ signal, resolve });
      }),
    [pending],
  );
  const state = useReadOnlyQuery(loader, [value]);
  return <div>{state.data ?? state.status}</div>;
}

describe("useReadOnlyQuery", () => {
  it("aborts and ignores an older request when filters change", async () => {
    const pending: Pending[] = [];
    const view = render(<Probe value="first" pending={pending} />);
    view.rerender(<Probe value="second" pending={pending} />);

    expect(pending[0].signal.aborted).toBe(true);
    pending[1].resolve("new result");
    expect(await screen.findByText("new result")).toBeInTheDocument();
    pending[0].resolve("stale result");
    expect(screen.queryByText("stale result")).not.toBeInTheDocument();
  });
});
