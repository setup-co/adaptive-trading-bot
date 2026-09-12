import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

function sources(root: string): string[] {
  return readdirSync(root).flatMap((name) => {
    const path = join(root, name);
    if (name === "test" || name.endsWith(".test.ts") || name.endsWith(".test.tsx")) return [];
    return statSync(path).isDirectory() ? sources(path) : /\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("frontend safety architecture", () => {
  it("contains no mutation transport or trading-control implementation", () => {
    const content = sources(join(process.cwd(), "src"))
      .map((path) => readFileSync(path, "utf8"))
      .join("\n");

    expect(content).not.toMatch(/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/);
    expect(content).not.toMatch(/\/api\/.*(?:orders|authorize|settings|live\/enable)/i);
    expect(content).not.toMatch(
      /<button[^>]*>\s*(?:Buy|Sell|Close|Reduce|Approve|Resume Trading|Enable Live)\s*<\/button>/i,
    );
  });

  it("confines Decimal-to-number conversion to the chart renderer", () => {
    const files = sources(join(process.cwd(), "src"));
    for (const path of files.filter((value) => !value.endsWith("CandleChart.tsx"))) {
      expect(readFileSync(path, "utf8"), path).not.toMatch(
        /(?:\b(?:Number|parseFloat)\s*\(|\bDate\.parse\s*\()/,
      );
    }
  });

  it("contains no local PnL, R, risk, safety, or indicator calculator", () => {
    const names = sources(join(process.cwd(), "src"))
      .map((path) => readFileSync(path, "utf8"))
      .join("\n");
    expect(names).not.toMatch(/(?:calculate|compute|derive)(?:Pnl|PnL|Risk|Safety|RMultiple|Ema|Vwap)/);
  });
});
