"""
Tests für die Admin-Session-Revocation über den Passwortwechsel.

Kernaussage: Ein Admin-Token, das VOR der letzten Passwortänderung ausgestellt wurde,
wird abgelehnt. Damit wirft ein Passwort-Reset (z.B. bei Verdacht) bestehende
Admin-Sitzungen raus — die sonst stateless Admin-JWTs sind so widerrufbar.

Ausführen:
    cd backend && python -m pytest tests/test_admin_revocation.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import datetime, timedelta

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine

from models import User
from services.auth import AuthService, create_admin_token, hash_password


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_admin(s, pw="geheim123", changed_at="now"):
    if changed_at == "now":
        changed_at = datetime.utcnow()
    u = User(email="a@x.de", password_hash=hash_password(pw), role="superadmin",
             school_id=None, is_active=True, password_changed_at=changed_at)
    s.add(u); s.commit(); s.refresh(u)
    return u


def test_fresh_token_is_valid(session):
    u = make_admin(session)
    token = create_admin_token(u)
    user, error = AuthService(session).validate_admin_token(token)
    assert error is None
    assert user is not None and user.id == u.id


def test_token_issued_before_password_change_is_rejected(session):
    u = make_admin(session)
    token = create_admin_token(u)  # iat ≈ jetzt

    # Passwort später geändert → alle vorher ausgestellten Tokens sind ungültig.
    u.password_changed_at = datetime.utcnow() + timedelta(minutes=5)
    session.add(u); session.commit()

    user, error = AuthService(session).validate_admin_token(token)
    assert user is None
    assert error is not None and "Passwort" in error


def test_token_issued_after_password_change_is_valid(session):
    u = make_admin(session)
    # Passwortänderung liegt in der Vergangenheit → frisch ausgestelltes Token ok.
    u.password_changed_at = datetime.utcnow() - timedelta(minutes=5)
    session.add(u); session.commit()

    token = create_admin_token(u)
    user, error = AuthService(session).validate_admin_token(token)
    assert error is None
    assert user is not None


def test_null_password_changed_at_is_backward_compatible(session):
    # Altbestand ohne Wert → keine Ablehnung (bis das Passwort einmal wechselt).
    u = make_admin(session, changed_at=None)
    token = create_admin_token(u)
    user, error = AuthService(session).validate_admin_token(token)
    assert error is None
    assert user is not None


def test_deactivated_still_rejected(session):
    # is_active-Check bleibt vorrangig unverändert bestehen.
    u = make_admin(session)
    token = create_admin_token(u)
    u.is_active = False
    session.add(u); session.commit()
    user, error = AuthService(session).validate_admin_token(token)
    assert user is None
    assert error == "Konto deaktiviert."
