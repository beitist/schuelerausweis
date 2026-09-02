"""
Login-Rate-Limits live per Superadmin-UI (DB-Override statt nur Env).

Testet: DB schlägt Env, DB-Werte werden auf Min/Max geklammert, die Superadmin-Endpunkte
persistieren + klammern, und Nicht-Superadmins bekommen 403.

Ausführen:  cd backend && python -m pytest tests/test_ratelimit_settings.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine

from models import User
from services import ratelimit, appsettings
import routes.admin as admin


@pytest.fixture
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _superadmin():
    return User(id=1, email="su@x.de", password_hash="x", role="superadmin", is_active=True)


def _schooladmin():
    return User(id=2, email="a@x.de", password_hash="x", role="school_admin", school_id=1, is_active=True)


def test_db_override_beats_env(session, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ATTEMPTS", "7")
    assert ratelimit.get_limits(session)[0] == 7          # aus Env
    appsettings.set_value(session, ratelimit.RL_ATTEMPTS_KEY, "12")
    assert ratelimit.get_limits(session)[0] == 12         # DB schlägt Env


def test_db_value_is_clamped(session):
    appsettings.set_value(session, ratelimit.RL_ATTEMPTS_KEY, "999")   # über max
    appsettings.set_value(session, ratelimit.RL_WINDOW_KEY, "1")       # unter min
    attempts, window = ratelimit.get_limits(session)
    assert attempts == 20 and window == 5


def test_no_session_uses_env_only(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ATTEMPTS", "9")
    assert ratelimit.get_limits()[0] == 9                 # Rückwärtskompatibel (kein DB-Lookup)


def test_put_endpoint_persists_and_clamps(session):
    su = _superadmin()
    out = admin.set_rate_limit_settings(
        admin.RateLimitSettings(attempts=999, window_minutes=1),
        current_admin=su, session=session)
    assert (out.attempts, out.window_minutes) == (20, 5)   # geklammert VOR dem Speichern
    assert out.is_db_override is True
    got = admin.get_rate_limit_settings(current_admin=su, session=session)
    assert (got.attempts, got.window_minutes) == (20, 5)   # persistiert
    # und der Limiter nutzt den neuen Wert
    assert ratelimit.get_limits(session) == (20, 5)


def test_non_superadmin_forbidden(session):
    for fn in (
        lambda: admin.get_rate_limit_settings(current_admin=_schooladmin(), session=session),
        lambda: admin.set_rate_limit_settings(
            admin.RateLimitSettings(attempts=5, window_minutes=15),
            current_admin=_schooladmin(), session=session),
    ):
        with pytest.raises(HTTPException) as e:
            fn()
        assert e.value.status_code == 403
