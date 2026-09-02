"""
Tests für Bescheinigungs-Templates (Schuladmin-Editor) + Dienstbescheinigung.

Ausführen:  cd backend && python -m pytest tests/test_cert_templates.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.schulbescheinigung import (
    render_template, render_certificate_pdf,
    DEFAULT_TEMPLATE_STUDENT, DEFAULT_TEMPLATE_TEACHER,
)


VALUES = {
    "Name": "Müller", "Vorname": "Erika", "GebDatum": "01.05.1980",
    "Bildungsgang": "", "Klasse": "5a", "Schule": "Käthe-Kollwitz-BK",
    "Schuljahr": "2026/2027",
}


def test_render_template_substitutes_variables():
    out = render_template("{Vorname} {Name}, geb. {GebDatum}, Klasse {Klasse}", VALUES)
    assert out == "Erika Müller, geb. 01.05.1980, Klasse 5a"


def test_render_template_unknown_placeholder_stays_visible():
    # Tippfehler im Template soll auffallen, nicht still verschwinden
    out = render_template("Hallo {Vornname}!", VALUES)
    assert out == "Hallo {Vornname}!"


def test_render_template_bildungsgang_resolves_empty_for_now():
    out = render_template("Bildungsgang: {Bildungsgang}.", VALUES)
    assert out == "Bildungsgang: ."


def test_default_templates_render_cleanly():
    s = render_template(DEFAULT_TEMPLATE_STUDENT, VALUES)
    assert "Erika Müller" in s and "5a" in s and "{" not in s
    t = render_template(DEFAULT_TEMPLATE_TEACHER, VALUES)
    assert "Erika Müller" in t and "Lehrkraft" in t and "{" not in t


def _pdf(**kw):
    base = dict(
        school_name="Käthe-Kollwitz-BK", student_name="Erika Müller",
        dob_str="01.05.1980", class_name="Lehrerkollegium",
        school_year="2026/2027", issue_date_str="27.08.2026",
        valid_until_str="31.07.2031", is_provisional=False,
        verify_url="https://ausweisapi.example/verify-cert?c=x",
    )
    base.update(kw)
    return render_certificate_pdf(**base)


def test_teacher_pdf_renders():
    body = render_template(DEFAULT_TEMPLATE_TEACHER, VALUES)
    pdf = _pdf(body=body, is_teacher=True)
    assert pdf.startswith(b"%PDF")


def test_student_pdf_without_body_uses_builtin_text():
    pdf = _pdf(class_name="5a")           # body=None → eingebauter Standardtext
    assert pdf.startswith(b"%PDF")


def test_pdf_is_single_page():
    """Regression: die Fußzeile (set_y in der Auto-Break-Zone) darf keine fast
    leere ZWEITE Seite erzeugen — besonders bei der kurzen Dienstbescheinigung."""
    pdf = _pdf(body=render_template(DEFAULT_TEMPLATE_TEACHER, VALUES), is_teacher=True,
               contact_email="sekretariat@schule.de",     # 2-zeilige Fußzeile = der Auslöser
               school_address="Musterstraße 1\n58095 Hagen",
               school_number="123456", school_phone="02331 12345-0")
    assert b"/Count 1" in pdf             # genau EINE Seite


def test_teacher_service_end_halfyears():
    """Dienstzeitende = Ende des Schulhalbjahres, in dem der 67. Geburtstag liegt."""
    from datetime import date
    from services.schulbescheinigung import teacher_service_end
    # Geburtstag im 1. Halbjahr (Aug–Jan): endet 31.01.
    assert teacher_service_end(date(1963, 9, 15)) == date(2031, 1, 31)   # 67. am 15.09.2030
    assert teacher_service_end(date(1964, 1, 10)) == date(2031, 1, 31)   # 67. am 10.01.2031
    # Geburtstag im 2. Halbjahr (Feb–Jul): endet 31.07.
    assert teacher_service_end(date(1964, 3, 15)) == date(2031, 7, 31)
    assert teacher_service_end(date(1964, 7, 31)) == date(2031, 7, 31)
    # Schalttag: 29.02. → 28.02. im Nicht-Schaltjahr, 2. Halbjahr
    assert teacher_service_end(date(1964, 2, 29)) == date(2031, 7, 31)
