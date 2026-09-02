"""
Voll-Sync für Lehrer (Tool = Source of Truth): Lehrer sind Student-Datensätze in der
Klasse "Lehrerkollegium" und werden über dieselbe Sync-API wie Schüler verwaltet.

Ausführen:  cd backend && python -m pytest tests/test_sync_teachers.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from models import School, Class, Student
import routes.sync as sync
from routes.sync import (
    SyncNewRequest, NewStudentRequest, SyncChangeRequest, ChangeStudentRequest,
    SyncSuspendRequest, TEACHER_CLASS_NAME,
)


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        sc = School(name="Testschule"); s.add(sc); s.commit(); s.refresh(sc)
        yield s, sc


def _teacher(sid="T-1", first="Erika", last="Muster", dob="1980-05-01"):
    return NewStudentRequest(school_internal_id=sid, first_name=first, last_name=last,
                             dob=dob, class_name=TEACHER_CLASS_NAME)


def _get(s, sid):
    return s.exec(select(Student).where(Student.school_internal_id == sid)).one()


def test_teacher_created_with_teacher_validity(ctx):
    s, sc = ctx
    resp = sync.sync_new_students(SyncNewRequest(students=[_teacher()]), school=sc, session=s)
    assert resp.results[0].status == "created"
    t = _get(s, "T-1")
    cls = s.get(Class, t.class_id)
    assert cls.name == TEACHER_CLASS_NAME
    today = date.today()
    assert cls.validity_end == date(today.year + 5, 7, 31)   # Lehrer-Gültigkeit, nicht Schuljahresende


def test_teacher_appears_in_manifest(ctx):
    s, sc = ctx
    sync.sync_new_students(SyncNewRequest(students=[_teacher()]), school=sc, session=s)
    man = sync.get_manifest(school=sc, session=s)
    by_id = {m.school_internal_id: m for m in man.students}
    assert "T-1" in by_id and by_id["T-1"].class_name == TEACHER_CLASS_NAME


def test_teacher_change_and_suspend(ctx):
    s, sc = ctx
    sync.sync_new_students(SyncNewRequest(students=[_teacher(last="Alt")]), school=sc, session=s)
    ch = sync.sync_change_students(
        SyncChangeRequest(students=[ChangeStudentRequest(school_internal_id="T-1", last_name="Neu")]),
        school=sc, session=s)
    assert ch.results[0].status == "updated"
    assert _get(s, "T-1").last_name == "Neu"

    su = sync.sync_suspend_students(SyncSuspendRequest(school_internal_ids=["T-1"]), school=sc, session=s)
    assert su.results[0].status == "suspended"
    assert _get(s, "T-1").is_active is False


def test_manifest_excludes_suspended_still_returns_active_teacher(ctx):
    # Regression: ein aktiver Lehrer + ein Schüler tauchen beide im Manifest auf.
    s, sc = ctx
    sync.sync_new_students(SyncNewRequest(students=[
        _teacher(sid="T-1"),
        NewStudentRequest(school_internal_id="S-1", first_name="Max", last_name="Schüler",
                          dob="2010-03-03", class_name="5a"),
    ]), school=sc, session=s)
    man = sync.get_manifest(school=sc, session=s)
    ids = {m.school_internal_id for m in man.students}
    assert {"T-1", "S-1"} <= ids
