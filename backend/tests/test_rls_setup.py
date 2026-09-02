"""
RLS-Setup-Test — testet das ECHTE Produktions-Setup aus services.rls.ensure_rls_setup:
Rolle + Grants + Policies auf allen Tenant-Tabellen (aus Metadata abgeleitet).

Beweist gegen echtes Postgres:
- ein Mandant sieht NUR seine Schule/Klassen/Schüler (auch die schools-Policy auf `id`),
- Superadmin (clear_tenant → Superuser) sieht alles (Bypass),
- ein Cross-Tenant-INSERT wird von WITH CHECK verhindert.

Postgres-only (TEST_DATABASE_URL). Ausführen:
    cd backend && TEST_DATABASE_URL=... python -m pytest tests/test_rls_setup.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import text
from sqlmodel import Session, select, func, SQLModel

from models import School, Class, Student
from services import rls


def _tenant_tables():
    return [t.name for t in SQLModel.metadata.sorted_tables if "school_id" in t.columns]


def _teardown_rls(engine):
    stmts = []
    for t in ["schools"] + _tenant_tables():
        stmts += [
            f"DROP POLICY IF EXISTS tenant_iso ON {t}",
            f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
        ]
    stmts.append(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='hagenid_app') THEN "
        "EXECUTE 'DROP OWNED BY hagenid_app'; EXECUTE 'DROP ROLE hagenid_app'; END IF; END $$"
    )
    with engine.begin() as c:
        for s in stmts:
            c.execute(text(s))


@pytest.fixture
def rls_setup(pg_engine, monkeypatch):
    monkeypatch.setattr(rls, "RLS_ENABLED", True)
    rls.ensure_rls_setup(pg_engine)      # <-- das ECHTE Produktions-Setup
    rls.register(pg_engine)

    # Sauberer Ausgangszustand (Fixture nutzt kein pg_session → selbst truncaten)
    with pg_engine.begin() as c:
        tbls = ", ".join(f'"{t.name}"' for t in reversed(SQLModel.metadata.sorted_tables))
        c.execute(text(f"TRUNCATE {tbls} RESTART IDENTITY CASCADE"))

    with Session(pg_engine) as s:        # privilegiert (kein set_tenant) → beide Schulen anlegen
        a = School(name="A"); b = School(name="B")
        s.add(a); s.add(b); s.commit(); s.refresh(a); s.refresh(b)
        ca = Class(school_id=a.id, name="A1", validity_end=date(2027, 7, 31))
        cb = Class(school_id=b.id, name="B1", validity_end=date(2027, 7, 31))
        s.add(ca); s.add(cb); s.commit(); s.refresh(ca); s.refresh(cb)
        s.add(Student(school_internal_id="a0", first_name="x", last_name="y",
                      dob=date(2005, 1, 1), class_id=ca.id, is_active=True))
        s.add(Student(school_internal_id="b0", first_name="x", last_name="y",
                      dob=date(2005, 1, 1), class_id=cb.id, is_active=True))
        s.commit()
        ids = (a.id, b.id)

    yield ids

    rls.unregister(pg_engine)
    _teardown_rls(pg_engine)


def test_tenant_sees_only_own_school(rls_setup, pg_engine):
    a, _b = rls_setup
    with Session(pg_engine) as s:
        rls.set_tenant(s, a)
        assert s.exec(select(func.count(Student.id))).one() == 1     # nur A's Schüler
        assert s.exec(select(func.count(Class.id))).one() == 1       # nur A's Klasse
        assert [sc.name for sc in s.exec(select(School)).all()] == ["A"]  # schools-Policy auf id


def test_superadmin_bypass_sees_all(rls_setup, pg_engine):
    with Session(pg_engine) as s:
        rls.clear_tenant(s)                                          # kein Mandant → Superuser
        assert s.exec(select(func.count(School.id))).one() == 2
        assert s.exec(select(func.count(Student.id))).one() == 2


def test_with_check_blocks_cross_tenant_insert(rls_setup, pg_engine):
    a, b = rls_setup
    with Session(pg_engine) as s:
        rls.set_tenant(s, a)                                         # als Mandant A ...
        s.add(Class(school_id=b, name="Fremd", validity_end=date(2027, 7, 31)))  # ... Klasse für B
        with pytest.raises(Exception):                              # WITH CHECK verhindert es
            s.commit()
