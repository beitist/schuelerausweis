"""
Tests für die Sync-IP-Allowlist.

Kernaussagen:
- normalize_cidr/ip_in_entries/is_sync_allowed verhalten sich fail-closed und
  verstehen Einzel-IPs + CIDR, v4 und v6.
- validate_api_key setzt die Allowlist durch: AUS → durchlassen, AN → nur erlaubte
  IPs; eine blockierte IP erzeugt 403 + Audit `sync_ip_blocked`.
- Die Verwaltungs-Endpoints sind auf die Key-Manager-Stufe beschränkt (school_admin
  eigene Schule / superadmin), validieren Eingaben und verhindern das Selbst-Aussperren.

Ausführen:
    cd backend && python -m pytest tests/test_ipallow.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import hashlib

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine, select
from fastapi import HTTPException

from models import School, User, AuditLog, SyncIpAllowlistEntry
from services import ipallow
import routes.sync as sync
import routes.schools as schools


# ---------------------------------------------------------------------------
# Fakes / Fixtures
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    """Minimaler Request-Ersatz: get_client_ip() nutzt .headers.get() + .client.host."""
    def __init__(self, ip="203.0.113.7", xff=None):
        self.headers = {"X-Forwarded-For": xff} if xff else {}
        self.client = _FakeClient(ip)
        self.method = "GET"


API_KEY = "hgn_sk_" + "a" * 64
KEY_HASH = hashlib.sha256(API_KEY.encode()).hexdigest()


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        school = School(name="Schule A", api_key_hash=KEY_HASH)
        s.add(school); s.commit(); s.refresh(school)
        yield s


def school_admin(school_id=1):
    return User(id=2, email="admin@x.de", password_hash="x", role="school_admin",
                school_id=school_id, is_active=True)


def teacher(school_id=1):
    return User(id=3, email="t@x.de", password_hash="x", role="teacher",
                school_id=school_id, is_active=True)


def superadmin():
    return User(id=1, email="super@x.de", password_hash="x", role="superadmin",
                school_id=None, is_active=True)


# ---------------------------------------------------------------------------
# services/ipallow.py
# ---------------------------------------------------------------------------

def test_normalize_single_ip_v4():
    assert ipallow.normalize_cidr("203.0.113.7") == "203.0.113.7/32"


def test_normalize_cidr_passthrough():
    assert ipallow.normalize_cidr("203.0.113.0/24") == "203.0.113.0/24"


def test_normalize_host_bits_set_snaps_to_network():
    assert ipallow.normalize_cidr("203.0.113.5/24") == "203.0.113.0/24"


def test_normalize_ipv6():
    assert ipallow.normalize_cidr("2001:db8::1") == "2001:db8::1/128"


def test_normalize_invalid_raises():
    for bad in ["", "not-an-ip", "999.999.1.1", "203.0.113.0/99"]:
        with pytest.raises(ValueError):
            ipallow.normalize_cidr(bad)


def test_ip_in_entries_match_and_miss():
    entries = ["203.0.113.0/24", "10.0.0.5/32"]
    assert ipallow.ip_in_entries("203.0.113.99", entries) is True
    assert ipallow.ip_in_entries("10.0.0.5", entries) is True
    assert ipallow.ip_in_entries("192.168.1.1", entries) is False


def test_ip_in_entries_skips_broken_entry():
    # kaputter Eintrag darf keinen Treffer erzeugen, aber die Prüfung nicht sprengen
    assert ipallow.ip_in_entries("203.0.113.7", ["kaputt", "203.0.113.0/24"]) is True
    assert ipallow.ip_in_entries("8.8.8.8", ["kaputt"]) is False


def test_is_sync_allowed_disabled_always_true():
    assert ipallow.is_sync_allowed(False, "8.8.8.8", []) is True


def test_is_sync_allowed_enabled_empty_blocks_all():
    assert ipallow.is_sync_allowed(True, "8.8.8.8", []) is False


def test_is_sync_allowed_enabled_match():
    assert ipallow.is_sync_allowed(True, "203.0.113.7", ["203.0.113.0/24"]) is True
    assert ipallow.is_sync_allowed(True, "8.8.8.8", ["203.0.113.0/24"]) is False


def test_is_sync_allowed_no_ip_blocks():
    assert ipallow.is_sync_allowed(True, None, ["203.0.113.0/24"]) is False


# ---------------------------------------------------------------------------
# Enforcement in validate_api_key
# ---------------------------------------------------------------------------

def test_validate_key_allowlist_off_passes(session):
    school = sync.validate_api_key(request=FakeRequest("8.8.8.8"), x_api_key=API_KEY, session=session)
    assert school.name == "Schule A"


def test_validate_key_allowlist_on_allowed_ip_passes(session):
    school = session.get(School, 1)
    school.sync_ip_allowlist_enabled = True
    session.add(SyncIpAllowlistEntry(school_id=1, cidr="203.0.113.0/24"))
    session.commit()
    school = sync.validate_api_key(request=FakeRequest("203.0.113.50"), x_api_key=API_KEY, session=session)
    assert school.id == 1


def test_validate_key_allowlist_on_blocked_ip_403_and_audit(session):
    school = session.get(School, 1)
    school.sync_ip_allowlist_enabled = True
    session.add(SyncIpAllowlistEntry(school_id=1, cidr="203.0.113.0/24"))
    session.commit()
    with pytest.raises(HTTPException) as exc:
        sync.validate_api_key(request=FakeRequest("8.8.8.8"), x_api_key=API_KEY, session=session)
    assert exc.value.status_code == 403
    log = session.exec(select(AuditLog).where(AuditLog.action == "sync_ip_blocked")).one()
    assert log.ip_address == "8.8.8.8"
    assert log.school_id == 1


def test_validate_key_bad_key_still_401(session):
    with pytest.raises(HTTPException) as exc:
        sync.validate_api_key(request=FakeRequest(), x_api_key="wrong", session=session)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Verwaltungs-Endpoints (Berechtigung + Validierung + Anti-Lockout)
# ---------------------------------------------------------------------------

def test_add_entry_normalizes_and_audits(session):
    out = schools.add_sync_allowlist_entry(
        1, schools.AllowlistEntryIn(cidr="203.0.113.7", note="Sekretariat"),
        current_user=school_admin(), session=session)
    assert out.cidr == "203.0.113.7/32"
    assert out.note == "Sekretariat"
    assert session.exec(select(AuditLog).where(AuditLog.action == "sync_allowlist_added")).one()


def test_add_entry_invalid_400(session):
    with pytest.raises(HTTPException) as exc:
        schools.add_sync_allowlist_entry(
            1, schools.AllowlistEntryIn(cidr="nope"), current_user=school_admin(), session=session)
    assert exc.value.status_code == 400


def test_add_entry_duplicate_409(session):
    schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.7"),
                                     current_user=school_admin(), session=session)
    with pytest.raises(HTTPException) as exc:
        schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.7/32"),
                                         current_user=school_admin(), session=session)
    assert exc.value.status_code == 409


def test_teacher_forbidden(session):
    with pytest.raises(HTTPException) as exc:
        schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.7"),
                                         current_user=teacher(), session=session)
    assert exc.value.status_code == 403


def test_admin_other_school_forbidden(session):
    with pytest.raises(HTTPException) as exc:
        schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.7"),
                                         current_user=school_admin(school_id=99), session=session)
    assert exc.value.status_code == 403


def test_enable_with_empty_list_rejected(session):
    with pytest.raises(HTTPException) as exc:
        schools.set_sync_allowlist_enabled(
            1, schools.AllowlistToggle(enabled=True), request=FakeRequest(),
            current_user=school_admin(), session=session)
    assert exc.value.status_code == 400


def test_enable_then_toggle_works(session):
    schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.0/24"),
                                     current_user=school_admin(), session=session)
    out = schools.set_sync_allowlist_enabled(
        1, schools.AllowlistToggle(enabled=True), request=FakeRequest(),
        current_user=school_admin(), session=session)
    assert out.enabled is True
    assert len(out.entries) == 1


def test_delete_last_entry_auto_disables(session):
    entry = schools.add_sync_allowlist_entry(1, schools.AllowlistEntryIn(cidr="203.0.113.0/24"),
                                             current_user=school_admin(), session=session)
    schools.set_sync_allowlist_enabled(1, schools.AllowlistToggle(enabled=True),
                                       request=FakeRequest(), current_user=school_admin(), session=session)
    res = schools.delete_sync_allowlist_entry(1, entry.id, current_user=school_admin(), session=session)
    assert res["auto_disabled"] is True
    assert session.get(School, 1).sync_ip_allowlist_enabled is False


def test_get_allowlist_reports_your_ip(session):
    out = schools.get_sync_allowlist(1, request=FakeRequest("198.51.100.4"),
                                     current_user=school_admin(), session=session)
    assert out.your_ip == "198.51.100.4"
    assert out.enabled is False
    assert out.entries == []
