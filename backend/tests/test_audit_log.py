"""
Tests für das zentrale Audit-Logging (services/audit.py) und den
Superadmin-Log-Viewer (GET /admin/audit-logs).

Kernaussagen:
- record() schreibt pseudonym (subject_id = ID, nicht Klarname).
- purge_old() setzt die 180-Tage-Retention durch.
- Der Viewer ist NUR für Superadmins (schulübergreifende, personenbezogene Daten).
- Filter (action / school_id / subject_id / since / until) und Pagination greifen,
  Reihenfolge ist neueste-zuerst.

Ausführen:
    cd backend && python -m pytest tests/test_audit_log.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import datetime, timedelta

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine, select
from fastapi import HTTPException

from models import AuditLog, User
from services import audit
import routes.admin as admin


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def superadmin():
    return User(id=1, email="super@x.de", password_hash="x", role="superadmin",
                school_id=None, is_active=True)


def school_admin(school_id=1):
    return User(id=2, email="admin@x.de", password_hash="x", role="school_admin",
                school_id=school_id, is_active=True)


def call_viewer(session, current_admin, *, action=None, school_id=None,
                subject_id=None, since=None, until=None, limit=100, offset=0):
    """
    Direktaufruf des Handlers mit expliziten Werten.

    Nötig, weil die Query(...)-Defaults beim Direktaufruf (ohne HTTP-Layer)
    KEIN None sind, sondern truthy Query-Objekte — deshalb setzen wir sie hier
    explizit, so wie FastAPI sie zur Laufzeit auflösen würde.
    """
    return admin.list_audit_logs(
        action=action, school_id=school_id, subject_id=subject_id,
        since=since, until=until, limit=limit, offset=offset,
        current_admin=current_admin, session=session,
    )


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------

def test_record_writes_row_with_fields(session):
    audit.record(session, "id_verified", subject_id="abc-123",
                 school_id=1, target_table="students", result="valid",
                 ip="203.0.113.7", extra={"card_type": "student"})
    row = session.exec(select(AuditLog)).one()
    assert row.action == "id_verified"
    assert row.subject_id == "abc-123"
    assert row.school_id == 1
    assert row.target_table == "students"
    assert row.ip_address == "203.0.113.7"
    # result + extra landen im JSON-Detailfeld
    assert '"result": "valid"' in row.details
    assert '"card_type": "student"' in row.details


def test_record_subject_id_coerced_to_str(session):
    audit.record(session, "test_action", subject_id=42)
    row = session.exec(select(AuditLog)).one()
    assert row.subject_id == "42"


def test_record_no_details_when_empty(session):
    audit.record(session, "test_action")
    row = session.exec(select(AuditLog)).one()
    assert row.details is None


def test_record_commit_false_defers(session):
    audit.record(session, "test_action", commit=False)
    # Ohne commit ist die Zeile in der Session, aber noch nicht persistiert.
    # Ein Rollback muss sie verwerfen.
    session.rollback()
    assert session.exec(select(AuditLog)).first() is None


# ---------------------------------------------------------------------------
# purge_old() — Retention
# ---------------------------------------------------------------------------

def test_purge_old_deletes_only_old_rows(session):
    now = datetime.utcnow()
    old = AuditLog(action="old", timestamp=now - timedelta(days=181))
    edge = AuditLog(action="edge", timestamp=now - timedelta(days=179))
    fresh = AuditLog(action="fresh", timestamp=now)
    session.add(old); session.add(edge); session.add(fresh); session.commit()

    n = audit.purge_old(session)  # Standard: 180 Tage
    assert n == 1

    remaining = {r.action for r in session.exec(select(AuditLog)).all()}
    assert remaining == {"edge", "fresh"}


def test_purge_old_nothing_to_delete(session):
    session.add(AuditLog(action="fresh", timestamp=datetime.utcnow())); session.commit()
    assert audit.purge_old(session) == 0


# ---------------------------------------------------------------------------
# GET /admin/audit-logs — Zugriffsschutz
# ---------------------------------------------------------------------------

def test_viewer_forbidden_for_school_admin(session):
    with pytest.raises(HTTPException) as exc:
        call_viewer(session, school_admin())
    assert exc.value.status_code == 403


def test_viewer_allowed_for_superadmin(session):
    audit.record(session, "id_verified", subject_id="s1", school_id=1)
    page = call_viewer(session, superadmin())
    assert page.total == 1
    assert page.items[0].action == "id_verified"


# ---------------------------------------------------------------------------
# GET /admin/audit-logs — Filter, Pagination, Reihenfolge
# ---------------------------------------------------------------------------

def _seed(session):
    now = datetime.utcnow()
    rows = [
        AuditLog(action="id_verified", subject_id="s1", school_id=1,
                 timestamp=now - timedelta(hours=3)),
        AuditLog(action="cert_downloaded", subject_id="s1", school_id=1,
                 timestamp=now - timedelta(hours=2)),
        AuditLog(action="id_verified", subject_id="s2", school_id=2,
                 timestamp=now - timedelta(hours=1)),
        AuditLog(action="admin_login_failed", subject_id="a@x.de", school_id=None,
                 timestamp=now),
    ]
    for r in rows:
        session.add(r)
    session.commit()


def test_filter_by_action(session):
    _seed(session)
    page = call_viewer(session, superadmin(), action="id_verified")
    assert page.total == 2
    assert all(i.action == "id_verified" for i in page.items)


def test_filter_by_school(session):
    _seed(session)
    page = call_viewer(session, superadmin(), school_id=1)
    assert page.total == 2
    assert all(i.school_id == 1 for i in page.items)


def test_filter_by_subject(session):
    _seed(session)
    page = call_viewer(session, superadmin(), subject_id="s1")
    assert page.total == 2
    assert all(i.subject_id == "s1" for i in page.items)


def test_filter_by_time_window(session):
    _seed(session)
    now = datetime.utcnow()
    page = call_viewer(session, superadmin(), since=now - timedelta(hours=1, minutes=30))
    # nur die letzten beiden (1h alt + jetzt)
    assert page.total == 2


def test_order_is_newest_first(session):
    _seed(session)
    page = call_viewer(session, superadmin())
    ts = [i.timestamp for i in page.items]
    assert ts == sorted(ts, reverse=True)
    assert page.items[0].action == "admin_login_failed"  # der jüngste


def test_pagination_limit_and_offset(session):
    _seed(session)
    first = call_viewer(session, superadmin(), limit=2, offset=0)
    assert first.total == 4          # total ignoriert das Limit
    assert len(first.items) == 2
    second = call_viewer(session, superadmin(), limit=2, offset=2)
    assert len(second.items) == 2
    # keine Überschneidung zwischen den Seiten
    assert {i.id for i in first.items}.isdisjoint({i.id for i in second.items})
