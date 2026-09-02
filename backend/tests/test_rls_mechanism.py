"""
RLS-Mechanismus-Proof — gegen ECHTES Postgres.

Beweist die komplette Kette, auf der die Mandanten-RLS beruht:
- eine **Nicht-Superuser-Rolle** (Superuser umgehen RLS immer),
- `ENABLE` + `FORCE ROW LEVEL SECURITY`,
- eine Policy mit `USING` (Lesen) **und** `WITH CHECK` (Schreiben) über
  `current_setting('app.current_school_id')`,
- **fail-closed:** ohne gesetzte Variable sieht die Rolle NICHTS.

Damit ist das eigentliche Sicherheitsversprechen belegt: *eine vergessene Scope-Prüfung
oder eine Raw-Query kann den Mandanten nicht überschreiten — Postgres verweigert.*

Läuft nur mit `TEST_DATABASE_URL` (CI-Postgres bzw. lokaler Wegwerf-Container); sonst Skip.

Ausführen:
    TEST_DATABASE_URL=postgresql://test:test@localhost:55432/hagenid_test \
        python -m pytest tests/test_rls_mechanism.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from urllib.parse import urlparse

import pytest
from sqlalchemy import text, create_engine


APP_ROLE = "rls_app"
APP_PW = "rls_pw"


def _app_role_url() -> str:
    """URL wie TEST_DATABASE_URL, aber als eingeschränkte Nicht-Superuser-Rolle."""
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("Postgres-only Test: TEST_DATABASE_URL nicht gesetzt.")
    p = urlparse(base)
    return f"postgresql://{APP_ROLE}:{APP_PW}@{p.hostname}:{p.port}{p.path}"


@pytest.fixture
def rls_demo(pg_engine):
    """
    Baut als Superuser eine Demo-Tabelle mit RLS + Policy + eingeschränkter Rolle auf.
    (Bewusst eine eigene Tabelle: isoliert den MECHANISMUS von FK/Schema-Details.)
    """
    setup = [
        "DROP TABLE IF EXISTS rls_demo",
        "CREATE TABLE rls_demo (id serial PRIMARY KEY, school_id int NOT NULL, name text)",
        "INSERT INTO rls_demo (school_id, name) VALUES (1,'a1'),(1,'a2'),(2,'b1')",
        "ALTER TABLE rls_demo ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE rls_demo FORCE ROW LEVEL SECURITY",
        # Tenant-Isolation für Lesen UND Schreiben.
        """CREATE POLICY tenant_iso ON rls_demo
             USING      (school_id = current_setting('app.current_school_id', true)::int)
             WITH CHECK (school_id = current_setting('app.current_school_id', true)::int)""",
        # Rolle idempotent neu aufsetzen (evtl. Reste aus Vorlauf entfernen).
        f"""DO $$ BEGIN
              IF EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') THEN
                EXECUTE 'DROP OWNED BY {APP_ROLE}';
                EXECUTE 'DROP ROLE {APP_ROLE}';
              END IF;
            END $$""",
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PW}'",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON rls_demo TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ]
    with pg_engine.begin() as c:
        for stmt in setup:
            c.execute(text(stmt))

    yield

    teardown = [
        "DROP TABLE IF EXISTS rls_demo",
        f"""DO $$ BEGIN
              IF EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') THEN
                EXECUTE 'DROP OWNED BY {APP_ROLE}';
                EXECUTE 'DROP ROLE {APP_ROLE}';
              END IF;
            END $$""",
    ]
    with pg_engine.begin() as c:
        for stmt in teardown:
            c.execute(text(stmt))


def _app_conn():
    """Verbindung als eingeschränkte Rolle, autocommit (SET soll sofort greifen)."""
    eng = create_engine(_app_role_url())
    return eng, eng.connect().execution_options(isolation_level="AUTOCOMMIT")


def test_read_is_isolated_per_tenant(rls_demo):
    eng, c = _app_conn()
    try:
        c.execute(text("SET app.current_school_id = '1'"))
        rows = c.execute(text("SELECT school_id FROM rls_demo")).fetchall()
        assert len(rows) == 2 and {r[0] for r in rows} == {1}

        c.execute(text("SET app.current_school_id = '2'"))
        rows = c.execute(text("SELECT school_id FROM rls_demo")).fetchall()
        assert len(rows) == 1 and {r[0] for r in rows} == {2}
    finally:
        c.close(); eng.dispose()


def test_fail_closed_without_setting(rls_demo):
    # Kein SET → current_setting(..., true) = NULL → school_id = NULL → 0 Zeilen.
    eng, c = _app_conn()
    try:
        rows = c.execute(text("SELECT * FROM rls_demo")).fetchall()
        assert rows == []
    finally:
        c.close(); eng.dispose()


def test_raw_query_cannot_cross_tenant(rls_demo):
    # Selbst eine „vergessene" ungefilterte Query sieht nur den eigenen Mandanten.
    eng, c = _app_conn()
    try:
        c.execute(text("SET app.current_school_id = '1'"))
        # Versuch, gezielt eine fremde Zeile (school 2) zu lesen → RLS blockt.
        row = c.execute(text("SELECT * FROM rls_demo WHERE school_id = 2")).fetchall()
        assert row == []
    finally:
        c.close(); eng.dispose()


def test_write_check_blocks_foreign_tenant_insert(rls_demo):
    # WITH CHECK: Insert in einen FREMDEN Mandanten wird abgelehnt.
    import sqlalchemy
    eng, c = _app_conn()
    try:
        c.execute(text("SET app.current_school_id = '1'"))
        with pytest.raises(sqlalchemy.exc.ProgrammingError):
            c.execute(text("INSERT INTO rls_demo (school_id, name) VALUES (2, 'fremd')"))
        # Insert in den EIGENEN Mandanten geht.
        c.execute(text("INSERT INTO rls_demo (school_id, name) VALUES (1, 'eigen')"))
        cnt = c.execute(text("SELECT count(*) FROM rls_demo")).scalar()
        assert cnt == 3  # a1, a2, eigen (b1 gehört school 2 → unsichtbar)
    finally:
        c.close(); eng.dispose()
