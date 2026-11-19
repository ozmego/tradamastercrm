import { useQuery } from "@tanstack/react-query";
import { CloudOff, Database, HeartPulse, Layers } from "lucide-react";

import { api } from "../api/client";
import type { Coverage, SourcesHealth } from "../api/types";
import { Badge, Card, CardHeader, EmptyState, ErrorState, Notice, PageHeader, Spinner, Stat } from "../components/ui";
import { date, dateTime, relative } from "../lib/format";

const SOURCES = [
  { name: "Binance spot OHLCV", use: "Strateji sinyali, fiyat", history: "Yıllarca" },
  { name: "Binance perpetual funding", use: "funding filtresi", history: "2019'dan beri" },
  { name: "Fear & Greed (alternative.me)", use: "fng filtresi", history: "2018'den beri" },
  { name: "Makro takvim (FOMC)", use: "macro filtresi", history: "statik CSV" },
  { name: "Open interest + long/short", use: "izleme (toplayıcı)", history: "Binance ~30 gün -> biriktiriliyor" },
];

export function DataPage() {
  const health = useQuery({ queryKey: ["sources"], queryFn: () => api.get<SourcesHealth>("/data/sources") });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: () => api.get<Coverage[]>("/data/coverage") });

  return (
    <>
      <PageHeader title="Veri kaynakları" icon={Database} description="Kaynak erişilemezse filtre girişi engellemez (fail-open) ve audit'e yazılır" />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Sağlık" icon={HeartPulse} />
          <div className="space-y-3 p-4">
            {health.error && <ErrorState error={health.error} />}
            {health.data && (
              <>
                <Stat label="Son runner tick'i">
                  {health.data.last_tick_at ? `${dateTime(health.data.last_tick_at)} (${relative(health.data.last_tick_at)})` : "—"}
                </Stat>
                {health.data.last_data_problems?.length ? (
                  <Notice>Son tick'teki veri sorunlari: {health.data.last_data_problems.join("; ")}</Notice>
                ) : (
                  <div className="text-[12.5px] text-muted">Son tick'te veri sorunu yok.</div>
                )}
                {health.data.failures.length > 0 ? (
                  <ul className="space-y-2">
                    {health.data.failures.map((f) => (
                      <li key={f.source} className="rounded-lg border border-warn/30 bg-warn/5 p-2.5 text-[12px]">
                        <div className="flex items-center justify-between">
                          <span className="flex items-center gap-1.5 font-medium text-warn">
                            <CloudOff size={13} /> {f.source}
                          </span>
                          <span className="num text-muted">{f.count}x</span>
                        </div>
                        <div className="mt-1 text-muted">
                          {relative(f.last_error_at)} · {f.error ?? "erişilemedi"}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Badge tone="gain">Son kayıtlarda kaynak hatası yok</Badge>
                )}
              </>
            )}
          </div>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Kaynaklar" icon={Layers} />
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Kaynak</th>
                  <th>Kullanım</th>
                  <th>Geçmiş</th>
                </tr>
              </thead>
              <tbody>
                {SOURCES.map((s) => (
                  <tr key={s.name}>
                    <td className="font-medium">{s.name}</td>
                    <td className="text-muted">{s.use}</td>
                    <td className="text-muted">{s.history}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Biriken metrikler (toplayıcı)" icon={Database} />
        {coverage.isLoading && <Spinner />}
        {coverage.data && coverage.data.length === 0 && (
          <EmptyState icon={Database} title="Henüz veri yok">
            Süreçler sayfasından veri toplayıcıyı başlatın.
          </EmptyState>
        )}
        {coverage.data && coverage.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Sembol</th>
                  <th>Metrik</th>
                  <th className="text-right">Satır</th>
                  <th>Başlangıç</th>
                  <th>Son</th>
                </tr>
              </thead>
              <tbody>
                {coverage.data.map((c) => (
                  <tr key={`${c.symbol}-${c.metric}`}>
                    <td className="font-medium">{c.symbol}</td>
                    <td className="num text-muted">{c.metric}</td>
                    <td className="num text-right">{c.rows.toLocaleString("tr-TR")}</td>
                    <td className="text-muted">{date(c.from)}</td>
                    <td className="text-muted">
                      {dateTime(c.to)} <span className="text-[11.5px]">({relative(c.to)})</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
