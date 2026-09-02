"""
Regressionstests für die Mandantentrennung.

Testet die Autorisierungs-Helfer (services/authz.py) sowie die kritischen
admin.py-Endpunkte direkt (ohne HTTP-Layer/TestClient — daher keine
httpx-Abhängigkeit nötig). Die Scope-Prüfung läuft in den Handlern als
erste Aktion, sodass der direkte Aufruf denselben Sicherheitspfad trifft.

Ausführen:
    cd backend && python -m pytest tests/test_authz_scope.py -q
"""

import os
# JWT_SECRET muss vor dem Import von routes.auth/services.auth gesetzt sein
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import uuid
from datetime import date

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import SQLModel, Session, create_engine
from fastapi import HTTPException

from models import School, Class, Student, User
from services import authz
import routes.admin as admin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class Ctx:
    """Container für die Testobjekte (Schule A/B, Klassen, Schüler)."""
    pass


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        a = School(name="Schule A")
        b = School(name="Schule B")
        s.add(a); s.add(b); s.commit(); s.refresh(a); s.refresh(b)

        ca = Class(school_id=a.id, name="A1", validity_end=date(2027, 7, 31))
        cb = Class(school_id=b.id, name="B1", validity_end=date(2027, 7, 31))
        s.add(ca); s.add(cb); s.commit(); s.refresh(ca); s.refresh(cb)

        sa = Student(school_internal_id="1", first_name="Anna", last_name="A",
                     dob=date(2005, 1, 1), class_id=ca.id, is_active=True, login_pin="123456")
        sb = Student(school_internal_id="2", first_name="Bob", last_name="B",
                     dob=date(2006, 1, 1), class_id=cb.id, is_active=True, login_pin="654321")
        s.add(sa); s.add(sb); s.commit(); s.refresh(sa); s.refresh(sb)

        c = Ctx()
        c.session = s
        c.a, c.b, c.ca, c.cb, c.sa, c.sb = a, b, ca, cb, sa, sb
        yield c


def teacher(school_id):
    return User(id=1, email="t@a.de", password_hash="x", role="teacher",
                school_id=school_id, is_active=True)


def school_admin(school_id):
    return User(id=2, email="sa@a.de", password_hash="x", role="school_admin",
                school_id=school_id, is_active=True)


def superadmin():
    return User(id=9, email="su@x.de", password_hash="x", role="superadmin",
                school_id=None, is_active=True)


def status_of(fn, *args, **kwargs):
    """Ruft eine Funktion auf und gibt den HTTP-Status zurück (None bei Erfolg)."""
    try:
        fn(*args, **kwargs)
        return None
    except HTTPException as e:
        return e.status_code


# ---------------------------------------------------------------------------
# Unit-Tests der Helfer
# ---------------------------------------------------------------------------

def test_assert_school_access(ctx):
    assert status_of(authz.assert_school_access, teacher(ctx.a.id), ctx.a.id) is None
    assert status_of(authz.assert_school_access, teacher(ctx.a.id), ctx.b.id) == 403
    assert status_of(authz.assert_school_access, superadmin(), ctx.b.id) is None


def test_load_student_in_scope(ctx):
    # eigener Schüler ok
    st = authz.load_student_in_scope(str(ctx.sa.id), teacher(ctx.a.id), ctx.session)
    assert st.id == ctx.sa.id
    # fremder Schüler -> 404 (verrät Existenz nicht)
    assert status_of(authz.load_student_in_scope, str(ctx.sb.id), teacher(ctx.a.id), ctx.session) == 404
    # kaputte UUID -> 400
    assert status_of(authz.load_student_in_scope, "not-a-uuid", teacher(ctx.a.id), ctx.session) == 400
    # nicht existent -> 404
    assert status_of(authz.load_student_in_scope, str(uuid.uuid4()), teacher(ctx.a.id), ctx.session) == 404
    # superadmin darf fremden lesen
    assert authz.load_student_in_scope(str(ctx.sb.id), superadmin(), ctx.session).id == ctx.sb.id


def test_load_class_in_scope(ctx):
    assert authz.load_class_in_scope(ctx.ca.id, teacher(ctx.a.id), ctx.session).id == ctx.ca.id
    assert status_of(authz.load_class_in_scope, ctx.cb.id, teacher(ctx.a.id), ctx.session) == 404
    assert status_of(authz.load_class_in_scope, 99999, teacher(ctx.a.id), ctx.session) == 404
    assert authz.load_class_in_scope(ctx.cb.id, superadmin(), ctx.session).id == ctx.cb.id


