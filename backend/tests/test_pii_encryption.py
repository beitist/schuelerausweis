"""
Tests für die PII-Verschlüsselung: first/last_name + dob + Foto at-rest
verschlüsselt, Login/Suche über Blind-Index, Admin-Suche/Sortierung in-memory.

Ausführen:  cd backend && python -m pytest tests/test_pii_encryption.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet
from sqlmodel import SQLModel, Session, create_engine

from models import School, Class, Student, Photo, User
from services.auth import AuthService
import routes.admin as admin


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        sc = School(name="S"); s.add(sc); s.commit(); s.refresh(sc)
        cl = Class(school_id=sc.id, name="A1", validity_end=date(2027, 7, 31))
        s.add(cl); s.commit(); s.refresh(cl)
        yield s, sc.id, cl.id


def _mk(s, cls_id, first, last, dob, pin="123456", sid="1"):
    st = Student(school_internal_id=sid, first_name=first, last_name=last, dob=dob,
                 class_id=cls_id, is_active=True, login_pin=pin, totp_secret="ABC")
    s.add(st); s.commit(); s.refresh(st)
    return st


def test_name_dob_photo_encrypted_at_rest(key, ctx):
    s, sid, cls_id = ctx
    st = _mk(s, cls_id, "Max", "Müller", date(2005, 3, 15))
    s.add(Photo(student_id=st.id, blob_data="AAAAPHOTO", hash="h")); s.commit()
    # ORM: Klartext
    assert st.first_name == "Max" and st.last_name == "Müller" and st.dob == date(2005, 3, 15)
    # RAW: alles verschlüsselt (Fernet-Token)
    raw = s.connection().exec_driver_sql(
        "SELECT first_name, last_name, dob FROM students").fetchone()
    assert all(v.startswith("gAAAAA") for v in raw)
    praw = s.connection().exec_driver_sql("SELECT blob_data FROM photos").fetchone()
    assert praw[0].startswith("gAAAAA")
    # bidx sind gesetzt (nicht der Klartext)
    assert st.last_name_bidx and st.last_name_bidx != "müller"


def test_login_works_via_blind_index(key, ctx):
    s, sid, cls_id = ctx
    _mk(s, cls_id, "Max", "Müller", date(2005, 3, 15))
    token, _t, _se, err = AuthService(s).login_student(
        last_name="MÜLLER", dob=date(2005, 3, 15), school_id=None,
        client_ip="1.2.3.4", login_pin="123456")
    assert err is None and token is not None   # case-insensitiv über bidx


def test_wrong_name_no_match(key, ctx):
    s, sid, cls_id = ctx
    _mk(s, cls_id, "Max", "Müller", date(2005, 3, 15))
    token, _t, _se, err = AuthService(s).login_student(
        last_name="Schmidt", dob=date(2005, 3, 15), school_id=None,
        client_ip="1.2.3.4", login_pin="123456")
    assert token is None and err is not None


def test_admin_list_search_and_sort_in_memory(key, ctx):
    s, sid, cls_id = ctx
    _mk(s, cls_id, "Anna", "Zeder", date(2005, 1, 1), sid="1")
    _mk(s, cls_id, "Max", "Müller", date(2005, 3, 15), sid="2")
    _mk(s, cls_id, "Bea", "Abel", date(2005, 5, 5), sid="3")
    school_admin = User(id=2, email="a@x.de", password_hash="x", role="school_admin",
                        school_id=sid, is_active=True)
    # Sortierung nach Nachname (in-memory): Abel, Müller, Zeder
    res = admin.list_students(school_id=sid, class_id=None, search=None, include_inactive=False,
                              exclude_teachers=True, limit=50, offset=0,
                              current_admin=school_admin, session=s)
    assert [r.last_name for r in res] == ["Abel", "Müller", "Zeder"]
    # Suche (in-memory, entschlüsselt) — Teilstring im Namen
    res2 = admin.list_students(school_id=sid, class_id=None, search="müll", include_inactive=False,
                               exclude_teachers=True, limit=50, offset=0,
                               current_admin=school_admin, session=s)
    assert len(res2) == 1 and res2[0].last_name == "Müller"
