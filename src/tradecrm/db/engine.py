from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from tradecrm.db import models as _models  # noqa: F401  (tablolarin metadata'ya kaydolmasi icin)

_engines: dict[str, object] = {}


def get_engine(database_url: str):
    if database_url not in _engines:
        _engines[database_url] = create_engine(database_url, pool_pre_ping=True)
    return _engines[database_url]


def create_db_and_tables(database_url: str) -> None:
    engine = get_engine(database_url)
    SQLModel.metadata.create_all(engine)


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
