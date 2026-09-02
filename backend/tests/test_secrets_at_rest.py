"""
Tests für die Feld-Verschlüsselung at-rest (login_pin/totp_secret).

- login_pin/totp_secret liegen bei gesetztem SECRETS_KEY VERSCHLÜSSELT in der DB,
  über die ORM aber im Klartext (transparent).
- Der Schüler-Login funktioniert weiterhin (PIN-Match läuft in Python).
- Lesen ist tolerant gegenüber Legacy-Klartext (vor der Migration).

Ausführen:  cd backend && python -m pytest tests/test_secrets_at_rest.py -q
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
from sqlalchemy import text

from models import School, Class, Student
from services.auth import AuthService


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
        yield s, cl.id


def _student(s, cls_id, pin="123456", totp="ABC234"):
    st = Student(school_internal_id="1", first_name="Max", last_name="Müller",
                 dob=date(2005, 3, 15), class_id=cls_id, is_active=True,
                 login_pin=pin, totp_secret=totp)
    s.add(st); s.commit(); s.refresh(st)
    return st


def test_encrypted_at_rest_plaintext_via_orm(key, ctx):
    s, cls_id = ctx
    st = _student(s, cls_id)
    # ORM: Klartext
    assert st.login_pin == "123456" and st.totp_secret == "ABC234"
    # Roh: verschlüsselt (Fernet-Token)
    raw = s.connection().exec_driver_sql(
        "SELECT login_pin, totp_secret FROM students").fetchone()
    assert raw[0].startswith("gAAAAA") and raw[1].startswith("gAAAAA")
    assert raw[0] != "123456"


def test_login_still_works_with_encryption(key, ctx):
    s, cls_id = ctx
    _student(s, cls_id)
    token, _totp, _sess, error = AuthService(s).login_student(
        last_name="müller", dob=date(2005, 3, 15), school_id=None,
        client_ip="1.2.3.4", login_pin="123456")
    assert error is None and token is not None


def test_wrong_pin_fails_with_encryption(key, ctx):
    s, cls_id = ctx
    _student(s, cls_id)
    token, _t, _se, error = AuthService(s).login_student(
        last_name="müller", dob=date(2005, 3, 15), school_id=None,
        client_ip="1.2.3.4", login_pin="000000")
    assert token is None and error is not None


def test_tolerant_read_of_legacy_plaintext(key, ctx):
    s, cls_id = ctx
    st = _student(s, cls_id)
    # Legacy simulieren: Klartext direkt in die DB schreiben (umgeht die Verschlüsselung).
    # Nur ein Schüler vorhanden → kein WHERE nötig (vermeidet UUID-Format-Fallstricke).
    s.connection().exec_driver_sql("UPDATE students SET login_pin = '654321'")
    s.commit(); s.expire_all()
    # tolerantes Lesen: Klartext wird unverändert zurückgegeben (kein Decrypt-Crash)
    st2 = s.get(Student, st.id)
    assert st2.login_pin == "654321"
