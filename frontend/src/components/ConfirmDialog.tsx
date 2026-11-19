import clsx from "clsx";
import { ShieldAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Risk islemleri icin onay penceresi: kullanici hesap adini AYNEN yazmadan
 * onay dugmesi aktif olmaz (yanlislikla tiklamaya karsi). Backend de ayni
 * metni dogrular.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  actionLabel,
  danger = false,
  busy = false,
  error,
  extra,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmText: string;
  actionLabel: string;
  danger?: boolean;
  busy?: boolean;
  error?: string | null;
  extra?: ReactNode;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setTyped("");
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const matches = typed === confirmText;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={title}>
      <div className="card w-full max-w-md">
        <div className="flex items-start justify-between gap-3 border-b border-line/60 px-5 py-4">
          <div className="flex items-center gap-3">
            <span className={clsx("grid h-9 w-9 place-items-center rounded-lg", danger ? "bg-loss/15 text-loss" : "bg-accent/15 text-accent")}>
              <ShieldAlert size={18} />
            </span>
            <h2 className="text-[15px] font-semibold">{title}</h2>
          </div>
          <button className="text-muted hover:text-fg" onClick={onClose} aria-label="Kapat">
            <X size={18} />
          </button>
        </div>
        <form
          className="space-y-4 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (matches && !busy) onConfirm();
          }}
        >
          <div className="text-[13px] leading-relaxed text-muted">{description}</div>
          {extra}
          <label className="block">
            <span className="label mb-1.5 block">
              Onaylamak için <span className="num normal-case text-fg">{confirmText}</span> yazın
            </span>
            <input
              ref={inputRef}
              className="input num"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-label="Onay metni"
            />
          </label>
          {error && <div className="rounded-lg bg-loss/10 px-3 py-2 text-[12.5px] text-loss">{error}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Vazgeç
            </button>
            <button type="submit" className={danger ? "btn-danger" : "btn-primary"} disabled={!matches || busy}>
              {busy ? "İşleniyor..." : actionLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
