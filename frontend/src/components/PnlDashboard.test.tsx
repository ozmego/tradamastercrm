import { render, screen } from "@testing-library/react";

import { SymbolBars } from "./PnlDashboard";

const row = (symbol: string, total: number) => ({ symbol, realized: total, unrealized: 0, total, closed: 1, wins: total > 0 ? 1 : 0, open: 0 });

describe("SymbolBars", () => {
  it("draws gains to the right and losses to the left of zero, with signed labels", () => {
    const { container } = render(<SymbolBars rows={[row("BTC/USDT", 50), row("ETH/USDT", -25)]} />);
    const rows = container.querySelectorAll('[role="row"]');
    expect(rows).toHaveLength(2);
    // ilk satir en buyuk kar (siralama toplam K/Z'ye gore)
    const [btc, eth] = Array.from(rows);
    const halves = (r: Element) => r.querySelectorAll('[role="cell"]')[1]!.children;
    expect(halves(btc!)[1]!.querySelector("[class*=bg-gain]")).not.toBeNull();
    expect(halves(btc!)[0]!.querySelector("[class*=bg-loss]")).toBeNull();
    expect(halves(eth!)[0]!.querySelector("[class*=bg-loss]")).not.toBeNull();
    expect(screen.getByText("+50,00")).toBeInTheDocument();
    expect(screen.getByText("−25,00")).toBeInTheDocument();
    // cubuk genisligi en buyuk mutlak degere gore olceklenir
    expect((halves(eth!)[0]!.firstElementChild as HTMLElement).style.width).toBe("50%");
  });

  it("folds symbols beyond the top 10 into a summary line", () => {
    const rows = Array.from({ length: 12 }, (_, i) => row(`C${i}/USDT`, i + 1));
    render(<SymbolBars rows={rows} />);
    expect(screen.getByText(/2 sembol daha/)).toBeInTheDocument();
  });
});
