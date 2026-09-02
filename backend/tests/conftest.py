"""
Gemeinsame Test-Fixtures.

Die meisten Tests laufen auf **In-Memory-SQLite** (schnell, setup-frei) — jede Testdatei
bringt dafür ihre eigene `session`-Fixture mit.

Für Dinge, die SQLite NICHT kann (Row-Level-Security, native ENUMs, echtes Migrations-/
`FORCE`-Verhalten) gibt es hier `pg_engine` / `pg_session`: sie laufen gegen ein **echtes
Postgres**, adressiert über `TEST_DATABASE_URL`.

- Ist `TEST_DATABASE_URL` nicht gesetzt → PG-Tests werden **übersprungen** (lokal Standard;
  in CI läuft ein Wegwerf-Postgres-Service, der die Variable setzt).
- **SICHERHEITSSPERRE:** Der Harness macht `drop_all` / `TRUNCATE`. Er weigert sich daher,
  gegen eine DB zu laufen, deren Name nicht klar als Wegwerf-Test-DB erkennbar ist
  (Name muss `test` enthalten). So kann ein falsch gesetztes `TEST_DATABASE_URL` niemals
  aus Versehen eine echte Datenbank plätten.
"""

import os

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine


def _require_test_pg_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Postgres-only Test: TEST_DATABASE_URL nicht gesetzt "
                    "(lokal übersprungen; läuft im CI-Postgres-Service).")
    dbname = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in dbname.lower():
        pytest.fail(
            f"TEST_DATABASE_URL zeigt auf DB '{dbname}' — VERWEIGERT. "
            "Die Test-DB muss 'test' im Namen haben (Schutz vor drop_all/TRUNCATE auf einer echten DB)."
        )
    return url


@pytest.fixture(scope="session")
def pg_engine():
    """Echte Postgres-Engine mit frischem Schema (einmal pro Test-Session)."""
    url = _require_test_pg_url()
    import models  # noqa: F401 — registriert alle Tabellen im SQLModel-Metadata
    engine = create_engine(url)
    # Sauberer Ausgangszustand
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def pg_session(pg_engine):
    """Session pro Test; danach werden alle Tabellen geleert (Testunabhängigkeit)."""
    with Session(pg_engine) as s:
        yield s
    tables = ", ".join(f'"{t.name}"' for t in reversed(SQLModel.metadata.sorted_tables))
    if tables:
        with pg_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
