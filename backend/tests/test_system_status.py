"""
Tests für die Superadmin-System-Statusseite (GET /admin/system/status).

Kernaussagen:
- Nur Superadmin (schulübergreifende Betriebssicht).
- Kennzahlen + Sicherheits-Anomalien (24h) + letzter Job-Lauf werden korrekt aggregiert.

Ausführen:
    cd backend && python -m pytest tests/test_system_status.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from datetime import date, datetime, timedelta

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine
from fastapi import HTTPException

from models import School, Class, Student, User, AuditLog
import routes.admin as admin


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        school = School(name="Schule A", api_key_hash="x" * 64, sync_ip_allowlist_enabled=True)
        s.add(school); s.commit(); s.refresh(school)
        cls = Class(school_id=school.id, name="A1", validity_end=date(2027, 7, 31))
        s.add(cls); s.commit(); s.refresh(cls)
        # 2 aktive + 1 inaktiver Schüler
        for i, active in enumerate([True, True, False]):
            s.add(Student(school_internal_id=str(i), first_name="S", last_name=str(i),
                          dob=date(2005, 1, 1), class_id=cls.id, is_active=active))
        s.commit()
        yield s


def superadmin():
    return User(id=1, email="super@x.de", password_hash="x", role="superadmin",
                school_id=None, is_active=True)


def school_admin():
    return User(id=2, email="a@x.de", password_hash="x", role="school_admin",
                school_id=1, is_active=True)


def test_forbidden_for_school_admin(session):
    with pytest.raises(HTTPException) as exc:
        admin.system_status(current_admin=school_admin(), session=session)
    assert exc.value.status_code == 403


def test_status_counts_and_shape(session):
    st = admin.system_status(current_admin=superadmin(), session=session)
    assert st.db_ok is True
    assert st.uptime_seconds >= 0
    assert st.counts["schools"] == 1
    assert st.counts["students_total"] == 3
    assert st.counts["students_active"] == 2
    assert st.counts["admin_users"] >= 0
    # Key + Allowlist der Testschule schlagen sich in den Kennzahlen nieder
    assert st.security["schools_with_api_key"] == 1
    assert st.security["schools_with_allowlist"] == 1


def test_security_counts_last_24h(session):
    now = datetime.utcnow()
    # 2 frische Fehl-Logins + 1 alter (außerhalb 24h) → nur 2 zählen
    session.add(AuditLog(action="admin_login_failed", timestamp=now))
    session.add(AuditLog(action="admin_login_failed", timestamp=now - timedelta(hours=1)))
    session.add(AuditLog(action="admin_login_failed", timestamp=now - timedelta(hours=30)))
    session.add(AuditLog(action="sync_ip_blocked", timestamp=now, ip_address="8.8.8.8"))
    session.commit()

    st = admin.system_status(current_admin=superadmin(), session=session)
    assert st.security["failed_admin_logins_24h"] == 2
    assert st.security["sync_ip_blocked_24h"] == 1
    # sync_ip_blocked taucht in der Anomalie-Liste auf
    actions = {a["action"] for a in st.recent_anomalies}
    assert "sync_ip_blocked" in actions


def test_last_cleanup_heartbeat(session):
    session.add(AuditLog(action="system_cleanup_ran", timestamp=datetime.utcnow(),
                         details='{"students": 4}'))
    session.commit()
    st = admin.system_status(current_admin=superadmin(), session=session)
    assert st.last_cleanup is not None
    assert "students" in st.last_cleanup["details"]
