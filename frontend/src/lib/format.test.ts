import { compact, money, pct, price, relative, signedMoney, tone } from "./format";

describe("format", () => {
  it("formats money with tr-TR separators", () => {
    expect(money(12345.678)).toBe("12.345,68");
    expect(money(null)).toBe("—");
    expect(money(Number.NaN)).toBe("—");
  });

  it("formats signed percent with explicit sign", () => {
    expect(pct(0.0123)).toBe("+%1,23");
    expect(pct(-0.05)).toBe("−%5,00");
    expect(pct(0)).toBe("%0,00");
    expect(pct(undefined)).toBe("—");
  });

  it("formats signed money", () => {
    expect(signedMoney(-1234.5)).toBe("−1.234,50");
    expect(signedMoney(10)).toBe("+10,00");
  });

  it("adapts price precision to magnitude", () => {
    expect(price(64250.5)).toBe("64.250,50");
    expect(price(0.123456)).toBe("0,123456");
  });

  it("gives tone without relying on color only", () => {
    expect(tone(0.1)).toBe("gain");
    expect(tone(-0.1)).toBe("loss");
    expect(tone(0)).toBe("flat");
    expect(tone(null)).toBe("flat");
  });

  it("compacts large numbers and relative time", () => {
    expect(compact(1_500_000)).toMatch(/1,5/);
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(relative("2026-01-01T11:30:00Z", now)).toBe("30 dk önce");
    expect(relative("2025-12-30T12:00:00Z", now)).toBe("2 gün önce");
  });
});
