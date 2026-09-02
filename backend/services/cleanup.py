"""
DSGVO-Datenbegrenzung für Hagen-ID

Löscht soft-gelöschte Schüler nach Ablauf der Aufbewahrungsfrist endgültig —
inklusive aller abhängigen Datensätze.

WICHTIG: `photos.student_id` und `push_subscriptions.student_id` sind Foreign
Keys auf `students.id` OHNE `ON DELETE CASCADE`. Ein reiner Bulk-Delete auf
`students` läuft deshalb in eine ForeignKeyViolation. Daraus folgen zwei
Regeln für diesen Service:
1. Abhängige Zeilen EXPLIZIT zuerst löschen.
2. Fehler NICHT schlucken — ein fehlgeschlagener DSGVO-Job ist eine
   Compliance-Verletzung und muss laut sein; sonst sammelt sich die PII
   von Ex-Schülern unbemerkt und unbegrenzt an.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, select, delete

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Student, Photo, PushSubscription


# Aufbewahrungsfrist nach dem Soft-Delete
RETENTION_WEEKS = 4

# Projektfester Schlüssel für den Postgres-Advisory-Lock des nächtlichen Jobs.
# Verhindert, dass bei mehreren Uvicorn-Workern (--workers 2) der Cron doppelt läuft
# und zwei parallele Purges auf denselben Zeilen kollidieren.
CLEANUP_LOCK_KEY = 42_000_017


def purge_expired_students(session: Session, retention_weeks: int = RETENTION_WEEKS) -> dict:
    """
    Löscht Schüler endgültig, die länger als `retention_weeks` soft-gelöscht sind.

    Reihenfolge ist zwingend: erst die abhängigen Datensätze (Fotos,
    Push-Subscriptions), dann die Schüler — sonst FK-Verletzung.

    Returns:
        dict mit den Löschzahlen: {"students": n, "photos": n, "subscriptions": n}

    Raises:
        Alle DB-Fehler werden durchgereicht — der Aufrufer MUSS laut scheitern.
    """
    cutoff = datetime.utcnow() - timedelta(weeks=retention_weeks)

    student_ids = session.exec(
        select(Student.id).where(Student.deleted_at < cutoff)
    ).all()

    if not student_ids:
        return {"students": 0, "photos": 0, "subscriptions": 0}

    # 1. Abhängige Datensätze zuerst (kein ON DELETE CASCADE in der DB!)
    photos_res = session.exec(
        delete(Photo).where(Photo.student_id.in_(student_ids))
    )
    subs_res = session.exec(
        delete(PushSubscription).where(PushSubscription.student_id.in_(student_ids))
    )

    # 2. Dann die Schüler selbst
    session.exec(
        delete(Student).where(Student.id.in_(student_ids))
    )

    session.commit()

    return {
        "students": len(student_ids),
        "photos": photos_res.rowcount,
        "subscriptions": subs_res.rowcount,
    }


def run_full_cleanup(engine) -> dict:
    """
    Nächtlicher DSGVO-Sammel-Job (vom APScheduler aufgerufen): löscht abgelaufene
    soft-gelöschte Schüler endgültig + räumt alte Login-Attempts und Audit-Logs auf,
    und schreibt einen `system_cleanup_ran`-Heartbeat fürs Superadmin-Statusboard.

    **Single-Flight:** Auf Postgres wird ein Session-Advisory-Lock gehalten, damit bei
    mehreren Uvicorn-Workern nur EINER den Job ausführt (der zweite überspringt sauber).
    Auf SQLite (Dev, Einzelprozess) ist kein Lock nötig.

    Returns: Summary-dict (bei übersprungenem Lauf `{"skipped": True}`).
    Raises: harte Fehler im Schüler-Purge werden durchgereicht — ein stiller
    DSGVO-Fehler ist inakzeptabel; der Aufrufer muss laut scheitern.
    """
    from sqlalchemy import text as _sql_text
    from services.ratelimit import purge_old as purge_login_attempts
    from services.audit import purge_old as purge_audit_logs
    from services import audit as audit_svc

    is_pg = engine.dialect.name == "postgresql"

    with Session(engine) as session:
        if is_pg:
            got = bool(session.execute(
                _sql_text("SELECT pg_try_advisory_lock(:k)"), {"k": CLEANUP_LOCK_KEY}
            ).scalar())
            if not got:
                print("⏭️  DSGVO Cleanup: anderer Worker hält den Lock — übersprungen")
                return {"skipped": True}
        try:
            result = purge_expired_students(session)

            purged_attempts = 0
            try:
                purged_attempts = purge_login_attempts(session)
                if purged_attempts:
                    print(f"🧹 Rate-Limit-Cleanup: {purged_attempts} alte login_attempts entfernt")
            except Exception as e:
                print(f"⚠️  Rate-Limit-Cleanup: {e}")  # nicht kritisch, Job nicht abbrechen

            purged_audit = 0
            try:
                purged_audit = purge_audit_logs(session)
                if purged_audit:
                    print(f"🧹 Audit-Retention: {purged_audit} Log-Zeilen entfernt")
            except Exception as e:
                print(f"⚠️  Audit-Retention: {e}")  # nicht kritisch, Job nicht abbrechen

            audit_svc.record(
                session, "system_cleanup_ran", target_table=None, result="ok",
                extra={
                    "students": result["students"], "photos": result["photos"],
                    "subscriptions": result["subscriptions"],
                    "login_attempts_purged": purged_attempts,
                    "audit_rows_purged": purged_audit,
                },
            )
        finally:
            if is_pg:
                # Falls der Purge mit offener/fehlerhafter Transaktion abbrach: erst
                # aufräumen, damit der Unlock nicht in einer aborted Transaction läuft.
                try:
                    session.rollback()
                except Exception:
                    pass
                session.execute(_sql_text("SELECT pg_advisory_unlock(:k)"), {"k": CLEANUP_LOCK_KEY})
                session.commit()

    if result["students"]:
        print(
            f"🗑️  DSGVO Cleanup: {result['students']} Schüler endgültig gelöscht "
            f"(inkl. {result['photos']} Fotos, {result['subscriptions']} Push-Subscriptions)"
        )
    else:
        print("✅ DSGVO Cleanup: Keine alten Schüler zu löschen")

    result["login_attempts_purged"] = purged_attempts
    result["audit_rows_purged"] = purged_audit
    return result
