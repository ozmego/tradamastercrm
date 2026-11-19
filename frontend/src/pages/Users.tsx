import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, UserMinus, UserPlus, UserRoundCheck, Users } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { User } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Card, CardHeader, ErrorState, Field, Notice, PageHeader, Spinner } from "../components/ui";
import { dateTime } from "../lib/format";

export function UsersPage() {
  const qc = useQueryClient();
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pwFor, setPwFor] = useState<User | null>(null);
  const [newPw, setNewPw] = useState("");

  const create = useMutation({
    mutationFn: () => api.post<User>("/users", { username, password }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const patch = useMutation({
    mutationFn: (args: { id: number; body: { is_active?: boolean; password?: string } }) => api.patch<User>(`/users/${args.id}`, args.body),
    onSuccess: () => {
      setPwFor(null);
      setNewPw("");
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <>
      <PageHeader title="Kullanıcılar" icon={Users} description="Rol yok: bütün aktif kullanıcılar aynı yetkidedir" />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Kullanıcı ekle" icon={UserPlus} />
          <form onSubmit={submit} className="space-y-3 p-4">
            <Field label="Kullanıcı adi" hint="3-32 karakter: harf, rakam, _ . -">
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" required />
            </Field>
            <Field label="Parola" hint="En az 12 karakter">
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required />
            </Field>
            {create.error && <Notice tone="loss">{(create.error as Error).message}</Notice>}
            <button className="btn-primary w-full" disabled={create.isPending}>
              <UserPlus size={14} /> Ekle
            </button>
          </form>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader title="Kullanıcılar" icon={Users} />
          {q.error && <ErrorState error={q.error} />}
          {!q.data && !q.error && <Spinner />}
          {patch.error && (
            <div className="p-4 pb-0">
              <Notice tone="loss">{(patch.error as Error).message}</Notice>
            </div>
          )}
          {q.data && (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Kullanıcı</th>
                    <th>Durum</th>
                    <th>Son giriş</th>
                    <th>Oluşturma</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {q.data.map((u) => (
                    <tr key={u.id}>
                      <td className="font-medium">
                        {u.username} {u.username === me.username && <span className="text-[11.5px] text-muted">(siz)</span>}
                      </td>
                      <td>{u.is_active ? <Badge tone="gain">Aktif</Badge> : <Badge>Pasif</Badge>}</td>
                      <td className="text-muted">{dateTime(u.last_login_at)}</td>
                      <td className="text-muted">{dateTime(u.created_at)}</td>
                      <td>
                        <div className="flex justify-end gap-1.5">
                          <button className="btn-ghost px-2 py-1 text-[12px]" onClick={() => setPwFor(u)} title="Parola değiştir">
                            <KeyRound size={13} />
                          </button>
                          {u.is_active ? (
                            <button
                              className="btn-ghost px-2 py-1 text-[12px]"
                              onClick={() => patch.mutate({ id: u.id, body: { is_active: false } })}
                              title="Devre dışı bırak"
                            >
                              <UserMinus size={13} className="text-loss" />
                            </button>
                          ) : (
                            <button
                              className="btn-ghost px-2 py-1 text-[12px]"
                              onClick={() => patch.mutate({ id: u.id, body: { is_active: true } })}
                              title="Etkinleştir"
                            >
                              <UserRoundCheck size={13} className="text-gain" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pwFor && (
            <form
              className="flex flex-wrap items-end gap-2 border-t border-line/60 p-4"
              onSubmit={(e) => {
                e.preventDefault();
                patch.mutate({ id: pwFor.id, body: { password: newPw } });
              }}
            >
              <div className="min-w-[220px] flex-1">
                <Field label={`${pwFor.username} için yeni parola`} hint="Kullanıcının açık oturumları kapatılır">
                  <input className="input" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" autoFocus />
                </Field>
              </div>
              <button className="btn-primary" disabled={patch.isPending}>
                Kaydet
              </button>
              <button type="button" className="btn-ghost" onClick={() => setPwFor(null)}>
                Vazgeç
              </button>
            </form>
          )}
        </Card>
      </div>
    </>
  );
}
