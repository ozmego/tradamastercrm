import os

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

import tradecrm.db.models  # noqa: F401  (tablolarin metadata'ya kaydolmasi icin)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://tradecrm:tradecrm@localhost:5434/tradecrm_test",
)


@pytest.fixture(scope="session")
def _test_engine():
    from sqlalchemy.exc import OperationalError

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.exit(
            "Test PostgreSQL'ine baglanilamadi. Yerelde 'docker compose up -d' calistirin "
            "(CI'da .github/workflows/tests.yml servisi ayaga kaldirir). URL: TEST_DATABASE_URL",
            returncode=3,
        )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(_test_engine):
    """Her test kendi SAVEPOINT'inde calisir, test sonunda tamamen geri
    alinir -- testler gerçek Postgres'e karsi calisir ama birbirinden
    izole kalir ve her test icin schema yeniden olusturulmaz (hizli).

    Uygulama kodu (repository) session.commit() cagirdiginda, disaridaki
    asil transaction'i degil, sadece bu SAVEPOINT'i kapatir; asagidaki
    event listener otomatik olarak yeni bir SAVEPOINT acar."""
    connection = _test_engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
