import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BadgeDollarSign, CandlestickChart, ChartNoAxesColumn, DoorOpen, LineChart, Scale } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api/client";
import type { PnlDashboard as PnlData } from "../api/types";
import { DASH, exitReason, money, pct, price, qty, relative, signedMoney } from "../lib/format";
import type { Theme } from "../lib/theme";
import { PnlChart } from "./charts";
import { Card, CardHeader, EmptyState, ErrorState, Pnl, Segmented, Skeleton } from "./ui";

type Range = "7" | "30" | "90" | "0";
const RANGE_LABEL: Record<Range, string> = { "7": "son 7 gün", "30": "son 30 gün", "90": "son 90 gün", "0": "tüm zamanlar" };
const MAX_BARS = 10;

function MiniStat({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-line/70 bg-bg/40 px-3 py-2.5">
      <div className="label truncate">{label}</div>
      <div className="num mt-1 truncate text-[16px] font-semibold leading-tight">{children}</div>
      {hint && <div className="mt-0.5 truncate text-[11.5px] text-muted">{hint}</div>}
    </div>
  );
}

function hours(h: number | null | undefined): string {
  if (h == null) return DASH;
  if (h < 48) return `${h.toLocaleString("tr-TR", { maximumFractionDigits: 1 })} sa`;
  return `${(h / 24).toLocaleString("tr-TR", { maximumFractionDigits: 1 })} gün`;
}

/** Sembol basina toplam K/Z: sifir cizgisinden saga (kar) / sola (zarar) cubuk + isaretli deger.
 *  Yon konumla kodlanir; renk yalnizca destekleyici (kirmizi/yesil CVD'de ayrismaz). */
export function SymbolBars({ rows }: { rows: PnlData["by_symbol"] }) {
  const sorted = [...rows].sort((a, b) => Math.abs(b.total) - Math.abs(a.total));
  const shown = sorted.slice(0, MAX_BARS).sort((a, b) => b.total - a.total);
  const rest = sorted.slice(MAX_BARS);
  const max = Math.max(1e-9, ...shown.map((r) => Math.abs(r.total)));
  return (
    <div className="space-y-[2px] px-4 py-3" role="table" aria-label="Sembol bazında kâr/zarar">
      {shown.map((r) => {
        const w = (Math.abs(r.total) / max) * 100;
        const up = r.total >= 0;
        const tip =
          `${r.symbol}: toplam ${signedMoney(r.total)} USDT\n` +
          `Gerçekleşen ${signedMoney(r.realized)} (${r.closed} işlem, ${r.wins} kazanç)\n` +
          `Gerçekleşmemiş ${signedMoney(r.unrealized)} (${r.open} açık)`;
        return (
          <div key={r.symbol} role="row" title={tip} className="group grid grid-cols-[76px_1fr_86px] items-center gap-2 rounded-md px-1 py-1 hover:bg-raised/60">
            <span role="cell" className="truncate text-[12px] font-medium">
              {r.symbol.replace("/USDT", "")}
              {r.open > 0 && <span className="ml-1 text-[10.5px] text-accent" title="açık pozisyon var">●</span>}
            </span>
            <div role="cell" className="relative grid h-4 grid-cols-2">
              <div className="flex justify-end border-r border-line">
                {!up && <div className="h-full rounded-l bg-loss/75" style={{ width: `${w}%`, minWidth: 2 }} />}
              </div>
              <div className="flex justify-start">{up && <div className="h-full rounded-r bg-gain/75" style={{ width: `${w}%`, minWidth: 2 }} />}</div>
            </div>
            <span role="cell" className="num text-right text-[12px]">
              {signedMoney(r.total)}
            </span>
          </div>
        );
      })}
      {rest.length > 0 && (
        <div className="px-1 pt-1 text-[11.5px] text-muted">
          + {rest.length} sembol daha (toplam {signedMoney(rest.reduce((a, r) => a + r.total, 0))})
        </div>
      )}
      <div className="grid grid-cols-[76px_1fr_86px] gap-2 px-1 pt-1 text-[10.5px] text-muted">
        <span />
        <div className="grid grid-cols-2">
          <span className="text-right pr-1.5">← zarar</span>
          <span className="pl-1.5">kâr →</span>
        </div>
        <span className="text-right">USDT</span>
      </div>
    </div>
  );
}

