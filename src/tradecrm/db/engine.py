from contextlib import contextmanager

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from tradecrm.db import (
    models as _models,  # noqa: F401  (tablolarin metadata'ya kaydolmasi icin)
)

_engines: dict[str, object] = {}


def get_engine(database_url: str):
    if database_url not in _engines:
        _engines[database_url] = create_engine(database_url, pool_pre_ping=True)
    return _engines[database_url]


# create_all mevcut tablolara yeni kolon eklemez. Alembic'e gecilene kadar,
# sonradan eklenen (nullable/default'lu) kolonlar burada idempotent eklenir.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "trade": {
        "entry_fees": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        "exit_reason": "VARCHAR",
        "initial_stop_price": "DOUBLE PRECISION",
        "stop_price": "DOUBLE PRECISION",
        "take_profit_price": "DOUBLE PRECISION",
        "highest_price": "DOUBLE PRECISION",
        "entry_client_order_id": "VARCHAR",
        "exit_client_order_id": "VARCHAR",
    },
    # Eski (hesapsiz) risk olaylari paper kok hesabina aittir: o donemde sadece paper vardi.
    "riskevent": {
        "account": "VARCHAR NOT NULL DEFAULT 'paper'",
    },
}


def create_db_and_tables(database_url: str) -> None:
    engine = get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    apply_column_migrations(engine)


def apply_column_migrations(engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))


def reset_engine(database_url: str) -> None:
    """Testlerde her test icin taze bir engine kullanmak icin."""
    engine = _engines.pop(database_url, None)
    if engine is not None:
        engine.dispose()


@contextmanager
def get_session(database_url: str):
    engine = get_engine(database_url)
    with Session(engine) as session:
        yield session
