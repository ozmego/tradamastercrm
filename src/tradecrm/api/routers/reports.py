"""Raporlar: donem raporu olustur, listele, goruntule, indir (Markdown/CSV), Telegram'a gonder, sil."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db, require_csrf
from tradecrm.api.services.users import record_action
from tradecrm.db.models import Report
from tradecrm.reports.builder import KINDS, ReportParamsError
from tradecrm.reports.render import to_markdown, to_text, trades_csv
from tradecrm.reports.store import create_report, list_reports, report_full, report_meta

router = APIRouter(tags=["reports"])


class ReportIn(BaseModel):
    kind: str = Field(max_length=10)
    start: date | None = None
    end: date | None = None
    # Hesap adi: paper, paper:<profil>, binance, binance:testnet; sonunda * = tum alt hesaplar.
    # Serbest metin degil: saklanir ve indirme dosya adina (HTTP basligina) girer.
    account: str | None = Field(default=None, max_length=64, pattern=r"^[a-z]+(:[a-z0-9_-]+)?\*?$")


def _get(db: Session, report_id: int) -> Report:
    row = db.get(Report, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="rapor bulunamadi")
    return row


def _filename(row: Report, ext: str) -> str:
    acc = (row.account or "tum").replace(":", "-").replace("*", "")
    return f"tradecrm-rapor-{row.id}-{row.kind}-{row.period_start:%Y%m%d}-{acc}.{ext}"


@router.get("/reports")
def get_reports(limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0), kind: str | None = None,
                _: Auth = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=400, detail="gecersiz rapor turu")
    rows, total = list_reports(db, limit=limit, offset=offset, kind=kind)
    return {"items": [report_meta(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/reports", status_code=201)
def post_report(body: ReportIn, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    try:
        row = create_report(db, body.kind, body.start, body.end, account=body.account or None,
                            created_by=auth.username)
    except ReportParamsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    record_action(db, auth.username, "report_create", account=row.account,
                  detail={"id": row.id, "kind": row.kind, "title": row.title})
    return report_meta(row)


@router.get("/reports/{report_id}")
def get_report(report_id: int, _: Auth = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return report_full(_get(db, report_id))


@router.get("/reports/{report_id}/download")
def download_report(report_id: int, format: str = Query("md", pattern="^(md|csv)$"),
                    _: Auth = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    row = _get(db, report_id)
    data = report_full(row)["data"]
    if format == "csv":
        body, media = "﻿" + trades_csv(data), "text/csv; charset=utf-8"  # BOM: Excel Turkce karakter
    else:
        body, media = to_markdown(data), "text/markdown; charset=utf-8"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{_filename(row, format)}"'})


@router.post("/reports/{report_id}/send")
def send_report(report_id: int, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                db: Session = Depends(get_db)) -> dict:
    row = _get(db, report_id)
    if ctx.notifier is None:
        raise HTTPException(status_code=400, detail="Telegram yapilandirilmamis (.env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    ok = ctx.notifier.send_message(to_text(report_full(row)["data"]))
    record_action(db, auth.username, "report_send", account=row.account, detail={"id": row.id}, ok=bool(ok))
    if not ok:
        raise HTTPException(status_code=502, detail="Telegram mesaji gonderilemedi")
    row.sent_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    return report_meta(row)


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    row = _get(db, report_id)
    meta = report_meta(row)
    db.delete(row)
    db.commit()
    record_action(db, auth.username, "report_delete", account=meta["account"],
                  detail={"id": report_id, "title": meta["title"]})
    return {"deleted": report_id}