export function PnlDashboard({ theme, accounts }: { theme: Theme; accounts: string[] }) {
  const [range, setRange] = useState<Range>("30");
  const [account, setAccount] = useState("");
  const q = useQuery({
    queryKey: ["pnl", range, account],
    queryFn: () => api.get<PnlData>(`/pnl${qs({ days: range, account: account || undefined })}`),
  });
  const d = q.data;
  const s = d?.summary;
  const accountOptions = Array.from(new Set([...accounts, ...(d?.accounts ?? [])])).sort();

  return (
    <Card className="mt-4">
      <CardHeader
        title="Kâr / Zarar"
        icon={Scale}
        subtitle={`Kapanan işlemler (${RANGE_LABEL[range]}) + açık pozisyonların anlık durumu · komisyonlar düşülmüş`}
        actions={
          <>
            {accountOptions.length > 1 && (
              <select className="input h-8 w-auto py-0 text-[12px]" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Hesap">
                <option value="">Tüm hesaplar</option>
                {accountOptions.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            )}
            <Segmented<Range>
              value={range}
              onChange={setRange}
              options={[
                { value: "7", label: "7G" },
                { value: "30", label: "30G" },
                { value: "90", label: "90G" },
                { value: "0", label: "Tümü" },
              ]}
            />
          </>
        }
      />
      {q.error && <ErrorState error={q.error} />}
      {!d && !q.error && (
        <div className="grid grid-cols-2 gap-3 p-4 lg:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-[72px] rounded-lg" />
          ))}
        </div>
      )}
      {d && s && (
        <>
          <div className="grid grid-cols-2 gap-3 p-4 lg:grid-cols-3 2xl:grid-cols-6">
            <MiniStat label="Toplam K/Z" hint="gerçekleşen + gerçekleşmemiş">
              <Pnl value={s.total} kind="money" />
            </MiniStat>
            <MiniStat label="Gerçekleşen" hint={`${s.closed} kapanan işlem · ${money(s.fees)} komisyon`}>
              <Pnl value={s.realized} kind="money" />
            </MiniStat>
            <MiniStat
              label="Gerçekleşmemiş"
              hint={
                s.unpriced_positions > 0 ? (
                  <span className="text-warn">{s.unpriced_positions} pozisyonun fiyatı alınamadı</span>
                ) : (
                  `${s.open} açık pozisyon`
                )
              }
            >
              <Pnl value={s.unrealized} kind="money" />
            </MiniStat>
            <MiniStat label="Kazanma oranı" hint={`${s.wins} kazanç · ${s.losses} kayıp`}>
              {s.win_rate == null ? DASH : pct(s.win_rate, 0, false)}
            </MiniStat>
            <MiniStat label="Kâr faktörü" hint="brüt kazanç / brüt kayıp">
              {s.profit_factor == null ? (s.wins > 0 ? "∞" : DASH) : money(s.profit_factor)}
            </MiniStat>
            <MiniStat
              label="Ort. kazanç / kayıp"
              hint={`işlem başına ${s.expectancy == null ? DASH : signedMoney(s.expectancy)} · ort. ${hours(s.avg_hours_held)}`}
            >
              <span className="text-[14px]">
                {s.avg_win == null ? DASH : signedMoney(s.avg_win)} <span className="text-muted">/</span>{" "}
                {s.avg_loss == null ? DASH : signedMoney(s.avg_loss)}
              </span>
            </MiniStat>
          </div>

          {s.closed === 0 && s.open === 0 ? (
            <EmptyState icon={BadgeDollarSign} title="Bu dönemde işlem yok">
              Paper runner işlem açıp kapattıkça kâr/zarar burada birikir. Dönemi "Tümü" yaparak eski işlemlere bakabilirsiniz.
            </EmptyState>
          ) : (
            <>
              <div className="grid grid-cols-1 border-t border-line/60 xl:grid-cols-3">
                <div className="min-w-0 border-line/60 xl:col-span-2 xl:border-r">
                  <div className="flex items-center justify-between px-4 pt-3 text-[12px]">
                    <span className="flex items-center gap-1.5 font-medium">
                      <LineChart size={13} className="text-muted" /> Kümülatif gerçekleşen K/Z
                    </span>
                    {(s.best || s.worst) && (
                      <span className="hidden gap-3 text-muted sm:flex">
                        {s.best && (
                          <span>
                            En iyi: {s.best.symbol} <Pnl value={s.best.pnl} kind="money" />
                          </span>
                        )}
                        {s.worst && (
                          <span>
                            En kötü: {s.worst.symbol} <Pnl value={s.worst.pnl} kind="money" />
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                  <div className="p-3">
                    {d.cumulative.length > 1 ? (
                      <PnlChart theme={theme} points={d.cumulative} />
                    ) : (
                      <EmptyState icon={LineChart} title="Bu dönemde kapanan işlem yok">
                        Grafik, işlemler kapandıkça oluşur.
                      </EmptyState>
                    )}
                  </div>
                </div>
                <div className="min-w-0 border-t border-line/60 xl:border-t-0">
                  <div className="flex items-center gap-1.5 px-4 pt-3 text-[12px] font-medium">
                    <ChartNoAxesColumn size={13} className="text-muted" /> Sembol bazında (gerçekleşen + açık)
                  </div>
                  {d.by_symbol.length > 0 ? <SymbolBars rows={d.by_symbol} /> : <EmptyState icon={ChartNoAxesColumn} title="Veri yok" />}
                  {d.by_reason.length > 0 && (
                    <div className="border-t border-line/60 px-4 py-3">
                      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium">
                        <DoorOpen size={13} className="text-muted" /> Çıkış nedenleri
                      </div>
                      <ul className="space-y-1 text-[12px]">
                        {d.by_reason.map((r) => (
                          <li key={r.reason} className="flex items-center justify-between gap-2">
                            <span className="truncate text-muted">
                              {exitReason(r.reason)} <span className="num">· {r.count}</span>
                            </span>
                            <Pnl value={r.pnl} kind="money" />
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>

              <div className="border-t border-line/60">
                <div className="flex items-center justify-between px-4 pt-3 text-[12px]">
                  <span className="flex items-center gap-1.5 font-medium">
                    <CandlestickChart size={13} className="text-muted" /> Açık işlemler — anlık K/Z
                  </span>
                  <Link to="/positions" className="text-accent">
                    Pozisyonlar →
                  </Link>
                </div>
                {d.open_positions.length === 0 ? (
                  <div className="px-4 py-4 text-[12.5px] text-muted">Açık işlem yok.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="table-base">
                      <thead>
                        <tr>
                          <th>Sembol</th>
                          <th>Hesap</th>
                          <th className="text-right">Giriş</th>
                          <th className="text-right">Anlık</th>
                          <th className="text-right">Adet</th>
                          <th className="text-right">K/Z</th>
                          <th className="text-right">Stop'a</th>
                          <th>Süre</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.open_positions.map((p) => (
                          <tr key={p.id}>
                            <td>
                              <Link to={`/trades/${p.id}/chart`} className="font-medium hover:text-accent">
                                {p.symbol}
                              </Link>
                            </td>
                            <td className="num text-muted">{p.account}</td>
                            <td className="num text-right">{price(p.entry_price)}</td>
                            <td className="num text-right">{price(p.price)}</td>
                            <td className="num text-right text-muted">{qty(p.qty)}</td>
                            <td className="text-right">
                              <div className="flex flex-col items-end">
                                <Pnl value={p.unrealized_pnl} kind="money" />
                                <span className="text-[11px]">
                                  <Pnl value={p.unrealized_pct} />
                                </span>
                              </div>
                            </td>
                            <td className={clsx("num text-right", p.stop_distance_pct != null && p.stop_distance_pct > -0.02 && "text-warn")}>
                              {p.stop_distance_pct == null ? DASH : pct(p.stop_distance_pct, 1)}
                            </td>
                            <td className="text-muted">{relative(p.entry_time).replace(" önce", "")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </>
      )}
    </Card>
  );
}
