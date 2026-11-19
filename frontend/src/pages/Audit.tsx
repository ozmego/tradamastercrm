import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleX, FileClock, ScrollText, UserCog } from "lucide-react";
import { useState } from "react";

import { api, qs } from "../api/client";
import type { AdminActionRow, Paged } from "../api/types";
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Pagination, Segmented, Spinner } from "../components/ui";
import { actionLabel, dateTime } from "../lib/format";

type Tab = "admin" | "bot";

export function AuditPage() {
  const [tab, setTab] = useState<Tab>("admin");
  return (
    <>
      <PageHeader
        title="Denetim kaydı"
        icon={ScrollText}
        description="Panel işlemleri (kim, ne, ne zaman) ve botun karar kaydı (logs/audit.jsonl)"
        actions={
          <Segmented<Tab>
            value={tab}
            onChange={setTab}
            options={[
              { value: "admin", label: "Panel işlemleri" },
              { value: "bot", label: "Bot kaydı" },
            ]}
          />
        }
      />
      {tab === "admin" ? <AdminActions /> : <BotLog />}
    </>
  );
}

function AdminActions() {
  const [page, setPage] = useState(1);
  const q = useQuery({
    queryKey: ["admin-actions", page],
    queryFn: () => api.get<Paged<AdminActionRow>>(`/audit/admin-actions${qs({ page, size: 50 })}`),
    placeholderData: keepPreviousData,
  });
  return (
    <Card>
      <CardHeader title="Panel işlemleri" icon={UserCog} />
      {q.error && <ErrorState error={q.error} />}
      {!q.data && !q.error && <Spinner />}
      {q.data && q.data.items.length === 0 && <EmptyState title="Kayıt yok" />}
      {q.data && q.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Zaman</th>
                  <th>Kullanıcı</th>
                  <th>İşlem</th>
                  <th>Hesap</th>
                  <th>Ayrıntı</th>
                  <th>Sonuç</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((a) => (
                  <tr key={a.id}>
                    <td className="text-muted">{dateTime(a.ts)}</td>
                    <td className="font-medium">{a.username}</td>
                    <td>
                      <div>{actionLabel(a.action)}</div>
                      <div className="num text-[11px] text-muted">{a.action}</div>
                    </td>
                    <td className="num text-muted">{a.account ?? "—"}</td>
                    <td className="num max-w-[360px] truncate text-[11.5px] text-muted" title={a.detail ? JSON.stringify(a.detail) : ""}>
                      {a.detail ? JSON.stringify(a.detail) : "—"}
                    </td>
                    <td>{a.ok ? <CircleCheck size={15} className="text-gain" /> : <CircleX size={15} className="text-loss" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={50} total={q.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function BotLog() {
  const [eventType, setEventType] = useState("");
  const q = useQuery({
    queryKey: ["audit-log", eventType],
    queryFn: () => api.get<{ items: Record<string, unknown>[]; event_types: string[] }>(`/audit/log${qs({ event_type: eventType, limit: 300 })}`),
    placeholderData: keepPreviousData,
  });
  return (
    <Card>
      <CardHeader
        title="Bot kaydı"
        icon={FileClock}
        subtitle="Son 300 kayıt"
        actions={
          <select className="input w-auto" value={eventType} onChange={(e) => setEventType(e.target.value)} aria-label="Olay tipi">
            <option value="">Bütün olaylar</option>
            {q.data?.event_types.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        }
      />
      {q.error && <ErrorState error={q.error} />}
      {q.data && q.data.items.length === 0 && <EmptyState title="Kayıt yok" />}
      {q.data && q.data.items.length > 0 && (
        <ul className="max-h-[640px] divide-y divide-line/40 overflow-auto">
          {q.data.items.map((r, i) => {
            const { timestamp, event_type, symbol, ...rest } = r as { timestamp?: string; event_type?: string; symbol?: string };
            return (
              <li key={i} className="px-4 py-2.5">
                <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                  <span className="text-muted">{dateTime(timestamp)}</span>
                  <span className="num rounded bg-raised px-1.5 py-0.5 text-[11.5px]">{event_type}</span>
                  {symbol && <span className="font-medium">{symbol}</span>}
                </div>
                <div className="num mt-1 break-all text-[11.5px] text-muted">{JSON.stringify(rest)}</div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
