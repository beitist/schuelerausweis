"""
Tests für die Schüler-Session (Cookie-Auth + Ausweis-Ablauf + „da & aktiv").

Kernaussagen:
- student_session_expiry = Ausweis-Ablauf (Klassen-validity_end / Override), Fallback Schuljahresende.
- Login setzt HttpOnly-Cookies (student_token + session_token).
- get_current_student liest Cookie ODER Header; lehnt fehlend/abgemeldet/inaktiv **generisch** ab
  (kein Status-Leak), Highlander „kicked_out" bleibt gesondert.

Ausführen:
    cd backend && python -m pytest tests/test_student_session.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date, datetime

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine
from fastapi import HTTPException, Response

from models import School, Class, Student
from services import auth as auth_svc
from services.auth import (
    AuthService, create_student_token, generate_session_token, hash_session_token,
    student_session_expiry, end_of_school_year,
)
import routes.auth as auth_routes
import routes.pwa as pwa


class _FakeClient:
    host = "203.0.113.9"


class FakeRequest:
    """Minimaler Request: get_current_student nutzt .cookies.get + .headers.get."""
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.client = _FakeClient()
        self.method = "GET"


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        school = School(name="Schule A")
        s.add(school); s.commit(); s.refresh(school)
        cls = Class(school_id=school.id, name="A1", validity_end=date(2027, 7, 31))
        s.add(cls); s.commit(); s.refresh(cls)
        student = Student(school_internal_id="1", first_name="Max", last_name="M",
                          dob=date(2005, 1, 1), class_id=cls.id, is_active=True,
                          login_pin="123456", totp_secret="ABC")
        s.add(student); s.commit(); s.refresh(student)
        yield s, school, cls, student


def _logged_in_cookies(s, student, school_id):
    """Erzeugt gültige Cookie-Werte (Token + Session) und speichert den Highlander-Hash."""
    token = create_student_token(student, school_id, expires_at=student_session_expiry(student, s))
    session_token = generate_session_token()
    student.active_session_token_hash = hash_session_token(session_token)
    s.add(student); s.commit()
    return {"student_token": token, "session_token": session_token}


# ---------------------------------------------------------------------------
# Ablauf-Berechnung
# ---------------------------------------------------------------------------

def test_end_of_school_year_before_july():
    assert end_of_school_year(date(2026, 3, 1)) == date(2026, 7, 31)


def test_end_of_school_year_after_july():
    assert end_of_school_year(date(2026, 9, 1)) == date(2027, 7, 31)


def test_session_expiry_uses_class_validity(ctx):
    s, school, cls, student = ctx
    exp = student_session_expiry(student, s)
    assert exp.date() == date(2027, 7, 31)


def test_session_expiry_uses_override(ctx):
    s, school, cls, student = ctx
    student.validity_end_override = date(2026, 12, 31)
    s.add(student); s.commit()
    assert student_session_expiry(student, s).date() == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Cookie-Setzen
# ---------------------------------------------------------------------------

def test_set_student_cookies_sets_both_httponly(ctx):
    s, school, cls, student = ctx
    token = create_student_token(student, school.id, expires_at=student_session_expiry(student, s))
    resp = Response()
    auth_routes._set_student_cookies(resp, token, "sess-abc")
    raw = b"\n".join(v for k, v in resp.raw_headers if k == b"set-cookie").decode()
    assert "student_token=" in raw
    assert "session_token=" in raw
    assert "HttpOnly" in raw


# ---------------------------------------------------------------------------
# get_current_student
# ---------------------------------------------------------------------------

def test_cookie_auth_returns_student(ctx):
    s, school, cls, student = ctx
    cookies = _logged_in_cookies(s, student, school.id)
    got = pwa.get_current_student(request=FakeRequest(cookies=cookies), credentials=None, session=s)
    assert str(got.id) == str(student.id)


def test_header_fallback_still_works(ctx):
    s, school, cls, student = ctx
    cookies = _logged_in_cookies(s, student, school.id)

    class Creds:
        credentials = cookies["student_token"]

    req = FakeRequest(headers={"X-Session-Token": cookies["session_token"]})
    got = pwa.get_current_student(request=req, credentials=Creds(), session=s)
    assert str(got.id) == str(student.id)


def test_no_token_is_401(ctx):
    s, *_ = ctx
    with pytest.raises(HTTPException) as exc:
        pwa.get_current_student(request=FakeRequest(), credentials=None, session=s)
    assert exc.value.status_code == 401


def test_inactive_student_rejected_generically(ctx):
    s, school, cls, student = ctx
    cookies = _logged_in_cookies(s, student, school.id)
    student.is_active = False
    s.add(student); s.commit()
    with pytest.raises(HTTPException) as exc:
        pwa.get_current_student(request=FakeRequest(cookies=cookies), credentials=None, session=s)
    assert exc.value.status_code == 401
    # KEIN Status-Leak: generische Meldung, nicht "Konto deaktiviert"
    assert exc.value.detail == "Nicht authentifiziert"


def test_deleted_student_rejected_generically(ctx):
    s, school, cls, student = ctx
    cookies = _logged_in_cookies(s, student, school.id)
    student.deleted_at = datetime.utcnow()
    s.add(student); s.commit()
    with pytest.raises(HTTPException) as exc:
        pwa.get_current_student(request=FakeRequest(cookies=cookies), credentials=None, session=s)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Nicht authentifiziert"


def test_kicked_out_is_special(ctx):
    s, school, cls, student = ctx
    _logged_in_cookies(s, student, school.id)
    # Falscher Session-Token (anderes Gerät hat sich eingeloggt)
    token = create_student_token(student, school.id, expires_at=student_session_expiry(student, s))
    bad = {"student_token": token, "session_token": "voellig-falsch"}
    with pytest.raises(HTTPException) as exc:
        pwa.get_current_student(request=FakeRequest(cookies=bad), credentials=None, session=s)
    assert exc.value.status_code == 401
    assert exc.value.headers.get("X-Kicked-Out") == "true"
