// Finansal bicimlendirme: tr-TR binlik ayiraci, isaretli yuzde, kisa tutar.

const LOCALE = "tr-TR";

export const DASH = "—";

export function isNum(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

export function money(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return DASH;
  return x.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function usd(x: number | null | undefined, digits = 2): string {
  return isNum(x) ? `${money(x, digits)} USDT` : DASH;
}

/** 0.0123 -> "+1,23%" (signed=true) */
export function pct(x: number | null | undefined, digits = 2, signed = true): string {
  if (!isNum(x)) return DASH;
  const v = x * 100;
  const s = Math.abs(v).toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (!signed) return `${v < 0 ? "−" : ""}%${s}`;
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}%${s}`;
}

export function signedMoney(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return DASH;
  const sign = x > 0 ? "+" : x < 0 ? "−" : "";
  return `${sign}${money(Math.abs(x), digits)}`;
}

/** 12345678 -> "12,3 Mn" */
export function compact(x: number | null | undefined): string {
  if (!isNum(x)) return DASH;
  return x.toLocaleString(LOCALE, { notation: "compact", maximumFractionDigits: 1 });
}

/** Fiyat: buyukluge gore ondalik (BTC 2, DOGE 5). */
export function price(x: number | null | undefined): string {
  if (!isNum(x)) return DASH;
  const a = Math.abs(x);
  const digits = a >= 1000 ? 2 : a >= 1 ? 4 : 6;
  return x.toLocaleString(LOCALE, { minimumFractionDigits: Math.min(digits, 2), maximumFractionDigits: digits });
}

export function qty(x: number | null | undefined): string {
  if (!isNum(x)) return DASH;
  return x.toLocaleString(LOCALE, { maximumFractionDigits: 6 });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleString(LOCALE, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function date(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? DASH : d.toLocaleDateString(LOCALE, { day: "2-digit", month: "short", year: "numeric" });
}

export function relative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return DASH;
  const s = Math.round((now - t) / 1000);
  if (s < 60) return "az önce";
  if (s < 3600) return `${Math.floor(s / 60)} dk önce`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa önce`;
  return `${Math.floor(s / 86400)} gün önce`;
}

export type Tone = "gain" | "loss" | "flat";

export function tone(x: number | null | undefined, epsilon = 1e-12): Tone {
  if (!isNum(x) || Math.abs(x) < epsilon) return "flat";
  return x > 0 ? "gain" : "loss";
}

export const EXIT_REASONS: Record<string, string> = {
  stop_loss: "Zarar durdur",
  end_of_data: "Veri sonu",
  trailing_stop: "Takip eden stop",
  breakeven_stop: "Başabaş stop",
  take_profit: "Kâr al",
  time_stop: "Zaman stopu",
  signal: "Sinyal",
};

export function exitReason(r: string | null | undefined): string {
  if (!r) return DASH;
  return EXIT_REASONS[r] ?? r;
}

/** Panel islem kaydi (AdminAction.action) -> Turkce. Bilinmeyen ad oldugu gibi gosterilir. */
export const ACTION_LABELS: Record<string, string> = {
  login: "Giriş",
  login_failed: "Başarısız giriş",
  logout: "Çıkış",
  live_start: "Paper runner başlatıldı",
  live_stop: "Paper runner durduruldu",
  collector_start: "Toplayıcı başlatıldı",
  collector_stop: "Toplayıcı durduruldu",
  collector_run: "Toplayıcı elle çalıştırıldı",
  autostart_enable: "Otomatik başlatma açıldı",
  autostart_disable: "Otomatik başlatma kapatıldı",
  trading_enabled: "Alım izni açıldı",
  trading_disabled: "Alım izni kapatıldı",
  trading_enable_refused: "Alım izni açma reddedildi",
  kill_switch_trip: "Kill-switch tetiklendi",
  kill_switch_reset: "Kill-switch sıfırlandı",
  kill_switch_reset_refused: "Kill-switch sıfırlama reddedildi",
  backtest_start: "Backtest başlatıldı",
  backtest_cancel: "Backtest iptal edildi",
  baseline_saved: "Drift baseline'ı kaydedildi",
  report_create: "Rapor oluşturuldu",
  report_send: "Rapor gönderildi",
  report_delete: "Rapor silindi",
  user_created: "Kullanıcı oluşturuldu",
  user_activated: "Kullanıcı etkinleştirildi",
  user_deactivated: "Kullanıcı devre dışı bırakıldı",
  user_password_changed: "Parola değiştirildi",
};

export function actionLabel(a: string): string {
  return ACTION_LABELS[a] ?? a;
}
