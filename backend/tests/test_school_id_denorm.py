"""
Tests für die automatische Pflege der denormalisierten `school_id`.

Die ORM-Events (models.py) leiten `school_id` beim Flush ab — für JEDEN Schreibpfad
(CSV-Import, Sync, Admin, PWA nutzen alle die ORM). Diese Tests prüfen den Mechanismus
direkt (DB-agnostisch, hier auf SQLite):
- Student-Insert  → school_id = class.school_id
- Klassenwechsel  → school_id wird aktualisiert
- kein Klasse     → school_id = None
- Nicht-Klassen-Update (is_active) lässt school_id unangetastet
- Photo/Push      → erben school_id vom Schüler

Ausführen:
    cd backend && python -m pytest tests/test_school_id_denorm.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine

from models import School, Class, Student, Photo, PushSubscription


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        a = School(name="Schule A"); b = School(name="Schule B")
        s.add(a); s.add(b); s.commit(); s.refresh(a); s.refresh(b)
        ca = Class(school_id=a.id, name="A1", validity_end=date(2027, 7, 31))
        cb = Class(school_id=b.id, name="B1", validity_end=date(2027, 7, 31))
        s.add(ca); s.add(cb); s.commit(); s.refresh(ca); s.refresh(cb)
        yield s, a, b, ca, cb


def _student(s, cls_id):
    st = Student(school_internal_id="1", first_name="Max", last_name="M",
                 dob=date(2005, 1, 1), class_id=cls_id, is_active=True)
    s.add(st); s.commit(); s.refresh(st)
    return st


def test_insert_sets_school_id_from_class(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, ca.id)
    assert st.school_id == a.id


def test_class_change_updates_school_id(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, ca.id)
    assert st.school_id == a.id
    # In eine Klasse der anderen Schule verschieben → school_id folgt automatisch
    st.class_id = cb.id
    s.add(st); s.commit(); s.refresh(st)
    assert st.school_id == b.id


def test_no_class_gives_null_school_id(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, None)
    assert st.school_id is None


def test_non_class_update_keeps_school_id(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, ca.id)
    # z.B. Login/Deaktivierung ändert die Klasse nicht → school_id bleibt
    st.is_active = False
    st.active_session_token_hash = "x" * 64
    s.add(st); s.commit(); s.refresh(st)
    assert st.school_id == a.id


def test_photo_inherits_school_id(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, ca.id)
    photo = Photo(student_id=st.id, blob_data="AAAA", hash="h")
    s.add(photo); s.commit(); s.refresh(photo)
    assert photo.school_id == a.id


def test_push_inherits_school_id(ctx):
    s, a, b, ca, cb = ctx
    st = _student(s, ca.id)
    sub = PushSubscription(student_id=st.id, endpoint="https://x", p256dh="p", auth="au")
    s.add(sub); s.commit(); s.refresh(sub)
    assert sub.school_id == a.id
