"""
Regressionstest für die DSGVO-Datenbegrenzung (services/cleanup.py).

Abgesichertes Fehlerbild: Ein Bulk-Delete auf `students` läuft in eine
ForeignKeyViolation (photos/push_subscriptions haben FKs ohne ON DELETE
CASCADE). Würde dieser Fehler verschluckt, würden Schüler MIT Foto — also
faktisch alle — nie endgültig gelöscht.

Diese Tests laufen mit AKTIVIERTEM FK-Enforcement (PRAGMA foreign_keys=ON), damit
SQLite sich wie Postgres verhält — sonst wäre das Fehlerbild im Test unsichtbar.

Ausführen:  cd backend && python -m pytest tests/test_dsgvo_cleanup.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import datetime as dt
from datetime import datetime, timedelta

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, select

from models import Student, Photo, PushSubscription
from services.cleanup import purge_expired_students


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    # KRITISCH: FK-Enforcement an — sonst verhält sich SQLite nicht wie Postgres
    # und das FK-Fehlerbild wäre im Test unsichtbar.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _student(s, name, *, deleted_weeks_ago=None, active=True, with_photo=False, with_push=False):
    st = Student(
        school_internal_id=name, first_name=name, last_name="T",
        dob=dt.date(2005, 1, 1), is_active=active,
        deleted_at=(datetime.utcnow() - timedelta(weeks=deleted_weeks_ago))
        if deleted_weeks_ago is not None else None,
    )
    s.add(st); s.commit(); s.refresh(st)
    if with_photo:
        s.add(Photo(student_id=st.id, blob_data="img", hash="h"))
    if with_push:
        s.add(PushSubscription(student_id=st.id, endpoint="e", p256dh="p", auth="a"))
    s.commit()
    return st


def test_purges_expired_student_with_photo_and_push(session):
    """Kernfall: Schüler MIT Foto/Push wird trotz FK-Constraints endgültig gelöscht."""
    _student(session, "Alt", deleted_weeks_ago=8, active=False, with_photo=True, with_push=True)

    result = purge_expired_students(session)

    assert result == {"students": 1, "photos": 1, "subscriptions": 1}
    assert session.exec(select(Student)).all() == []
    assert session.exec(select(Photo)).all() == []
    assert session.exec(select(PushSubscription)).all() == []


def test_keeps_recently_softdeleted_and_active(session):
    """Frisch soft-gelöschte (< 4 Wochen) und aktive Schüler bleiben unangetastet."""
    _student(session, "Alt", deleted_weeks_ago=8, active=False, with_photo=True)
    _student(session, "Frisch", deleted_weeks_ago=1, active=False, with_photo=True)  # zu jung
    _student(session, "Aktiv", active=True, with_photo=True)

    result = purge_expired_students(session)

    assert result["students"] == 1
    remaining = {s.first_name for s in session.exec(select(Student)).all()}
    assert remaining == {"Frisch", "Aktiv"}
    # Die Fotos der verbliebenen Schüler dürfen NICHT gelöscht worden sein
    assert len(session.exec(select(Photo)).all()) == 2


def test_noop_when_nothing_expired(session):
    _student(session, "Aktiv", active=True, with_photo=True)
    assert purge_expired_students(session) == {"students": 0, "photos": 0, "subscriptions": 0}
    assert len(session.exec(select(Student)).all()) == 1


def test_errors_are_not_swallowed(session):
    """
    Ein DSGVO-Fehler MUSS laut sein.

    purge_expired_students() darf NICHT still scheitern — es muss werfen,
    damit der Aufrufer (und APScheduler) den Fehler sieht.
    """
    from sqlalchemy import text

    _student(session, "Alt", deleted_weeks_ago=8, active=False, with_photo=True)

    # DB-Fehler erzwingen: abhängige Tabelle wegziehen
    session.execute(text("DROP TABLE photos"))

    with pytest.raises(Exception):
        purge_expired_students(session)


# ============================================================================
# run_full_cleanup() — der nächtliche Sammel-Job (Purge + Heartbeat + Single-Flight)
# ============================================================================

@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(eng)
    return eng


def _seed_expired(eng, name="Alt", weeks=8):
    with Session(eng) as s:
        st = Student(school_internal_id=name, first_name=name, last_name="T",
                     dob=dt.date(2005, 1, 1), is_active=False,
                     deleted_at=datetime.utcnow() - timedelta(weeks=weeks))
        s.add(st); s.commit(); s.refresh(st)
        s.add(Photo(student_id=st.id, blob_data="img", hash="h")); s.commit()


def test_run_full_cleanup_purges_and_writes_heartbeat(engine):
    from services.cleanup import run_full_cleanup
    from models import AuditLog
    _seed_expired(engine)

    res = run_full_cleanup(engine)

    assert res["students"] == 1
    with Session(engine) as s:
        assert s.exec(select(Student)).all() == []
        beats = s.exec(select(AuditLog).where(AuditLog.action == "system_cleanup_ran")).all()
        assert len(beats) == 1   # Heartbeat fürs Statusboard geschrieben


def test_run_full_cleanup_idempotent(engine):
    from services.cleanup import run_full_cleanup
    run_full_cleanup(engine)                 # nichts zu löschen
    assert run_full_cleanup(engine)["students"] == 0


def test_advisory_lock_prevents_double_run(pg_engine, pg_session):
    """Postgres: hält ein Worker den Advisory-Lock, überspringt der zweite Lauf sauber."""
    from services.cleanup import run_full_cleanup, CLEANUP_LOCK_KEY
    from sqlalchemy import text as _t

    st = Student(school_internal_id="AltPG", first_name="Alt", last_name="T",
                 dob=dt.date(2005, 1, 1), is_active=False,
                 deleted_at=datetime.utcnow() - timedelta(weeks=8))
    pg_session.add(st); pg_session.commit()

    holder = pg_engine.connect()             # simuliert "anderer Worker hält den Lock"
    holder.execute(_t("SELECT pg_advisory_lock(:k)"), {"k": CLEANUP_LOCK_KEY})
    holder.commit()
    try:
        assert run_full_cleanup(pg_engine) == {"skipped": True}
        # Schüler bleibt — der Job wurde übersprungen, nicht ausgeführt
        assert pg_session.exec(
            select(Student).where(Student.school_internal_id == "AltPG")
        ).all() != []
    finally:
        holder.execute(_t("SELECT pg_advisory_unlock(:k)"), {"k": CLEANUP_LOCK_KEY})
        holder.commit()
        holder.close()

    # Ohne gehaltenen Lock läuft er durch und purged
    assert run_full_cleanup(pg_engine)["students"] == 1
