"""
Tests für den PIN-CSV-Export (Serienmail-Datenquelle): GET /admin/classes/{id}/export.csv

Ausführen:  cd backend && python -m pytest tests/test_csv_export.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

from datetime import date
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from models import School, Class, Student, User, AuditLog
import routes.admin as admin


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        sc = School(name="S"); s.add(sc); s.commit(); s.refresh(sc)
        cl = Class(school_id=sc.id, name="Lehrerkollegium", validity_end=date(2031, 7, 31))
        s.add(cl); s.commit(); s.refresh(cl)
        adm = User(id=2, email="a@x.de", password_hash="x", role="school_admin",
                   school_id=sc.id, is_active=True)
        yield s, sc, cl, adm


def _teacher(s, cl, first, last, email=None, pin=None, sid="1"):
    st = Student(school_internal_id=sid, first_name=first, last_name=last,
                 dob=date(1980, 1, 1), class_id=cl.id, is_active=True,
                 email=email, login_pin=pin)
    s.add(st); s.commit()
    return st


def test_csv_export_format_and_content(ctx):
    s, sc, cl, adm = ctx
    _teacher(s, cl, "Zoe", "Über", email="ueber@schule.de", pin="111111", sid="L-1")
    _teacher(s, cl, "Anna", "Abel", email=None, pin="222222", sid="L-2")

    resp = admin.export_class_pins_csv(class_id=cl.id, current_admin=adm, session=s)

    body = resp.body.decode("utf-8")
    assert body.startswith("﻿")                       # BOM für Excel
    lines = body.lstrip("﻿").strip().split("\r\n")     # CRLF-Zeilen
    assert lines[0] == "Nachname;Vorname;E-Mail;PIN;Klasse"
    # Sortierung nach Nachname: Abel vor Über; leere E-Mail = leeres Feld
    assert lines[1] == "Abel;Anna;;222222;Lehrerkollegium"
    assert lines[2] == "Über;Zoe;ueber@schule.de;111111;Lehrerkollegium"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-type"].startswith("text/csv")


def test_csv_export_regenerates_missing_pin_and_audits(ctx):
    s, sc, cl, adm = ctx
    st = _teacher(s, cl, "Max", "Ohnepin", email="m@x.de", pin=None, sid="L-3")

    resp = admin.export_class_pins_csv(class_id=cl.id, current_admin=adm, session=s)

    s.refresh(st)
    assert st.login_pin and len(st.login_pin) == 6          # PIN wurde nachgeneriert
    assert st.login_pin in resp.body.decode("utf-8")
    audit = s.exec(select(AuditLog).where(AuditLog.action == "pins_exported")).all()
    assert len(audit) == 1                                   # Export protokolliert


def test_csv_export_scope_enforced(ctx):
    s, sc, cl, adm = ctx
    other = School(name="Fremd"); s.add(other); s.commit(); s.refresh(other)
    foreign_cl = Class(school_id=other.id, name="5a", validity_end=date(2027, 7, 31))
    s.add(foreign_cl); s.commit(); s.refresh(foreign_cl)

    from fastapi import HTTPException
    with pytest.raises(HTTPException):                       # fremde Schule → verboten
        admin.export_class_pins_csv(class_id=foreign_cl.id, current_admin=adm, session=s)
