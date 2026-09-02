"""
Postgres-Smoke-Tests.

Beweisen, dass der Postgres-Test-Harness trägt — die Grundlage, um RLS-Policies
gegen ein echtes Postgres zu testen (SQLite kann kein RLS).

Läuft NUR mit gesetztem `TEST_DATABASE_URL` (in CI: Wegwerf-Postgres-Service). Lokal ohne
Postgres werden diese Tests übersprungen — siehe tests/conftest.py.

Ausführen (mit Wegwerf-DB):
    TEST_DATABASE_URL=postgresql://test:test@localhost:5432/hagenid_test \
        python -m pytest tests/test_pg_smoke.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import School, Class, Student, User, UserRole


def test_pg_connects_and_schema_exists(pg_session):
    """Verbindung steht + create_all lief → einfache CRUD-Runde auf echtem Postgres."""
    school = School(name="RLS-Testschule")
    pg_session.add(school)
    pg_session.commit()
    pg_session.refresh(school)
    assert school.id is not None
    assert pg_session.get(School, school.id).name == "RLS-Testschule"


def test_userrole_is_native_enum_roundtrip(pg_session):
    """
    Auf Postgres ist `UserRole` ein echtes ENUM (SQLite faket das nur als TEXT).
    Der Roundtrip stellt sicher, dass das Schema mit nativem ENUM sauber lebt —
    relevant, weil RLS/FORCE genau auf diesem echten Schema arbeitet.
    """
    u = User(email="super@x.de", password_hash="x", role=UserRole.SUPERADMIN, is_active=True)
    pg_session.add(u)
    pg_session.commit()
    pg_session.refresh(u)
    got = pg_session.get(User, u.id)
    assert got.role == UserRole.SUPERADMIN


def test_indirect_tenant_chain_student_class_school(pg_session):
    """
    Die Kette students → classes → schools: Ohne die denormalisierte `school_id`
    wäre die Schule eines Schülers nur über die Klasse erreichbar — genau deshalb
    denormalisieren die ORM-Events sie für die RLS-Policies. Dieser Test hält
    die zugrunde liegende Kette fest.
    """
    school = School(name="S")
    pg_session.add(school); pg_session.commit(); pg_session.refresh(school)

    cls = Class(school_id=school.id, name="A1", validity_end=date(2027, 7, 31))
    pg_session.add(cls); pg_session.commit(); pg_session.refresh(cls)

    st = Student(school_internal_id="1", first_name="Max", last_name="M",
                 dob=date(2005, 1, 1), class_id=cls.id, is_active=True)
    pg_session.add(st); pg_session.commit(); pg_session.refresh(st)

    # school_id ist nur über die Klasse erreichbar
    assert pg_session.get(Class, st.class_id).school_id == school.id


def test_truncate_isolation_between_tests(pg_session):
    """
    Nach jedem Test werden die Tabellen geleert (conftest). Dieser Test darf die
    Schulen aus den vorherigen Tests NICHT mehr sehen → Testunabhängigkeit.
    """
    from sqlmodel import select, func
    count = pg_session.exec(select(func.count(School.id))).one()
    assert count == 0
