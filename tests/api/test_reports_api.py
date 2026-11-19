from datetime import UTC, datetime, timedelta

from sqlmodel import select

from tradecrm.db.models import AdminAction, Report, Trade


class FakeNotifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)
        return self.ok


def _seed(db):
    exit_at = datetime.now(UTC) - timedelta(days=1)
    db.add(Trade(symbol="BTC/USDT", strategy_name="d", broker="paper", entry_time=exit_at - timedelta(hours=5),
                 entry_price=100, qty=1, account_balance_at_entry=1e4, exit_time=exit_at, exit_price=110,
                 status="CLOSED", realized_pnl=9.8, fees=0.2, exit_reason="trailing_stop"))
    db.commit()


def _create(authed, **body):
    return authed.post("/api/reports", json={"kind": "day", **body})


def test_requires_login(client):
    assert client.get("/api/reports").status_code == 401
    assert client.post("/api/reports", json={"kind": "day"}).status_code == 401


def test_create_list_get(authed, db_session):
    _seed(db_session)
    res = _create(authed)
    assert res.status_code == 201
    meta = res.json()
    assert meta["closed"] == 1 and meta["realized"] == 9.8 and meta["kind"] == "day"
    listing = authed.get("/api/reports").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == meta["id"]
    full = authed.get(f"/api/reports/{meta['id']}").json()
    assert full["data"]["summary"]["closed"] == 1 and full["data"]["generated_by"] == "ayse"
    acts = db_session.exec(select(AdminAction).where(AdminAction.action == "report_create")).all()
    assert len(acts) == 1


def test_create_validation(authed):
    assert _create(authed, kind="custom").status_code == 400
    assert _create(authed, kind="year").status_code == 400
    assert _create(authed, kind="custom", start="2026-11-05", end="2026-11-01").status_code == 400
    assert authed.post("/api/reports", json={"kind": "day", "start": "not-a-date"}).status_code == 422


def test_create_needs_csrf(authed):
    del authed.headers["X-CSRF-Token"]
    assert _create(authed).status_code == 403


def test_downloads(authed, db_session):
    _seed(db_session)
    rid = _create(authed).json()["id"]
    md = authed.get(f"/api/reports/{rid}/download?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"] and md.text.startswith("# Gunluk rapor")
    csv = authed.get(f"/api/reports/{rid}/download?format=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.lstrip("﻿").splitlines()[1].split(",")[2] == "BTC/USDT"
    assert authed.get(f"/api/reports/{rid}/download?format=exe").status_code == 422


def test_send_telegram(authed, ctx, db_session):
    rid = _create(authed).json()["id"]
    assert authed.post(f"/api/reports/{rid}/send").status_code == 400  # yapilandirilmamis
    ctx.notifier = FakeNotifier()
    res = authed.post(f"/api/reports/{rid}/send")
    assert res.status_code == 200 and res.json()["sent_at"]
    assert ctx.notifier.sent[0].startswith("TradeCRM - Gunluk rapor")
    ctx.notifier = FakeNotifier(ok=False)
    assert authed.post(f"/api/reports/{rid}/send").status_code == 502


def test_delete_and_404(authed, db_session):
    rid = _create(authed).json()["id"]
    assert authed.delete(f"/api/reports/{rid}").json() == {"deleted": rid}
    assert db_session.get(Report, rid) is None
    assert authed.get(f"/api/reports/{rid}").status_code == 404
    assert authed.delete(f"/api/reports/{rid}").status_code == 404


def test_account_name_is_validated(authed):
    """Hesap adi saklanir ve indirme dosya adina girer: serbest metin kabul edilmez."""
    for bad in ['paper"; x', "paper\nX-Evil: 1", "../etc", "PAPER", "paper:a b"]:
        assert authed.post("/api/reports", json={"kind": "day", "account": bad}).status_code == 422, bad
    for good in ["paper", "paper*", "paper:donchian_fng", "binance:testnet"]:
        assert authed.post("/api/reports", json={"kind": "day", "account": good}).status_code == 201, good
    assert authed.get('/api/pnl?account=paper"x').status_code == 422
