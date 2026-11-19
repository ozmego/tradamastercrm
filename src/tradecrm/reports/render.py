"""Rapor disa aktarimi: Markdown (indirme), kisa duz metin (Telegram), CSV (islemler)."""

import csv
import io

REASON_TR = {
    "stop_loss": "Zarar durdur", "trailing_stop": "Takip eden stop", "breakeven_stop": "Basabas stop",
    "take_profit": "Kar al", "time_stop": "Zaman stopu", "signal": "Sinyal", "end_of_data": "Veri sonu",
}


def _m(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:+,.{digits}f}"


def _p(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x:+.2%}" if signed else f"{x:.1%}"


def _pf(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def _d(iso: str | None) -> str:
    return (iso or "-")[:16].replace("T", " ")


def to_markdown(r: dict) -> str:
    s = r["summary"]
    per = r["period"]
    lines = [
        f"# {r['title']}",
        "",
        f"- Donem: {per['start'][:10]} - {per['end'][:10]} (UTC, bitis haric; {per['days']} gun)"
        + ("" if per["complete"] else " -- DONEM HENUZ BITMEDI"),
        f"- Hesap: {r['account'] or 'tum hesaplar'}",
        f"- Olusturma: {_d(r['generated_at'])} UTC ({r['generated_by'] or '-'})",
        "",
        "## Ozet",
        "",
        "| Gosterge | Deger |",
        "|---|---|",
        f"| Net gerceklesen K/Z | {_m(s['realized'])} USDT |",
        f"| Kapanan islem | {s['closed']} ({s['wins']} kazanc / {s['losses']} kayip) |",
        f"| Kazanma orani | {_p(s['win_rate'], signed=False)} |",
        f"| Kar faktoru | {_pf(s['profit_factor'])} |",
        f"| Islem basina beklenti | {_m(s['expectancy'])} USDT |",
        f"| Ort. kazanc / kayip | {_m(s['avg_win'])} / {_m(s['avg_loss'])} |",
        f"| En uzun kayip serisi | {s['longest_losing_streak']} |",
        f"| Komisyon | {s['fees']:,.2f} USDT |",
        f"| Donemde acilan / donem sonunda acik | {s['opened']} / {s['open_at_end']} |",
    ]
    if s.get("best"):
        lines.append(f"| En iyi islem | {s['best']['symbol']} {_m(s['best']['pnl'])} |")
    if s.get("worst"):
        lines.append(f"| En kotu islem | {s['worst']['symbol']} {_m(s['worst']['pnl'])} |")

    if r["equity"]:
        lines += ["", "## Hesap degeri", "", "| Hesap | Baslangic | Bitis | Degisim | Getiri | Maks. dusus |",
                  "|---|---|---|---|---|---|"]
        for e in r["equity"]:
            lines.append(f"| {e['account']} | {e['start']:,.2f} | {e['end']:,.2f} | {_m(e['change'])} | "
                         f"{_p(e['return_pct'])} | {_p(e['max_drawdown'], signed=False)} |")

    active_days = [d for d in r["daily"] if d["trades"]]
    if active_days:
        lines += ["", "## Gunluk K/Z (islem olan gunler)", "", "| Gun | Islem | K/Z | Kumulatif |", "|---|---|---|---|"]
        lines += [f"| {d['date']} | {d['trades']} | {_m(d['pnl'])} | {_m(d['cum'])} |" for d in active_days]

    if r["by_symbol"]:
        lines += ["", "## Sembol bazinda", "", "| Sembol | Islem | Kazanc | K/Z | Komisyon |", "|---|---|---|---|---|"]
        lines += [f"| {x['symbol']} | {x['trades']} | {x['wins']} | {_m(x['pnl'])} | {x['fees']:,.2f} |"
                  for x in r["by_symbol"]]

    if r["by_reason"]:
        lines += ["", "## Cikis nedenleri", "", "| Neden | Islem | Kazanc | K/Z |", "|---|---|---|---|"]
        lines += [f"| {REASON_TR.get(x['reason'], x['reason'])} | {x['count']} | {x['wins']} | {_m(x['pnl'])} |"
                  for x in r["by_reason"]]

    if r["risk_events"]:
        lines += ["", "## Risk olaylari", "", "| Zaman | Hesap | Olay | Neden |", "|---|---|---|---|"]
        lines += [f"| {_d(e['ts'])} | {e['account']} | {e['type']} | {(e['reason'] or '').replace('|', '/')} |"
                  for e in r["risk_events"]]

    c = r["collector"]
    collector_line = (f"{c['runs']} calisma: {c['ok']} basarili, {c['partial']} kismi, {c['failed']} basarisiz; "
                      f"+{c['rows_added']:,} satir eklendi, {c['rows_purged']:,} eski satir silindi.")
    lines += ["", "## Veri toplayici", "", collector_line]

    if r["trades"]:
        lines += ["", "## Kapanan islemler", "", "| Cikis | Hesap | Sembol | Giris | Cikis fiyati | K/Z | Getiri | Neden |",
                  "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {_d(t['exit_time'])} | {t['account']} | {t['symbol']} | {t['entry_price']:,.6g} | "
                  f"{(t['exit_price'] or 0):,.6g} | {_m(t['pnl'])} | {_p(t['return_pct'])} | "
                  f"{REASON_TR.get(t['exit_reason'] or '', t['exit_reason'] or '-')} |" for t in r["trades"]]
        if r.get("trades_truncated"):
            lines.append(f"\n({r['trades_truncated']} eski islem rapora sigmadi.)")
    lines.append("")
    return "\n".join(lines)


def to_text(r: dict) -> str:
    """Telegram icin kisa ozet."""
    s = r["summary"]
    lines = [f"TradeCRM - {r['title']}", f"{r['period']['start'][:10]} - {r['period']['end'][:10]} UTC (bitis haric)",
             (f"Net K/Z: {_m(s['realized'])} USDT | {s['closed']} islem ({s['wins']}K/{s['losses']}Z)"
              f" | kazanma {_p(s['win_rate'], signed=False)}")]
    if s["profit_factor"] is not None:
        lines.append(f"Kar faktoru {s['profit_factor']:.2f} | beklenti {_m(s['expectancy'])} | komisyon {s['fees']:,.2f}")
    for e in r["equity"]:
        lines.append(f"{e['account']}: {e['start']:,.2f} -> {e['end']:,.2f} ({_p(e['return_pct'])}, "
                     f"maks. dusus {_p(e['max_drawdown'], signed=False)})")
    if r["by_symbol"]:
        top = r["by_symbol"][:3]
        lines.append("En iyi semboller: " + ", ".join(f"{x['symbol']} {_m(x['pnl'])}" for x in top))
    if r["risk_events"]:
        lines.append(f"Risk olayi: {len(r['risk_events'])} (son: {r['risk_events'][-1]['type']})")
    c = r["collector"]
    if c["runs"]:
        lines.append(f"Toplayici: {c['ok']}/{c['runs']} basarili")
    return "\n".join(lines)


CSV_FIELDS = ["id", "account", "symbol", "entry_time", "entry_price", "exit_time", "exit_price", "qty", "pnl",
              "return_pct", "fees", "exit_reason", "hours_held"]


def trades_csv(r: dict) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for t in r["trades"]:
        w.writerow(t)
    return buf.getvalue()
