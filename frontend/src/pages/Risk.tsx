import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleSlash,
  History,
  OctagonX,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { api, qs } from "../api/client";
import type { Account, Paged, RiskEvent } from "../api/types";
import { BuyPermissionBadge, ModeBadge } from "../components/domain";
import { RiskActions } from "../components/RiskActions";
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Pagination, Spinner } from "../components/ui";
import { dateTime } from "../lib/format";

const EVENT: Record<string, { label: string; icon: LucideIcon; cls: string }> = {
  TRADING_ENABLED: { label: "Alım izni açıldı", icon: CircleCheck, cls: "text-gain bg-gain/10" },
  TRADING_DISABLED: { label: "Alım izni kapatıldı", icon: CircleSlash, cls: "text-muted bg-raised" },
  KILL_SWITCH_TRIP: { label: "Kill-switch tetiklendi", icon: OctagonX, cls: "text-loss bg-loss/10" },
  KILL_SWITCH_RESET: { label: "Kill-switch sıfırlandı", icon: RotateCcw, cls: "text-accent bg-accent/10" },
  FIRST_ORDER_CONFIRMED: { label: "İlk canlı emir onaylandı", icon: ShieldCheck, cls: "text-gold bg-gold/10" },
};

export function RiskPage() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/accounts") });
  const [account, setAccount] = useState("");
  const [page, setPage] = useState(1);
  const events = useQuery({
    queryKey: ["risk-events", account, page],
    queryFn: () => api.get<Paged<RiskEvent>>(`/risk/events${qs({ account, page, size: 30 })}`),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Risk"
        icon={ShieldAlert}
        description="Hesap bazlı alım izni ve kill-switch. Kök hesabi (paper) kapatmak bütün alt hesapları durdurur."
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-1">
          {accounts.error && <ErrorState error={accounts.error} />}
          {accounts.isLoading && <Spinner />}
          {accounts.data?.map((a) => (
            <Card key={a.name}>
              <div className="flex items-center justify-between gap-2 border-b border-line/60 px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="num font-semibold">{a.name}</span>
                  <ModeBadge mode={a.mode} />
                </div>
                <BuyPermissionBadge account={a} />
              </div>
              <div className="space-y-2 p-4">
                {a.flags.blockers.length > 0 && <div className="text-[12px] text-muted">Durduran: {a.flags.blockers.join(", ")}</div>}
                {a.flags.kill_switch && a.flags.kill_switch_reason && (
                  <div className="text-[12px] text-loss">Neden: {a.flags.kill_switch_reason}</div>
                )}
                <RiskActions account={a} compact />
              </div>
            </Card>
          ))}
        </div>

        <Card className="xl:col-span-2">
          <CardHeader
            title="Olay geçmişi"
            icon={History}
            actions={
              <select
                className="input w-auto"
                value={account}
                onChange={(e) => {
                  setAccount(e.target.value);
                  setPage(1);
                }}
                aria-label="Hesap"
              >
                <option value="">Bütün hesaplar</option>
                {accounts.data?.map((a) => (
                  <option key={a.name} value={a.name}>
                    {a.name}
                  </option>
                ))}
              </select>
            }
          />
          {events.data && events.data.items.length === 0 && <EmptyState icon={History} title="Olay yok" />}
          {events.data && events.data.items.length > 0 && (
            <>
              <ol className="relative space-y-0 px-4 py-2">
                {events.data.items.map((e) => {
                  const meta = EVENT[e.event_type] ?? { label: e.event_type, icon: ShieldAlert, cls: "text-muted bg-raised" };
                  const Icon = meta.icon;
                  return (
                    <li key={e.id} className="flex gap-3 border-b border-line/40 py-3 last:border-0">
                      <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${meta.cls}`}>
                        <Icon size={15} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <span className="text-[13px] font-medium">{meta.label}</span>
                          <span className="text-[12px] text-muted">{dateTime(e.timestamp)}</span>
                        </div>
                        <div className="text-[12px] text-muted">
                          <span className="num">{e.account}</span>
                          {e.reason && <> · {e.reason}</>}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
              <Pagination page={page} size={30} total={events.data.total} onPage={setPage} />
            </>
          )}
        </Card>
      </div>
    </>
  );
}
