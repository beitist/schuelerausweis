"""
Choke-Point-Test für den RLS-Mandanten (services/rls.py).

Beweist:
1. `session.info` überlebt die FastAPI-Threadpool-Grenze (Dependency vs. Endpoint) —
   eine ContextVar würde genau daran scheitern. (kein Postgres nötig)
2. gegen echtes Postgres: Mandanten-Isolation **überlebt einen Mid-Request-Commit**
   (das `after_begin`-Event re-appliziert bei jedem Transaktionsbeginn), Superadmin-Bypass,
   und eine Session ohne gesetzten Mandanten läuft privilegiert (public/Startup).
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import threading
from datetime import date

import anyio
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlmodel import Session, create_engine, select, func

from models import School, Class, Student
from services import rls


# ---------------------------------------------------------------------------
# 1. Kern-Beweis: session.info überlebt die Threadpool-Grenze
#    (eine ContextVar sähe der Endpoint als None — session.info ist geteilt).
# ---------------------------------------------------------------------------

def test_session_info_survives_threadpool_boundary():
    eng = create_engine("sqlite://")
    with Session(eng) as s:
        async def run():
            def dependency():
                s.info["rls_school_id"] = 99          # wie set_tenant()
                return threading.get_ident()
            def endpoint():
                return s.info.get("rls_school_id"), threading.get_ident()
            await anyio.to_thread.run_sync(dependency)
            return await anyio.to_thread.run_sync(endpoint)
        val, _ = anyio.run(run)
    assert val == 99  # mit einer ContextVar wäre das None (fail-open)


# ---------------------------------------------------------------------------
# 2. Gegen echtes Postgres
# ---------------------------------------------------------------------------

@pytest.fixture
def rls_env(pg_engine):
    setup = [
        "TRUNCATE students, classes, schools RESTART IDENTITY CASCADE",
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='hagenid_app') THEN "
        "EXECUTE 'DROP OWNED BY hagenid_app'; EXECUTE 'DROP ROLE hagenid_app'; END IF; END $$",
        "CREATE ROLE hagenid_app NOSUPERUSER",
        "GRANT USAGE ON SCHEMA public TO hagenid_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON students TO hagenid_app",
        "ALTER TABLE students ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE students FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_iso ON students",
        """CREATE POLICY tenant_iso ON students
             USING      (school_id = current_setting('app.current_school_id', true)::int)
             WITH CHECK (school_id = current_setting('app.current_school_id', true)::int)""",
    ]
    with pg_engine.begin() as c:
        for s in setup:
            c.execute(text(s))
    with Session(pg_engine) as s:
        a = School(name="A"); b = School(name="B")
        s.add(a); s.add(b); s.commit(); s.refresh(a); s.refresh(b)
        ca = Class(school_id=a.id, name="A1", validity_end=date(2027, 7, 31))
        cb = Class(school_id=b.id, name="B1", validity_end=date(2027, 7, 31))
        s.add(ca); s.add(cb); s.commit(); s.refresh(ca); s.refresh(cb)
        for i in range(2):
            s.add(Student(school_internal_id=f"a{i}", first_name="x", last_name="y",
                          dob=date(2005, 1, 1), class_id=ca.id, is_active=True))
        s.add(Student(school_internal_id="b0", first_name="x", last_name="y",
                      dob=date(2005, 1, 1), class_id=cb.id, is_active=True))
        s.commit()
        ids = (a.id, b.id)

    rls.RLS_ENABLED = True
    rls.register(pg_engine)
    yield ids
    rls.unregister(pg_engine)
    rls.RLS_ENABLED = False
    teardown = [
        "DROP POLICY IF EXISTS tenant_iso ON students",
        "ALTER TABLE students NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE students DISABLE ROW LEVEL SECURITY",
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='hagenid_app') THEN "
        "EXECUTE 'DROP OWNED BY hagenid_app'; EXECUTE 'DROP ROLE hagenid_app'; END IF; END $$",
    ]
    with pg_engine.begin() as c:
        for s in teardown:
            c.execute(text(s))


def _count(session):
    return session.exec(select(func.count(Student.id))).one()


def test_isolation_survives_commit(rls_env, pg_engine):
    a, b = rls_env
    with Session(pg_engine) as s:
        rls.set_tenant(s, a)
        assert _count(s) == 2      # nur Schule A
        s.commit()                 # <-- der kritische Punkt: neue Transaktion danach
        assert _count(s) == 2      # after_begin re-appliziert → immer noch A (kein fail-open)
        s.commit()
        assert _count(s) == 2


def test_superadmin_bypass(rls_env, pg_engine):
    a, b = rls_env
    with Session(pg_engine) as s:
        rls.clear_tenant(s)        # kein Mandant → Superuser → RLS-Bypass
        assert _count(s) == 3


def test_unauth_session_is_privileged(rls_env, pg_engine):
    # Session, die NIE einen Mandanten setzt (public-Route/Startup) → after_begin _UNSET
    # → privilegiert (sieht alles). Wichtig: /check, /verify-cert etc. brauchen das.
    a, b = rls_env
    with Session(pg_engine) as s:
        assert _count(s) == 3
