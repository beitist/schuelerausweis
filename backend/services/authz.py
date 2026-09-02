"""
Autorisierungs-Helfer für Mandantentrennung und Rollenlogik.

Alle Funktionen nehmen den bereits authentifizierten `current_admin`
(aus routes.auth.get_current_admin) als Argument entgegen. Dadurch bleibt
dieses Modul frei von Router-/Request-Abhängigkeiten und ist gut testbar.

Regel:
- superadmin  → Zugriff auf alle Schulen
- sonst       → nur die eigene school_id

Fehlercodes (bewusst gewählt):
- Objekt-Zugriff auf fremde Schule → 404 (verrät die Existenz fremder IDs nicht)
- expliziter `school_id`-Query-Parameter → 403 (klare Aussage: gehört dir nicht)
"""

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import User, Student, Class


def role_of(user: User) -> str:
    """Rolle als lowercase-String (Enum ODER String)."""
    r = user.role.value if hasattr(user.role, "value") else user.role
    return str(r).lower()


def is_superadmin(user: User) -> bool:
    return role_of(user) == "superadmin"


def assert_school_access(current_admin: User, school_id: int) -> None:
    """
    Stellt sicher, dass der Admin zu `school_id` gehört.
    Für Endpunkte, die `school_id` als Query-Parameter erhalten.
    """
    if is_superadmin(current_admin):
        return
    # Fail-closed: ein Nicht-Superadmin ohne Schule hat nirgends Zugriff
    if current_admin.school_id is None or current_admin.school_id != school_id:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Schule")


def _student_school_id(student: Student, session: Session) -> Optional[int]:
    """Ermittelt die Schule eines Schülers über seine Klasse."""
    if not student.class_id:
        return None
    cls = session.get(Class, student.class_id)
    return cls.school_id if cls else None


def assert_student_scope(student: Student, current_admin: User, session: Session) -> None:
    """Prüft, ob ein bereits geladener Schüler zur Schule des Admins gehört."""
    if is_superadmin(current_admin):
        return
    student_school_id = _student_school_id(student, session)
    # Fail-closed: Nicht-Superadmin ohne Schule ODER Schüler ohne auflösbare Schule → kein Zugriff.
    # (Verhindert den None==None-Fall, falls die Klasse fehlt/gelöscht ist.)
    if current_admin.school_id is None or student_school_id != current_admin.school_id:
        # 404 statt 403 → verrät die Existenz fremder IDs nicht.
        raise HTTPException(status_code=404, detail="Schüler nicht gefunden")


def load_student_in_scope(student_id: str, current_admin: User, session: Session) -> Student:
    """
    Parst die UUID, lädt den Schüler und erzwingt die Schul-Bindung.
    Ersetzt das wiederholte `uuid.UUID(...)` + `session.get(Student, ...)` in den Handlern.
    """
    try:
        sid = uuid.UUID(student_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültige Schüler-ID")
    student = session.get(Student, sid)
    if not student:
        raise HTTPException(status_code=404, detail="Schüler nicht gefunden")
    assert_student_scope(student, current_admin, session)
    return student


def load_class_in_scope(class_id: int, current_admin: User, session: Session) -> Class:
    """Lädt eine Klasse und erzwingt die Schul-Bindung."""
    cls = session.get(Class, class_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Klasse nicht gefunden")
    if not is_superadmin(current_admin) and cls.school_id != current_admin.school_id:
        raise HTTPException(status_code=404, detail="Klasse nicht gefunden")
    return cls
