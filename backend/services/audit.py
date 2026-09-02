"""
Zentrales Audit-Logging für den Superadmin-Log-Viewer.

Ein Ort, an dem alle protokollierten Ereignisse entstehen — konsistent und testbar.
Schreibt Zeilen in `audit_logs`.

Datenschutz-Leitlinien:
- **Pseudonym:** gespeichert wird die ID (Schüler-UUID / Admin-E-Mail), NICHT der Klarname.
  Namen löst der Viewer bei Bedarf auf — das Log selbst bleibt schlank.
- **Ausweis-Loads werden NICHT protokolliert** (reiner Selbst-Zugriff des Schülers).
  Erst ein gezielter, zeitlich begrenzter „Trace" erfasst solche Details.
- **IP nur, wenn forensisch relevant** (Verifizierung durch Kontrolleure, fehlgeschlagene
  Logins, Sync) — nicht beim Selbst-Zugriff.
- **Retention 180 Tage** (Cron purged Älteres) — siehe main.py.
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select, delete, func

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import AuditLog, AccessTrace

# Aufbewahrung: 180 Tage. Danach purged der Cron Älteres.
RETENTION_DAYS = 180


def record(
    session: Session,
    action: str,
    *,
    actor_user_id: Optional[int] = None,
    school_id: Optional[int] = None,
    subject_id: Optional[str] = None,
    target_table: Optional[str] = None,
    result: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra: Optional[dict] = None,
    commit: bool = True,
) -> None:
    """
    Schreibt ein Audit-Ereignis.

    action:       kurzer Slug, z.B. "id_verified", "cert_downloaded", "student_login_failed"
    actor_user_id: handelnder Admin (falls zutreffend), sonst None
    subject_id:    betroffene ID (Schüler-UUID / E-Mail) — indexiert, für Filter/Trace
    result:        z.B. "valid" / "invalid" / "kicked_out"
    ip / user_agent: nur setzen, wenn forensisch relevant (siehe Leitlinien oben)
    extra:         zusätzliche, NICHT-sensible Details fürs JSON-Feld
    commit:        False, wenn der Aufrufer selbst committet (Standard: True)
    """
    details = {}
    if result is not None:
        details["result"] = result
    if extra:
        details.update(extra)

    session.add(AuditLog(
        user_id=actor_user_id,
        school_id=school_id,
        subject_id=str(subject_id) if subject_id is not None else None,
        action=action,
        target_table=target_table,
        details=json.dumps(details, ensure_ascii=False) if details else None,
        ip_address=ip,
        user_agent=user_agent,
    ))
    if commit:
        session.commit()


def active_trace(session: Session, *, school_id: Optional[int], subject_id: Optional[str]) -> bool:
    """
    Prüft, ob für diese Schule/ID gerade ein Trace läuft.

    Ein Trace matcht, wenn seine Schule passt UND
    (er die ganze Schule abdeckt (subject_id IS NULL) ODER exakt diese ID meint).
    Aktiv = nicht widerrufen UND noch nicht abgelaufen.

    Bewusst „fail-open zu NICHT-loggen": Wirft die Prüfung (o.ä.), wird kein Trace
    angenommen — im Zweifel protokollieren wir also WENIGER, nicht mehr (Datenschutz).
    """
    if school_id is None:
        return False
    now = datetime.utcnow()
    conds = [
        AccessTrace.school_id == school_id,
        AccessTrace.revoked_at.is_(None),
        AccessTrace.expires_at > now,
    ]
    q = select(AccessTrace.id).where(*conds)
    # Schulweiter Trace (subject_id NULL) ODER exakt passende ID
    if subject_id is not None:
        q = q.where(
            (AccessTrace.subject_id.is_(None)) | (AccessTrace.subject_id == str(subject_id))
        )
    else:
        q = q.where(AccessTrace.subject_id.is_(None))
    return session.exec(q.limit(1)).first() is not None


def record_card_load(
    session: Session,
    action: str,
    *,
    school_id: Optional[int],
    subject_id: Optional[str],
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    extra: Optional[dict] = None,
) -> bool:
    """
    Protokolliert einen Ausweis-Load NUR, wenn gerade ein Trace aktiv ist.

    Im Normalbetrieb sind Ausweis-Loads reiner Selbst-Zugriff und werden NICHT
    geloggt. Erst ein gezielter Trace macht sie sichtbar —
    dann inklusive IP. Gibt True zurück, wenn tatsächlich geschrieben wurde.
    """
    if not active_trace(session, school_id=school_id, subject_id=subject_id):
        return False
    record(session, action, school_id=school_id, subject_id=subject_id,
           target_table="students", ip=ip, user_agent=user_agent,
           extra={**(extra or {}), "trace": True})
    return True


def ip_if_traced(session: Session, *, school_id: Optional[int], subject_id: Optional[str],
                 ip: Optional[str]) -> Optional[str]:
    """
    Gibt die IP nur zurück, wenn ein Trace aktiv ist — sonst None.

    Für Ereignisse, die IMMER geloggt werden (Verifizierung, Bescheinigungs-Download):
    die IP wird der Zeile nur dann beigefügt, wenn gerade getract wird.
    """
    if ip and active_trace(session, school_id=school_id, subject_id=subject_id):
        return ip
    return None


def purge_old(session: Session, retention_days: int = RETENTION_DAYS) -> int:
    """
    Löscht Audit-Zeilen, die älter als `retention_days` sind (DSGVO-Datenbegrenzung).

    Wird täglich vom Cron aufgerufen. Gibt die Anzahl gelöschter Zeilen zurück.
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    n = session.exec(
        select(func.count(AuditLog.id)).where(AuditLog.timestamp < cutoff)
    ).one()
    if n:
        session.exec(delete(AuditLog).where(AuditLog.timestamp < cutoff))
        session.commit()
    return n
