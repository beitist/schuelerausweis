"""
Tests für das DB-basierte Login-Rate-Limiting.

Kernaussage: Der Lockout hängt am KONTO, nicht nur an der IP — IP-Rotation
bringt dem Angreifer also nichts. Ein reines pro-IP-Limit wäre bei einer
6-stelligen PIN der realistischste Brute-Force-Pfad.
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")
os.environ["RATE_LIMIT_ATTEMPTS"] = "3"       # kleiner für schnelle Tests
os.environ["RATE_LIMIT_WINDOW_MINUTES"] = "15"

from datetime import date, datetime, timedelta

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine, select

from models import School, Class, Student, LoginAttempt
from services import ratelimit
from services.auth import AuthService


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Limiter-Grundfunktionen
# ---------------------------------------------------------------------------

def test_get_limits_are_clamped():
    os.environ["RATE_LIMIT_ATTEMPTS"] = "1"     # unter Min → 3
    assert ratelimit.get_limits()[0] == 3
    os.environ["RATE_LIMIT_ATTEMPTS"] = "9999"  # über Max → 20
    assert ratelimit.get_limits()[0] == 20
    os.environ["RATE_LIMIT_WINDOW_MINUTES"] = "1"   # unter Min → 5
    assert ratelimit.get_limits()[1] == 5
    # zurücksetzen für die restlichen Tests
    os.environ["RATE_LIMIT_ATTEMPTS"] = "3"
    os.environ["RATE_LIMIT_WINDOW_MINUTES"] = "15"


def test_blocks_after_n_and_clear_resets(session):
    ident = "student-acct:1:mustermann:2005-03-15"
    for _ in range(3):
        assert ratelimit.is_blocked(session, ident)[0] is False
        ratelimit.record_failure(session, ident)
    blocked, secs = ratelimit.is_blocked(session, ident)
    assert blocked is True and secs > 0
    ratelimit.clear(session, ident)
    assert ratelimit.is_blocked(session, ident)[0] is False


def test_window_expiry_old_attempts_dont_count(session):
    ident = "x"
    for _ in range(5):  # 5 alte Versuche, aber außerhalb des 15-min-Fensters
        session.add(LoginAttempt(identifier=ident, created_at=datetime.utcnow() - timedelta(minutes=30)))
    session.commit()
    assert ratelimit.is_blocked(session, ident)[0] is False


def test_purge_old(session):
    session.add(LoginAttempt(identifier="z", created_at=datetime.utcnow() - timedelta(days=2)))
    session.add(LoginAttempt(identifier="z", created_at=datetime.utcnow()))
    session.commit()
    removed = ratelimit.purge_old(session, older_than_minutes=24 * 60)
    assert removed == 1
    assert len(session.exec(select(LoginAttempt)).all()) == 1


# ---------------------------------------------------------------------------
# Kern-Test: IP-Rotation besiegt den Konto-Lockout NICHT
# ---------------------------------------------------------------------------

def _make_student(session):
    school = School(name="S"); session.add(school); session.commit(); session.refresh(school)
    cls = Class(school_id=school.id, name="E1A"); session.add(cls); session.commit(); session.refresh(cls)
    st = Student(school_internal_id="1", first_name="Max", last_name="Mustermann",
                 dob=date(2005, 3, 15), class_id=cls.id, login_pin="482751", is_active=True)
    session.add(st); session.commit()
    return school


def test_pin_bruteforce_is_locked_despite_ip_rotation(session):
    school = _make_student(session)
    auth = AuthService(session)

    # 3 falsche PIN-Versuche, jeder von einer ANDEREN IP
    for i in range(3):
        token, _totp, _sess, err = auth.login_student(
            last_name="Mustermann", dob=date(2005, 3, 15), school_id=school.id,
            client_ip=f"9.9.9.{i}", login_pin="000000",
        )
        assert token is None

    # 4. Versuch mit KORREKTER PIN von einer weiteren frischen IP → trotzdem gesperrt
    token, _totp, _sess, err = auth.login_student(
        last_name="Mustermann", dob=date(2005, 3, 15), school_id=school.id,
        client_ip="9.9.9.250", login_pin="482751",
    )
    assert token is None
    assert "Zu viele" in err  # Konto-Lockout greift, IP-Rotation wirkungslos


def test_successful_login_clears_bucket(session):
    school = _make_student(session)
    auth = AuthService(session)

    # 2 Fehlversuche (unter dem Limit von 3)
    for _ in range(2):
        auth.login_student(last_name="Mustermann", dob=date(2005, 3, 15),
                           school_id=school.id, client_ip="1.1.1.1", login_pin="000000")

    # korrekter Login → Erfolg + Bucket geleert
    token, totp, sess_tok, err = auth.login_student(
        last_name="Mustermann", dob=date(2005, 3, 15), school_id=school.id,
        client_ip="1.1.1.1", login_pin="482751",
    )
    assert err is None and token and sess_tok
    acct = "student-acct:%d:mustermann:2005-03-15" % school.id
    assert ratelimit.remaining_attempts(session, acct) == 3  # zurückgesetzt


def test_failed_student_login_returns_clean_error_not_crash(session):
    """Regression: Fehlerpfad gibt ein 4-Tupel zurück (kein ValueError beim Entpacken)."""
    school = _make_student(session)
    auth = AuthService(session)
    token, totp, sess_tok, err = auth.login_student(
        last_name="Mustermann", dob=date(2005, 3, 15), school_id=school.id,
        client_ip="2.2.2.2", login_pin="999999",
    )
    assert token is None and err and "Versuche" in err
