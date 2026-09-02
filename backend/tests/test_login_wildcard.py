"""
Regressionstest: Der Schüler-Login darf LIKE-Wildcards im Nachnamen NICHT als
Wildcards interpretieren — sonst matcht `last_name="%"` jeden Schüler und das
Konto-Rate-Limit ist ausgehebelt.

Ausführen:  cd backend && python -m pytest tests/test_login_wildcard.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine
from models import School, Class, Student
from services.auth import AuthService


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        sc = School(name="S"); s.add(sc); s.commit(); s.refresh(sc)
        cl = Class(school_id=sc.id, name="A1", validity_end=date(2027, 7, 31))
        s.add(cl); s.commit(); s.refresh(cl)
        st = Student(school_internal_id="1", first_name="Max", last_name="Müller",
                     dob=date(2005, 3, 15), class_id=cl.id, is_active=True, login_pin="123456")
        s.add(st); s.commit()
        yield s


def _login(s, last_name):
    # school_id=None → globaler Suchpfad (der Angriff lässt school_id weg)
    return AuthService(s).login_student(
        last_name=last_name, dob=date(2005, 3, 15), school_id=None,
        client_ip="1.2.3.4", login_pin="123456"
    )


def test_percent_wildcard_does_not_match_anyone(ctx):
    token, _totp, _sess, error = _login(ctx, "%")
    assert token is None
    assert error is not None       # Login fehlgeschlagen, KEIN Treffer


def test_underscore_wildcard_does_not_match(ctx):
    token, _totp, _sess, error = _login(ctx, "M_ller")
    assert token is None
    assert error is not None


def test_correct_name_still_logs_in_case_insensitive(ctx):
    # Groß-/Kleinschreibung ist beim Login egal (case-insensitiver Exakt-Vergleich)
    token, _totp, _sess, error = _login(ctx, "müller")
    assert error is None
    assert token is not None
