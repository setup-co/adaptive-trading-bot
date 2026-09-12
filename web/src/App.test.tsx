import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import { dashboardApi } from "./api/client";
import { snapshotFixture, tradesPageFixture } from "./test/fixtures";

describe("application shell", () => {
  it("exposes observation navigation and no trading-control action", async () => {
    vi.spyOn(dashboardApi, "snapshot").mockResolvedValue(snapshotFixture);
    vi.spyOn(dashboardApi, "trades").mockResolvedValue(tradesPageFixture);
    window.location.hash = "#overview";
    render(<App />);

    expect(await screen.findByText("Market overview")).toBeInTheDocument();
    expect(screen.getByText("Phase 12").parentElement).toHaveAttribute("aria-disabled", "true");
    const forbidden = /^(Buy|Sell|Close|Reduce|Approve|Resume Trading|Enable Live)$/i;
    for (const button of screen.queryAllByRole("button")) {
      expect(button).not.toHaveTextContent(forbidden);
    }
    fireEvent.click(screen.getByRole("link", { name: "Trades" }));
    expect(await screen.findByText("Completed trades")).toBeInTheDocument();
  });
});
