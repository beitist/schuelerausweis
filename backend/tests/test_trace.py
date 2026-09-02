"""
Tests für die gezielte Detail-Nachverfolgung ("Trace") im Log-Viewer.

Kernaussagen:
- active_trace() matcht korrekt (ganze Schule vs. einzelne ID, abgelaufen, widerrufen).
- record_card_load() schreibt NUR bei aktivem Trace (sonst bleibt der Ausweis-Load unsichtbar).
- ip_if_traced() gibt die IP nur bei aktivem Trace zurück.
- Die Trace-Endpoints sind superadmin-only, begrenzen die Dauer und auditieren sich selbst.

Ausführen:
    cd backend && python -m pytest tests/test_trace.py -q
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

from models import AccessTrace, AuditLog, School, User
from services import audit
import routes.admin as admin


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        # Zwei Schulen anlegen (IDs 1 und 2)
        s.add(School(name="Schule A")); s.add(School(name="Schule B"))
        s.commit()
        yield s


def superadmin():
    return User(id=1, email="super@x.de", password_hash="x", role="superadmin",
                school_id=None, is_active=True)


def school_admin(school_id=1):
    return User(id=2, email="admin@x.de", password_hash="x", role="school_admin",
                school_id=school_id, is_active=True)


def mk_trace(session, *, school_id=1, subject_id=None, hours=48, revoked=False, expired=False):
    now = datetime.utcnow()
    exp = now - timedelta(hours=1) if expired else now + timedelta(hours=hours)
    t = AccessTrace(school_id=school_id, subject_id=subject_id, enabled_by=1,
                    created_at=now, expires_at=exp,
                    revoked_at=(now if revoked else None))
    session.add(t); session.commit(); session.refresh(t)
    return t


# ---------------------------------------------------------------------------
# active_trace()
# ---------------------------------------------------------------------------

def test_whole_school_trace_matches_any_subject(session):
    mk_trace(session, school_id=1, subject_id=None)
    assert audit.active_trace(session, school_id=1, subject_id="anyone") is True
    assert audit.active_trace(session, school_id=1, subject_id=None) is True


def test_specific_subject_trace_matches_only_that_id(session):
    mk_trace(session, school_id=1, subject_id="stud-1")
    assert audit.active_trace(session, school_id=1, subject_id="stud-1") is True
    assert audit.active_trace(session, school_id=1, subject_id="stud-2") is False


def test_wrong_school_does_not_match(session):
    mk_trace(session, school_id=1, subject_id=None)
    assert audit.active_trace(session, school_id=2, subject_id="stud-1") is False


def test_expired_trace_inactive(session):
    mk_trace(session, school_id=1, subject_id=None, expired=True)
    assert audit.active_trace(session, school_id=1, subject_id="x") is False


def test_revoked_trace_inactive(session):
    mk_trace(session, school_id=1, subject_id=None, revoked=True)
    assert audit.active_trace(session, school_id=1, subject_id="x") is False


def test_none_school_never_matches(session):
    mk_trace(session, school_id=1, subject_id=None)
    assert audit.active_trace(session, school_id=None, subject_id="x") is False


# ---------------------------------------------------------------------------
# record_card_load() / ip_if_traced()
# ---------------------------------------------------------------------------

def test_card_load_not_logged_without_trace(session):
    wrote = audit.record_card_load(session, "id_card_loaded",
                                   school_id=1, subject_id="stud-1", ip="1.2.3.4")
    assert wrote is False
    assert session.exec(select(AuditLog)).first() is None


def test_card_load_logged_with_trace_includes_ip(session):
    mk_trace(session, school_id=1, subject_id="stud-1")
    wrote = audit.record_card_load(session, "id_card_loaded",
                                   school_id=1, subject_id="stud-1", ip="1.2.3.4",
                                   extra={"card_type": "student"})
    assert wrote is True
    row = session.exec(select(AuditLog)).one()
    assert row.action == "id_card_loaded"
    assert row.ip_address == "1.2.3.4"
    assert '"trace": true' in row.details


def test_ip_if_traced_returns_none_without_trace(session):
    assert audit.ip_if_traced(session, school_id=1, subject_id="s", ip="9.9.9.9") is None


def test_ip_if_traced_returns_ip_with_trace(session):
    mk_trace(session, school_id=1, subject_id=None)
    assert audit.ip_if_traced(session, school_id=1, subject_id="s", ip="9.9.9.9") == "9.9.9.9"


# ---------------------------------------------------------------------------
# Trace-Endpoints (superadmin-only, Dauerbegrenzung, Self-Audit)
# ---------------------------------------------------------------------------

def test_create_trace_forbidden_for_school_admin(session):
    with pytest.raises(HTTPException) as exc:
        admin.create_trace(admin.TraceCreate(school_id=1),
                           current_admin=school_admin(), session=session)
    assert exc.value.status_code == 403


def test_create_trace_unknown_school_404(session):
    with pytest.raises(HTTPException) as exc:
        admin.create_trace(admin.TraceCreate(school_id=999),
                           current_admin=superadmin(), session=session)
    assert exc.value.status_code == 404


def test_create_trace_clamps_hours_and_audits(session):
    item = admin.create_trace(
        admin.TraceCreate(school_id=1, subject_id="stud-1", reason="Verdacht", hours=9999),
        current_admin=superadmin(), session=session)
    assert item.active is True
    assert item.school_name == "Schule A"
    # Dauer auf 7 Tage (168h) begrenzt
    span_h = (item.expires_at - item.created_at).total_seconds() / 3600
    assert round(span_h) == admin.TRACE_MAX_HOURS
    # Self-Audit: trace_enabled geschrieben
    log = session.exec(select(AuditLog).where(AuditLog.action == "trace_enabled")).one()
    assert log.subject_id == "stud-1"
    assert log.school_id == 1


def test_create_trace_default_hours(session):
    item = admin.create_trace(admin.TraceCreate(school_id=1),
                              current_admin=superadmin(), session=session)
    span_h = (item.expires_at - item.created_at).total_seconds() / 3600
    assert round(span_h) == admin.TRACE_DEFAULT_HOURS
    assert item.subject_id is None  # ganze Schule


def test_list_traces_active_only(session):
    mk_trace(session, school_id=1, subject_id=None)                 # aktiv
    mk_trace(session, school_id=1, subject_id="x", expired=True)     # abgelaufen
    active = admin.list_traces(active_only=True, current_admin=superadmin(), session=session)
    assert len(active) == 1
    all_rows = admin.list_traces(active_only=False, current_admin=superadmin(), session=session)
    assert len(all_rows) == 2


def test_list_traces_forbidden_for_school_admin(session):
    with pytest.raises(HTTPException) as exc:
        admin.list_traces(active_only=False, current_admin=school_admin(), session=session)
    assert exc.value.status_code == 403


def test_revoke_trace_sets_revoked_and_audits(session):
    t = mk_trace(session, school_id=1, subject_id="stud-1")
    res = admin.revoke_trace(t.id, current_admin=superadmin(), session=session)
    assert res["success"] is True
    session.refresh(t)
    assert t.revoked_at is not None
    assert audit.active_trace(session, school_id=1, subject_id="stud-1") is False
    log = session.exec(select(AuditLog).where(AuditLog.action == "trace_revoked")).one()
    assert log.school_id == 1


def test_revoke_unknown_trace_404(session):
    with pytest.raises(HTTPException) as exc:
        admin.revoke_trace(12345, current_admin=superadmin(), session=session)
    assert exc.value.status_code == 404
