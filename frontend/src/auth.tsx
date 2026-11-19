import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CandlestickChart, Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api/client";
import type { Me } from "./api/types";

interface AuthState {
  me: Me;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("AuthProvider yok");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setCsrfToken(null);
      qc.setQueryData(["me"], null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    });
  }, [qc]);

  useEffect(() => {
    setCsrfToken(meQuery.data?.csrf_token ?? null);
  }, [meQuery.data]);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setCsrfToken(null);
      qc.clear();
      qc.setQueryData(["me"], null);
    }
  }, [qc]);

  if (meQuery.isLoading) {
    return (
      <div className="grid h-full place-items-center text-muted">
        <Loader2 className="animate-spin" />
      </div>
    );
  }
  if (!meQuery.data) return <LoginPage />;
  return <AuthContext.Provider value={{ me: meQuery.data, logout }}>{children}</AuthContext.Provider>;
}

function LoginPage() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api.post<Me>("/auth/login", { username, password });
      setCsrfToken(me.csrf_token);
      qc.setQueryData(["me"], me);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sunucuya ulaşılamadı");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative grid min-h-full place-items-center overflow-hidden px-4 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-60"
        style={{
          background:
            "radial-gradient(60rem 30rem at 15% -10%, rgb(var(--accent) / 0.16), transparent 60%), radial-gradient(40rem 24rem at 100% 110%, rgb(var(--gold) / 0.12), transparent 60%)",
        }}
      />
      <div className="relative w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl border border-line bg-surface text-gold shadow-card">
            <CandlestickChart size={22} strokeWidth={1.8} />
          </span>
          <div>
            <div className="text-[18px] font-semibold tracking-tight">TradeCRM</div>
            <div className="text-[12px] text-muted">Yönetim paneli</div>
          </div>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <LockKeyhole size={15} className="text-gold" /> Giriş yap
          </div>
          <label className="block">
            <span className="label mb-1.5 block">Kullanıcı adi</span>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
          </label>
          <label className="block">
            <span className="label mb-1.5 block">Parola</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && <div className="rounded-lg bg-loss/10 px-3 py-2 text-[12.5px] text-loss">{error}</div>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
            Giriş
          </button>
          <p className="text-center text-[11.5px] text-muted">
            İlk kullanıcı: <span className="num">tradecrm admin create-user</span>
          </p>
        </form>
      </div>
    </div>
  );
}

export function isApiError(e: unknown, status?: number): e is ApiError {
  return e instanceof ApiError && (status === undefined || e.status === status);
}
