"""Admin paneli: FastAPI backend + derlenmis React SPA (static/).

Guvenlik ozeti:
- Butun /api uclari oturum ister (auth/login haric); durum degistirenler
  ayrica oturuma bagli CSRF token'i ister.
- Cerez: HttpOnly + SameSite=Strict (+ HTTPS arkasinda Secure).
- Sikı CSP: yalnizca ayni kaynaktan script/stil/baglanti; iframe'e gomulemez.
- Canli hesapta alim izni acmak / ilk emir onayi panelden yapilamaz (CLI).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tradecrm.api.context import AppContext
from tradecrm.api.routers import (
    activity,
    auth,
    collectors,
    control,
    portfolio,
    reports,
    research,
    users,
)

DEFAULT_STATIC_DIR = Path(__file__).parent / "static"

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

_NO_BUILD = """<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>TradeCRM</title></head>
<body style="font-family:system-ui;padding:40px;background:#0b0f14;color:#e6e8eb">
<h1>Panel derlenmemis</h1><p>Arayuzu derlemek icin:</p>
<pre>cd frontend
npm ci
npm run build</pre><p>API calisiyor: <code>/api/auth/me</code></p></body></html>"""


def create_app(ctx: AppContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if ctx.backtests is not None:
            ctx.backtests.shutdown()
        if ctx.collector_trigger is not None:
            ctx.collector_trigger.shutdown()

    app = FastAPI(title="TradeCRM Admin", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    for module in (auth, portfolio, activity, control, research, users, collectors, reports):
        app.include_router(module.router, prefix="/api")

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api"):
            detail = getattr(exc, "detail", "bulunamadi")
            return JSONResponse({"detail": detail}, status_code=404)
        return _spa_index(ctx)

    static_dir = ctx.static_dir or DEFAULT_STATIC_DIR
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api"):
            return JSONResponse({"detail": "bulunamadi"}, status_code=404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return _spa_index(ctx)

    return app


def _spa_index(ctx: AppContext):
    index = (ctx.static_dir or DEFAULT_STATIC_DIR) / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_NO_BUILD)


def _notifier(settings):
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return None
    from tradecrm.notify.telegram import TelegramNotifier

    return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)


def build_context(settings) -> AppContext:
    """Gercek bagimliliklarla context (CLI 'admin serve')."""
    from sqlmodel import Session

    from tradecrm.api.context import CachedMarket
    from tradecrm.api.services.autostart import AutostartService
    from tradecrm.api.services.backtests import BacktestQueue
    from tradecrm.api.services.collectors import CollectorTrigger
    from tradecrm.api.services.processes import (
        collector_process,
        exchange_runner_process,
        live_runner_process,
    )
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.db.engine import create_db_and_tables, get_engine

    create_db_and_tables(settings.database_url)
    engine = get_engine(settings.database_url)

    def session_factory():
        return Session(engine)

    queue = BacktestQueue(session_factory)
    queue.mark_interrupted()
    collect_symbols = [x.strip() for x in settings.collect_symbols.split(",") if x.strip()]
    return AppContext(
        session_factory=session_factory,
        market=CachedMarket(CCXTDataProvider()),
        collector=collector_process(),
        live=live_runner_process(),
        exchange_runners={"kucoin": exchange_runner_process("kucoin")},
        backtests=queue,
        session_hours=settings.admin_session_hours,
        cookie_secure=settings.admin_cookie_secure,
        audit_log_path=settings.audit_log_path,
        live_trading_allowed_by_env=settings.trading_enabled and not settings.dry_run,
        collect_symbols=collect_symbols,
        collect_retention_days=settings.collect_retention_days,
        collector_trigger=CollectorTrigger(session_factory, collect_symbols, settings.collect_retention_days),
        autostart=AutostartService(Path.cwd()),
        notifier=_notifier(settings),
        started_at=datetime.now(UTC).isoformat(),
    )
