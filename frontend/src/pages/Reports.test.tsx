import { render } from "@testing-library/react";

import { DailyBars, periodText } from "./Reports";

describe("DailyBars", () => {
  it("draws profit days above and loss days below the zero line", () => {
    const daily = [
      { date: "2026-11-09", pnl: 20, trades: 1, wins: 1, cum: 20 },
      { date: "2026-11-10", pnl: 0, trades: 0, wins: 0, cum: 20 },
      { date: "2026-11-11", pnl: -10, trades: 1, wins: 0, cum: 10 },
    ];
    const { container } = render(<DailyBars daily={daily} />);
    const cols = container.querySelectorAll('[role="img"] > div');
    expect(cols).toHaveLength(3);
    const [up, flat, down] = Array.from(cols);
    // ust yari (index 0) kar, alt yari (index 2) zarar
    expect(up!.children[0]!.querySelector('[class*="bg-gain"]')).not.toBeNull();
    expect(up!.children[2]!.querySelector('[class*="bg-loss"]')).toBeNull();
    expect(down!.children[2]!.querySelector('[class*="bg-loss"]')).not.toBeNull();
    expect(flat!.querySelector('[class*="bg-gain"], [class*="bg-loss"]')).toBeNull();
    expect(up!.getAttribute("title")).toContain("+20,00 USDT");
  });
});

describe("periodText", () => {
  it("shows a single day or an inclusive range (end is exclusive in data)", () => {
    expect(periodText("2026-11-17T00:00:00+00:00", "2026-11-18T00:00:00+00:00")).not.toContain("–");
    const week = periodText("2026-11-09T00:00:00+00:00", "2026-11-16T00:00:00+00:00");
    expect(week).toContain("09");
    expect(week).toContain("15");
  });
});