# ---------------------------------------------------------------------------
# Endpunkt-Tests (kritische Pfade)
# ---------------------------------------------------------------------------

def test_list_classes_scoped(ctx):
    # eigene Schule ok
    res = admin.list_classes(school_id=ctx.a.id, current_admin=teacher(ctx.a.id), session=ctx.session)
    assert [c.name for c in res] == ["A1"]
    # fremde Schule -> 403
    assert status_of(admin.list_classes, school_id=ctx.b.id, current_admin=teacher(ctx.a.id), session=ctx.session) == 403
    # superadmin darf fremde Schule
    res_su = admin.list_classes(school_id=ctx.b.id, current_admin=superadmin(), session=ctx.session)
    assert [c.name for c in res_su] == ["B1"]


def test_list_students_foreign_school_forbidden(ctx):
    assert status_of(
        admin.list_students, school_id=ctx.b.id, current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 403


def test_print_data_pin_export_scoped(ctx):
    # DER gefährlichste Endpunkt: Klartext-PINs fremder Schule -> 404
    assert status_of(
        admin.get_class_print_data, class_id=ctx.cb.id, current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 404
    # eigene Klasse liefert PINs
    res = admin.get_class_print_data(class_id=ctx.ca.id, current_admin=teacher(ctx.a.id), session=ctx.session)
    assert res[0].login_pin == "123456"


def test_get_student_foreign_forbidden(ctx):
    assert status_of(
        admin.get_student, student_id=str(ctx.sb.id), current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 404
    # eigener Schüler ok
    det = admin.get_student(student_id=str(ctx.sa.id), current_admin=teacher(ctx.a.id), session=ctx.session)
    assert det.first_name == "Anna"


def test_deactivate_foreign_student_forbidden(ctx):
    assert status_of(
        admin.deactivate_student, student_id=str(ctx.sb.id), current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 404


def test_create_student_requires_class(ctx):
    from routes.admin import StudentCreate
    data = StudentCreate(first_name="Neu", last_name="Ohneklasse", dob=date(2007, 5, 5))
    assert status_of(
        admin.create_student, data=data, school_id=ctx.a.id, current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 400


def test_create_student_foreign_class_rejected(ctx):
    from routes.admin import StudentCreate
    # Admin A versucht, Schüler an Klasse von Schule B zu hängen (Cross-Tenant-Write)
    data = StudentCreate(first_name="Hack", last_name="Versuch", dob=date(2007, 5, 5), class_id=ctx.cb.id)
    assert status_of(
        admin.create_student, data=data, school_id=ctx.a.id, current_admin=teacher(ctx.a.id), session=ctx.session
    ) == 404


def test_create_student_valid(ctx):
    from routes.admin import StudentCreate
    data = StudentCreate(first_name="Gül", last_name="Neu", dob=date(2007, 5, 5), class_name="A1")
    det = admin.create_student(data=data, school_id=ctx.a.id, current_admin=teacher(ctx.a.id), session=ctx.session)
    assert det.class_name == "A1"
    assert det.first_name == "Gül"


def test_dashboard_last_import_scoped_to_school(ctx):
    # last_import darf NICHT den Import-Zeitpunkt einer anderen Schule leaken
    from models import AuditLog
    from datetime import datetime
    ctx.session.add(AuditLog(action="bulk_import", school_id=ctx.a.id, timestamp=datetime(2026, 1, 1)))
    ctx.session.add(AuditLog(action="bulk_import", school_id=ctx.b.id, timestamp=datetime(2026, 6, 1)))
    ctx.session.commit()
    stats = admin.get_dashboard_stats(school_id=ctx.a.id, current_admin=teacher(ctx.a.id), session=ctx.session)
    assert stats.last_import == datetime(2026, 1, 1)  # A's Import, nicht B's neueren


def test_fail_closed_non_superadmin_without_school(ctx):
    # Nicht-Superadmin ohne Schule wird überall abgewiesen (fail-closed)
    ghost = User(id=5, email="g@x.de", password_hash="x", role="teacher", school_id=None, is_active=True)
    assert status_of(authz.assert_school_access, ghost, ctx.a.id) == 403
    assert status_of(authz.assert_student_scope, ctx.sa, ghost, ctx.session) == 404
    # Auch bei einem Schüler ohne auflösbare Klasse (None==None-Falle)
    orphan = Student(school_internal_id="9", first_name="X", last_name="Y",
                     dob=date(2005, 1, 1), class_id=None, is_active=True)
    ctx.session.add(orphan); ctx.session.commit(); ctx.session.refresh(orphan)
    assert status_of(authz.assert_student_scope, orphan, ghost, ctx.session) == 404


def test_api_key_teacher_may_not_mint(ctx):
    """
    Der Sync-API-Key umgeht ALLE Session-Layer (portabel, session-los, kein Rate-Limit,
    unbegrenzt gültig) und gibt den kompletten Roster lesend+schreibend frei.
    Ein `teacher` darf ihn deshalb NICHT ausstellen — mindestens school_admin.
    """
    import routes.schools as schools

    # teacher -> 403 (auch für die EIGENE Schule)
    assert status_of(schools.generate_api_key, school_id=ctx.a.id,
                     current_user=teacher(ctx.a.id), session=ctx.session) == 403
    assert status_of(schools.revoke_api_key, school_id=ctx.a.id,
                     current_user=teacher(ctx.a.id), session=ctx.session) == 403

    # school_admin der eigenen Schule -> darf
    res = schools.generate_api_key(school_id=ctx.a.id,
                                   current_user=school_admin(ctx.a.id), session=ctx.session)
    assert res["api_key"].startswith("hgn_sk_")
    assert len(res["api_key"]) == len("hgn_sk_") + 64  # token_hex(32) = 64 Zeichen

    # school_admin einer FREMDEN Schule -> 403
    assert status_of(schools.generate_api_key, school_id=ctx.b.id,
                     current_user=school_admin(ctx.a.id), session=ctx.session) == 403

    # superadmin -> darf überall
    assert schools.generate_api_key(school_id=ctx.b.id, current_user=superadmin(),
                                    session=ctx.session)["api_key"].startswith("hgn_sk_")


def test_api_key_issuance_is_audited(ctx):
    """Ausstellung UND Widerruf der mächtigsten Credential müssen protokolliert sein."""
    import json
    import routes.schools as schools
    from models import AuditLog
    from sqlmodel import select

    schools.generate_api_key(school_id=ctx.a.id, current_user=school_admin(ctx.a.id), session=ctx.session)
    schools.revoke_api_key(school_id=ctx.a.id, current_user=school_admin(ctx.a.id), session=ctx.session)

    logs = ctx.session.exec(
        select(AuditLog).where(AuditLog.action.in_(["api_key_generated", "api_key_revoked"]))
    ).all()
    actions = {l.action for l in logs}
    assert actions == {"api_key_generated", "api_key_revoked"}
    for l in logs:
        assert l.user_id == 2          # der handelnde school_admin
        assert l.school_id == ctx.a.id  # schul-attribuiert
        json.loads(l.details)           # valides JSON


def test_get_school_own_vs_foreign(ctx):
    """
    GET /schools/{id}: School-Admin/Teacher darf die EIGENE Schule lesen (für die
    pwa_base_url auf der QR-Druckseite), Superadmin jede — fremde Schule = 403.
    """
    import routes.schools as schools

    # school_admin der Schule A: eigene ok, fremde 403
    det = schools.get_school(school_id=ctx.a.id, current_user=school_admin(ctx.a.id), session=ctx.session)
    assert det.id == ctx.a.id
    assert status_of(schools.get_school, school_id=ctx.b.id, current_user=school_admin(ctx.a.id), session=ctx.session) == 403

    # teacher der Schule A: eigene ok (Druckseite), fremde 403
    assert schools.get_school(school_id=ctx.a.id, current_user=teacher(ctx.a.id), session=ctx.session).id == ctx.a.id
    assert status_of(schools.get_school, school_id=ctx.b.id, current_user=teacher(ctx.a.id), session=ctx.session) == 403

    # superadmin: jede Schule
    assert schools.get_school(school_id=ctx.b.id, current_user=superadmin(), session=ctx.session).id == ctx.b.id


def test_audit_log_records_acting_user(ctx):
    from models import AuditLog
    from sqlmodel import select
    admin.deactivate_student(student_id=str(ctx.sa.id), current_admin=teacher(ctx.a.id), session=ctx.session)
    log = ctx.session.exec(
        select(AuditLog).where(AuditLog.action == "manual_deactivate")
    ).first()
    assert log is not None and log.user_id == 1
    # details ist valides JSON (kein String-Injection)
    import json
    parsed = json.loads(log.details)
    assert parsed["name"] == "Anna A"
