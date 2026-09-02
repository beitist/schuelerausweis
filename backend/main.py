"""
Hagen-ID Backend API

Haupteinstiegspunkt für die FastAPI-Anwendung.
Verwaltet das digitale Schülerausweis-System.

Endpoints:
    - /           : Health-Check
    - /admin/*    : Admin-Dashboard, CSV-Import, Schülerverwaltung
    - /auth/*     : Login für Schüler und Admins
    - /pwa/*      : Schüler-App (Ausweis, QR-Code, Bescheinigung)
    - /schools/*  : Schulverwaltung (Superadmin)
    - /sync/*     : API-Key-basierte Sync-Schnittstelle für externe Tools
    - /check      : Öffentliche QR-Verifizierung
    - /verify-cert: Öffentliche Bescheinigungs-Verifizierung
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlmodel import SQLModel, Session, create_engine, select

# --- Models importieren (ALLE für automatische Tabellenerstellung) ---
from models import (
    School, Level, Class, Student, Photo,
    User, AuditLog, PushSubscription, UserRole
)

# --- Routes importieren ---
from routes import admin as admin_routes
from routes import auth as auth_routes
from routes import pwa as pwa_routes
from routes import schools as schools_routes
from routes import sync as sync_routes

# ============================================================================
# DATENBANK SETUP
# ============================================================================

# .env Datei explizit laden (wichtig für lokalen Start ohne Docker)
from dotenv import load_dotenv
load_dotenv()

# Die Datenbank-URL muss gesetzt sein; es gibt bewusst keinen SQLite-Fallback.
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("FATAL: DATABASE_URL environment variable is not set!")

# SQLAlchemy erwartet das Schema 'postgresql://', manche Hoster liefern 'postgres://'.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Engine erstellen
# echo=True zeigt SQL-Queries im Log (nur für Entwicklung!)
engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_DEBUG", "false").lower() == "true",
    # pool_pre_ping=True hilft bei verlorenen Verbindungen
    pool_pre_ping=True,
    # Pool größer als der SQLAlchemy-Default (5+10) — gegen Thundering-Herd am 1. Schultag
    # (1000 Erstlogins/10 min). 2 Worker × (15+25) = 80 < Postgres max_connections (100).
    # Für SQLite (Tests) werden diese Args ignoriert.
    **({} if DATABASE_URL.startswith("sqlite") else dict(
        pool_size=int(os.getenv("DB_POOL_SIZE", "15")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "25")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    )),
)

# Beim Start (mit maskiertem Passwort) loggen, wohin sich die App verbindet.
try:
    from sqlalchemy.engine.url import make_url
    url = make_url(DATABASE_URL)
    safe_url = f"{url.drivername}://{url.username}:***@{url.host}:{url.port}/{url.database}"

    if "sqlite" in url.drivername:
        print(f"⚠️  WARNUNG: Nutze SQLite Datenbank! ({url.database})")
    else:
        print(f"📡 DB CONNECTION: {safe_url} (SSL: {url.query.get('sslmode', 'default')})")
        
except Exception as e:
    print(f"📡 DB CONNECTION ERROR: {e}")


# RLS-Choke-Point an die Engine hängen (Checkout/Checkin-Events).
# No-Op, solange RLS_ENABLED aus ist oder die Engine nicht Postgres ist.
from services import rls as _rls
_rls.register(engine)


def get_session():
    """
    Dependency für DB-Session.
    Wird in Route-Funktionen mit Depends(get_session) verwendet.
    """
    with Session(engine) as session:
        yield session


# ============================================================================
# APP LIFECYCLE
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle-Handler: Wird beim Start/Stop der App ausgeführt.

    Startup:
        - Erstellt fehlende Tabellen und führt idempotente Schema-Migrationen aus
        - Legt bei leerer Datenbank eine Default-Schule samt Admin-Accounts an
        - Startet den nächtlichen DSGVO-Cleanup-Job

    Shutdown:
        - Stoppt den Scheduler
    """
    # --- STARTUP ---
    print("🚀 Starte Hagen-ID Backend...")
    
    # Tabellen erstellen (nur fehlende, bestehende bleiben)
    # WICHTIG: PostgreSQL ENUM Types (UserRole, ValidityStrategy) können beim
    # Neustart Konflikte verursachen, weil SQLAlchemy versucht sie erneut zu
    # erstellen. Lösung: Bestehende ENUMs vorher als "already created" markieren.
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)
    
    # Alle existierenden ENUM-Typen in der DB abfragen
    from sqlalchemy import text
    with engine.connect() as conn:
        existing_enums = {
            row[0] for row in conn.execute(
                text("SELECT typname FROM pg_type WHERE typtype = 'e'")
            ).fetchall()
        }
    
    # SQLAlchemy ENUM-Objekte als "bereits erstellt" markieren,
    # damit create_all() sie überspringt
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            if hasattr(column.type, 'enums') and hasattr(column.type, 'create_type'):
                # PostgreSQL ENUM-Spalte gefunden
                enum_name = column.type.name or column.type.impl.name if hasattr(column.type, 'impl') else None
                if enum_name and enum_name.lower() in existing_enums:
                    column.type.create_type = False
    
    # ------------------------------------------------------------------
    # SINGLE-FLIGHT-STARTUP-LOCK: Bei --workers 2 fahren sonst BEIDE Worker
    # gleichzeitig create_all + Migrationen (ALTER = AccessExclusiveLock) + Seed
    # (SELECT = AccessShareLock) → Deadlock (+ theoretisch Duplikat-Seed). Ein
    # blockierender Postgres-Advisory-Lock serialisiert das: Worker A macht die
    # Arbeit, Worker B wartet und findet danach alles idempotent erledigt vor.
    # Freigabe erfolgt nach dem Seed (vor dem Scheduler).
    # ------------------------------------------------------------------
    _startup_lock_conn = None
    if not DATABASE_URL.startswith("sqlite"):
        _startup_lock_conn = engine.raw_connection()
        _lc = _startup_lock_conn.cursor()
        _lc.execute("SELECT pg_advisory_lock(42000002)")   # blockierend: Worker B wartet hier
        _lc.close()
        _startup_lock_conn.commit()

    SQLModel.metadata.create_all(engine)
    print("✅ Datenbank-Tabellen geprüft/erstellt")

    # =========================================================================
    # MIGRATION: subdomain_slug → path_slug (einmalig, idempotent)
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            # Prüfe ob alte Spalte noch existiert
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'schools'")
            ).fetchall()}
            if "subdomain_slug" in cols and "path_slug" not in cols:
                conn.execute(sa_text("ALTER TABLE schools RENAME COLUMN subdomain_slug TO path_slug"))
                conn.execute(sa_text("ALTER TABLE schools ALTER COLUMN path_slug DROP NOT NULL"))
                conn.commit()
                print("✅ Migration: subdomain_slug → path_slug")
            elif "path_slug" in cols:
                pass  # Bereits migriert
    except Exception as e:
        print(f"⚠️  Migration path_slug: {e}")

    # =========================================================================
    # MIGRATION: pwa_base_url Spalte hinzufügen (einmalig, idempotent)
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'schools'")
            ).fetchall()}
            if "pwa_base_url" not in cols:
                conn.execute(sa_text("ALTER TABLE schools ADD COLUMN pwa_base_url VARCHAR"))
                conn.commit()
                print("✅ Migration: pwa_base_url Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration pwa_base_url: {e}")

    # =========================================================================
    # MIGRATION: logo_data Spalte hinzufügen (einmalig, idempotent)
    # Lokal gecachtes Schullogo (Base64) für die PDF-Schulbescheinigung.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'schools'")
            ).fetchall()}
            if "logo_data" not in cols:
                conn.execute(sa_text("ALTER TABLE schools ADD COLUMN logo_data VARCHAR"))
                conn.commit()
                print("✅ Migration: logo_data Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration logo_data: {e}")

    # =========================================================================
    # MIGRATION: cert_verify_token Spalte hinzufügen (einmalig, idempotent)
    # Dauerhafter Token für die Schulbescheinigungs-Verifizierung.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'students'")
            ).fetchall()}
            if "cert_verify_token" not in cols:
                conn.execute(sa_text("ALTER TABLE students ADD COLUMN cert_verify_token VARCHAR"))
                conn.execute(sa_text(
                    "CREATE INDEX IF NOT EXISTS ix_students_cert_verify_token "
                    "ON students (cert_verify_token)"
                ))
                conn.commit()
                print("✅ Migration: cert_verify_token Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration cert_verify_token: {e}")

    # =========================================================================
    # MIGRATION: schools.cert_template_student/_teacher (Template-Editor für
    # Schul-/Dienstbescheinigung; NULL = eingebauter Standardtext)
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'schools'")
            ).fetchall()}
            added = []
            for col in ("cert_template_student", "cert_template_teacher",
                        "address", "school_number", "phone"):
                if col not in cols:
                    conn.execute(sa_text(f"ALTER TABLE schools ADD COLUMN {col} VARCHAR"))
                    added.append(col)
            if added:
                conn.commit()
                print(f"✅ Migration: {', '.join(added)} Spalte(n) hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration cert_templates: {e}")

    # =========================================================================
    # MIGRATION: audit_logs.school_id Spalte hinzufügen (einmalig, idempotent)
    # Für mandantengetrennte Auswertung (Dashboard last_import etc.).
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_logs'")
            ).fetchall()}
            if "school_id" not in cols:
                conn.execute(sa_text("ALTER TABLE audit_logs ADD COLUMN school_id INTEGER"))
                conn.execute(sa_text(
                    "CREATE INDEX IF NOT EXISTS ix_audit_logs_school_id "
                    "ON audit_logs (school_id)"
                ))
                conn.commit()
                print("✅ Migration: audit_logs.school_id Spalte hinzugefügt")
            if "subject_id" not in cols:
                conn.execute(sa_text("ALTER TABLE audit_logs ADD COLUMN subject_id VARCHAR"))
                conn.execute(sa_text(
                    "CREATE INDEX IF NOT EXISTS ix_audit_logs_subject_id "
                    "ON audit_logs (subject_id)"
                ))
                conn.commit()
                print("✅ Migration: audit_logs.subject_id Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration audit_logs.school_id/subject_id: {e}")

    # =========================================================================
    # MIGRATION: schools.sync_ip_allowlist_enabled — einmalig, idempotent
    # Default false (AUS): bestehende Schulen bleiben unverändert erreichbar.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'schools'")
            ).fetchall()}
            if "sync_ip_allowlist_enabled" not in cols:
                conn.execute(sa_text(
                    "ALTER TABLE schools ADD COLUMN sync_ip_allowlist_enabled BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                conn.commit()
                print("✅ Migration: schools.sync_ip_allowlist_enabled Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration sync_ip_allowlist_enabled: {e}")

    # =========================================================================
    # MIGRATION: users.password_changed_at — einmalig, idempotent
    # Entwertet Admin-Tokens, die vor der letzten Passwortänderung ausgestellt wurden.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            cols = {row[0] for row in conn.execute(
                sa_text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
            ).fetchall()}
            if "password_changed_at" not in cols:
                conn.execute(sa_text("ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP"))
                conn.commit()
                print("✅ Migration: users.password_changed_at Spalte hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration password_changed_at: {e}")

    # =========================================================================
    # MIGRATION: denormalisierte school_id auf students/photos/push (für RLS)
    # Spalte hinzufügen (+Index) und bestehende Zeilen EINMALIG backfillen.
    # Neue/geänderte Zeilen pflegen die ORM-Events (models.py) automatisch.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            def _cols(table):
                return {row[0] for row in conn.execute(sa_text(
                    f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'"
                )).fetchall()}

            if "school_id" not in _cols("students"):
                conn.execute(sa_text("ALTER TABLE students ADD COLUMN school_id INTEGER"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_students_school_id ON students (school_id)"))
                conn.execute(sa_text(
                    "UPDATE students SET school_id = c.school_id "
                    "FROM classes c WHERE students.class_id = c.id AND students.school_id IS NULL"
                ))
                conn.commit()
                print("✅ Migration: students.school_id (+Backfill) hinzugefügt")

            if "school_id" not in _cols("photos"):
                conn.execute(sa_text("ALTER TABLE photos ADD COLUMN school_id INTEGER"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_photos_school_id ON photos (school_id)"))
                conn.execute(sa_text(
                    "UPDATE photos SET school_id = s.school_id "
                    "FROM students s WHERE photos.student_id = s.id AND photos.school_id IS NULL"
                ))
                conn.commit()
                print("✅ Migration: photos.school_id (+Backfill) hinzugefügt")

            if "school_id" not in _cols("push_subscriptions"):
                conn.execute(sa_text("ALTER TABLE push_subscriptions ADD COLUMN school_id INTEGER"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_push_subscriptions_school_id ON push_subscriptions (school_id)"))
                conn.execute(sa_text(
                    "UPDATE push_subscriptions SET school_id = s.school_id "
                    "FROM students s WHERE push_subscriptions.student_id = s.id AND push_subscriptions.school_id IS NULL"
                ))
                conn.commit()
                print("✅ Migration: push_subscriptions.school_id (+Backfill) hinzugefügt")
    except Exception as e:
        print(f"⚠️  Migration school_id-Denormalisierung: {e}")

    # =========================================================================
    # INDEX: funktionaler Index für den Schüler-Login-Lookup (lower(last_name)).
    # Der Login vergleicht case-insensitiv exakt — ohne diesen Index wäre es ein
    # Seq-Scan; am 1. Schultag (1000 Logins/10 min) hilft der Index spürbar.
    # =========================================================================
    try:
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            conn.execute(sa_text(
                "CREATE INDEX IF NOT EXISTS ix_students_lower_last_name "
                "ON students (lower(last_name))"
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️  Index ix_students_lower_last_name: {e}")

    # =========================================================================
    # MIGRATION: Altbestand von login_pin/totp_secret verschlüsseln,
    # falls SECRETS_KEY gesetzt ist. Idempotent (bereits verschlüsselte Werte
    # beginnen mit dem Fernet-Präfix 'gAAAAA' und werden übersprungen).
    # =========================================================================
    try:
        from services import secrets_at_rest as _sar
        if _sar.is_enabled():
            from sqlalchemy import text as sa_text
            with engine.connect() as conn:
                rows = conn.execute(sa_text(
                    "SELECT id, login_pin, totp_secret FROM students "
                    "WHERE (login_pin IS NOT NULL AND login_pin NOT LIKE 'gAAAAA%') "
                    "   OR (totp_secret IS NOT NULL AND totp_secret NOT LIKE 'gAAAAA%')"
                )).fetchall()
                migrated = 0
                for sid, pin, totp in rows:
                    updates = {}
                    if pin is not None and not str(pin).startswith("gAAAAA"):
                        updates["login_pin"] = _sar.encrypt(pin)
                    if totp is not None and not str(totp).startswith("gAAAAA"):
                        updates["totp_secret"] = _sar.encrypt(totp)
                    if updates:
                        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                        conn.execute(sa_text(f"UPDATE students SET {set_clause} WHERE id = :id"),
                                     {**updates, "id": sid})
                        migrated += 1
                if migrated:
                    conn.commit()
                    print(f"✅ Migration: {migrated} Schüler-Secrets (login_pin/totp_secret) verschlüsselt")
    except Exception as e:
        print(f"⚠️  Migration Secret-Verschlüsselung: {e}")

    # =========================================================================
    # MIGRATION: PII verschlüsseln — first/last_name, dob, Foto —
    # + Blind-Index-Spalten (last_name_bidx/dob_bidx) für Login/Suche.
    # Läuft IMMER (auch ohne SECRETS_KEY): die Blind-Indexe braucht der Login zwingend,
    # und der dob-Typ muss VARCHAR sein. encrypt() ist ohne Key ein No-Op (Passthrough).
    # Idempotent: Marker ist `last_name_bidx IS NULL` (danach ist die Zeile migriert).
    # =========================================================================
    try:
        from services import secrets_at_rest as _sar
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            scols = {row[0] for row in conn.execute(sa_text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'students'"
            )).fetchall()}
            if "last_name_bidx" not in scols:
                conn.execute(sa_text("ALTER TABLE students ADD COLUMN last_name_bidx VARCHAR"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_students_last_name_bidx ON students (last_name_bidx)"))
            if "dob_bidx" not in scols:
                conn.execute(sa_text("ALTER TABLE students ADD COLUMN dob_bidx VARCHAR"))
                conn.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_students_dob_bidx ON students (dob_bidx)"))
            # dob DATE → VARCHAR, damit verschlüsselte ISO-Strings hineinpassen
            dob_type = conn.execute(sa_text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='students' AND column_name='dob'"
            )).scalar()
            if dob_type == "date":
                conn.execute(sa_text("ALTER TABLE students ALTER COLUMN dob TYPE VARCHAR USING dob::text"))
            conn.commit()

            # PII verschlüsseln + Blind-Indexe backfillen (nur noch-nicht-migrierte Zeilen)
            rows = conn.execute(sa_text(
                "SELECT id, first_name, last_name, dob FROM students WHERE last_name_bidx IS NULL"
            )).fetchall()
            migrated = 0
            for sid, fn, ln, dob in rows:
                # Klartext ermitteln (tolerant: falls schon verschlüsselt → entschlüsseln)
                fn_p, ln_p, dob_p = _sar.decrypt(fn), _sar.decrypt(ln), _sar.decrypt(dob)
                updates = {
                    "first_name": _sar.encrypt(fn_p),
                    "last_name": _sar.encrypt(ln_p),
                    "dob": _sar.encrypt(dob_p),
                    "last_name_bidx": _sar.blind_index(ln_p.strip().lower()) if ln_p else None,
                    "dob_bidx": _sar.blind_index(dob_p) if dob_p else None,   # dob_p = ISO-String
                }
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                conn.execute(sa_text(f"UPDATE students SET {set_clause} WHERE id = :id"),
                             {**updates, "id": sid})
                migrated += 1

            # Foto-Blobs verschlüsseln (nur wenn Key gesetzt; Passthrough sonst)
            prows = conn.execute(sa_text(
                "SELECT id, blob_data FROM photos "
                "WHERE blob_data IS NOT NULL AND blob_data NOT LIKE 'gAAAAA%'"
            )).fetchall()
            pmig = 0
            for pid, blob in prows:
                enc = _sar.encrypt(blob)
                if enc != blob:  # nur schreiben, wenn sich was ändert (Key gesetzt)
                    conn.execute(sa_text("UPDATE photos SET blob_data = :b WHERE id = :id"),
                                 {"b": enc, "id": pid})
                    pmig += 1

            if migrated or pmig:
                conn.commit()
                print(f"✅ Migration: {migrated} Schüler-PII + {pmig} Fotos verschlüsselt/indiziert")
    except Exception as e:
        print(f"⚠️  Migration PII-Verschlüsselung: {e}")

    # =========================================================================
    # RLS: Rolle + Grants + Policies aufsetzen.
    # No-Op wenn RLS_ENABLED != true oder nicht Postgres. Läuft als privilegierter
    # Login-Role (Superuser), daher hier vor dem Seed und vor dem ersten Request.
    # =========================================================================
    try:
        _rls.ensure_rls_setup(engine)
    except Exception as e:
        print(f"⚠️  RLS-Setup: {e}")

    # =========================================================================
    # AUTO-SEED: Default-Schule und Admin-User anlegen (falls DB leer)
    # =========================================================================
    # Wird bei jedem Start geprüft. Erstellt nur Daten, wenn die users-Tabelle
    # leer ist. So funktioniert auch ein frisches Deployment ohne
    # externen DB-Zugang.
    try:
        with Session(engine) as session:
            existing_user = session.exec(select(User).limit(1)).first()
            if not existing_user:
                print("⚠️  Keine User gefunden — erstelle Default-Schule und Admin...")
                
                import bcrypt
                
                # Schule anlegen (falls noch keine existiert)
                existing_school = session.exec(select(School).limit(1)).first()
                if not existing_school:
                    school = School(
                        name="Käthe-Kollwitz-Berufskolleg",
                        path_slug="kkbk",
                        primary_color="#1a73e8"
                    )
                    session.add(school)
                    session.flush()
                    school_id = school.id
                    print(f"  ✅ Schule erstellt: {school.name} (ID: {school_id})")
                else:
                    school_id = existing_school.id
                
                # Admin-User anlegen. Es gibt bewusst kein festes Default-Passwort:
                # Ohne DEFAULT_ADMIN_PASSWORD wird ein zufälliges Passwort generiert
                # und EINMALIG geloggt.
                import secrets as _secrets
                _pw_from_env = os.getenv("DEFAULT_ADMIN_PASSWORD")
                default_pw = _pw_from_env or _secrets.token_urlsafe(12)
                admin_hash = bcrypt.hashpw(default_pw.encode(), bcrypt.gensalt()).decode()

                # School-Admin (für die Default-Schule)
                admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@example.com")
                admin = User(
                    email=admin_email,
                    password_hash=admin_hash,
                    school_id=school_id,
                    role=UserRole.SCHOOL_ADMIN,
                    is_active=True,
                    password_changed_at=datetime.utcnow()
                )
                session.add(admin)

                # Superadmin (schulübergreifend, school_id=NULL)
                superadmin_email = os.getenv("DEFAULT_SUPERADMIN_EMAIL", "superadmin@example.com")
                superadmin_hash = bcrypt.hashpw(default_pw.encode(), bcrypt.gensalt()).decode()
                superadmin = User(
                    email=superadmin_email,
                    password_hash=superadmin_hash,
                    school_id=None,
                    role=UserRole.SUPERADMIN,
                    is_active=True,
                    password_changed_at=datetime.utcnow()
                )
                session.add(superadmin)

                session.commit()
                if _pw_from_env:
                    # Vom Betreiber gesetztes Passwort NICHT ins Log schreiben
                    print(f"  ✅ Admin-User erstellt: {admin_email}")
                    print(f"  ✅ Superadmin erstellt: {superadmin_email}")
                    print("  ⚠️  Passwort = DEFAULT_ADMIN_PASSWORD. SOFORT ÄNDERN!")
                else:
                    # Zufalls-Passwort: einmalig ausgeben, damit man sich einloggen kann
                    print(f"  ✅ Admin-User erstellt: {admin_email} / {default_pw}")
                    print(f"  ✅ Superadmin erstellt: {superadmin_email} / {default_pw}")
                    print("  ⚠️  Zufalls-Passwort generiert (oben). SOFORT ÄNDERN!")
            else:
                print("✅ User-Tabelle hat Einträge — Auto-Seed übersprungen")
                
                # =============================================================
                # ADMIN PASSWORT RESET (per Env-Var auslösbar)
                # =============================================================
                # Falls der Admin-Login nicht funktioniert (z.B. kaputter Hash),
                # kann man RESET_ADMIN_PASSWORD=true setzen.
                #
                # Es werden dabei KEINE User gelöscht: Nur das Passwort der
                # definierten Admin-/Superadmin-Accounts wird zurückgesetzt
                # (bzw. der Account angelegt, falls er fehlt).
                # Bei gesetztem DEFAULT_ADMIN_PASSWORD ist der Vorgang idempotent —
                # ein zweiter Start mit noch gesetzter Var ändert dann nichts mehr.
                # DANACH die Env-Var wieder entfernen!
                if os.getenv("RESET_ADMIN_PASSWORD", "").lower() == "true":
                    import bcrypt
                    import secrets as _secrets

                    _pw_from_env = os.getenv("DEFAULT_ADMIN_PASSWORD")
                    if _pw_from_env:
                        reset_pw = _pw_from_env
                    else:
                        # Das Reset-Passwort wird deterministisch aus JWT_SECRET + Datum
                        # abgeleitet (HMAC), NICHT per token_urlsafe(): Bei mehreren
                        # Uvicorn-Workern durchlaufen alle diesen Block — ein per-Worker-
                        # Zufallswert würde vom jeweils nächsten Worker überschrieben und
                        # das geloggte Passwort wäre sofort ungültig. So erzeugen alle
                        # Worker DENSELBEN Wert, der Idempotenz-Check unten greift, und es
                        # wird genau EIN gültiger Wert geloggt. Von außen bleibt der Wert
                        # unvorhersehbar (HMAC mit Server-Secret).
                        import base64 as _b64
                        import hashlib as _hl
                        import hmac as _hm
                        _seed = f"admin-reset:{datetime.utcnow().date().isoformat()}"
                        _dig = _hm.new(os.getenv("JWT_SECRET", "").encode(),
                                       _seed.encode(), _hl.sha256).digest()
                        reset_pw = _b64.urlsafe_b64encode(_dig)[:16].decode()

                    existing_school = session.exec(select(School).limit(1)).first()
                    targets = [
                        {"email": os.getenv("DEFAULT_ADMIN_EMAIL", "admin@example.com"),
                         "role": "school_admin",
                         "school_id": existing_school.id if existing_school else None},
                        {"email": os.getenv("DEFAULT_SUPERADMIN_EMAIL", "superadmin@example.com"),
                         "role": "superadmin", "school_id": None},
                    ]

                    changed = False
                    for acc in targets:
                        user = session.exec(
                            select(User).where(User.email == acc["email"])
                        ).first()
                        if user is None:
                            # Account fehlt → neu anlegen
                            session.add(User(
                                email=acc["email"],
                                password_hash=bcrypt.hashpw(reset_pw.encode(), bcrypt.gensalt()).decode(),
                                school_id=acc["school_id"],
                                role=acc["role"],
                                is_active=True,
                                password_changed_at=datetime.utcnow()
                            ))
                            changed = True
                        else:
                            # Idempotenz: nur setzen, wenn der Ziel-Wert nicht schon gilt —
                            # so überschreiben sich mehrere Worker nicht gegenseitig.
                            if bcrypt.checkpw(reset_pw.encode(), user.password_hash.encode()):
                                continue
                            user.password_hash = bcrypt.hashpw(reset_pw.encode(), bcrypt.gensalt()).decode()
                            user.password_changed_at = datetime.utcnow()  # entwertet alte Tokens
                            session.add(user)
                            changed = True

                    if changed:
                        session.commit()
                        if _pw_from_env:
                            print("🔑 RESET_ADMIN_PASSWORD: Admin-Passwörter zurückgesetzt (DEFAULT_ADMIN_PASSWORD).")
                        else:
                            print(f"🔑 RESET_ADMIN_PASSWORD: Zufalls-Passwort gesetzt: {reset_pw}")
                        print("⚠️  RESET_ADMIN_PASSWORD env-var JETZT wieder ENTFERNEN!")
                    else:
                        print("✅ RESET_ADMIN_PASSWORD: Passwörter bereits gesetzt — nichts geändert. Bitte Var entfernen.")
    except Exception as e:
        print(f"⚠️  Auto-Seed fehlgeschlagen: {e}")

    # Startup-Lock freigeben — create_all + Migrationen + Seed sind durch;
    # ein evtl. wartender Worker läuft jetzt weiter (findet alles idempotent erledigt).
    if _startup_lock_conn is not None:
        try:
            _lc = _startup_lock_conn.cursor()
            _lc.execute("SELECT pg_advisory_unlock(42000002)")
            _lc.close()
            _startup_lock_conn.commit()
        finally:
            _startup_lock_conn.close()

    # =========================================================================
    # DSGVO: Cleanup-Job starten
    # =========================================================================
    # Löscht Schüler mit deleted_at > 4 Wochen (endgültig)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from sqlmodel import delete
    
    scheduler = AsyncIOScheduler()
    
    def cleanup_deleted_students():
        """
        DSGVO-konforme Datenbegrenzung (nächtlicher Job). Delegiert an
        services.cleanup.run_full_cleanup() — dort liegt die Logik inkl.
        Single-Flight-Advisory-Lock (bei --workers 2 läuft nur EIN Worker) und
        die Testabdeckung.
        """
        from services.cleanup import run_full_cleanup
        try:
            run_full_cleanup(engine)
        except Exception as e:
            # Laut scheitern: Ein stiller Fehler im DSGVO-Cleanup darf nicht
            # unbemerkt bleiben, sonst werden Löschfristen verletzt.
            import traceback
            print("❌❌❌ DSGVO-CLEANUP FEHLGESCHLAGEN — PII wird NICHT gelöscht! ❌❌❌")
            print(f"      {type(e).__name__}: {e}")
            traceback.print_exc()
            raise  # APScheduler protokolliert den Job-Fehler; er darf nicht verschwinden
    
    # Job registrieren: Täglich um 3:00 Uhr
    scheduler.add_job(
        cleanup_deleted_students,
        trigger=CronTrigger(hour=3, minute=0),
        id="dsgvo_cleanup",
        name="DSGVO Student Cleanup",
        replace_existing=True
    )
    
    # Scheduler starten
    scheduler.start()
    print("✅ DSGVO Cleanup-Job gestartet (täglich um 3:00 Uhr)")
    
    yield  # App läuft
    
    # --- SHUTDOWN ---
    scheduler.shutdown()
    print("👋 Fahre Hagen-ID Backend herunter...")


# ============================================================================
# APP INIT
# ============================================================================

# Prozess-Startzeit (für die Uptime-Anzeige auf der Superadmin-Statusseite).
START_TIME = datetime.utcnow()

# SICHERHEIT: API-Doku NUR in der Entwicklung.
# In Produktion legt Swagger/ReDoc/openapi.json sonst die komplette API-Oberfläche
# offen (alle Routen, Schemas, Parameter) — eine kostenlose Landkarte für Angreifer.
_DEBUG = os.getenv("DEBUG", "").lower() == "true"

app = FastAPI(
    lifespan=lifespan,
    title="Hagen-ID API",
    description="Digitaler Schülerausweis für Berufskollegs",
    version="1.0.0",
    docs_url="/docs" if _DEBUG else None,            # Swagger UI (nur DEBUG)
    redoc_url="/redoc" if _DEBUG else None,          # ReDoc (nur DEBUG)
    openapi_url="/openapi.json" if _DEBUG else None  # Schema (nur DEBUG)
)

if not _DEBUG:
    print("🔒 API-Doku (Swagger/ReDoc/openapi.json) in Produktion deaktiviert")


# ============================================================================
# CORS (Cross-Origin Resource Sharing)
# ============================================================================
# WICHTIG: Da die PWA auf dem Handy des Schülers läuft und
# die Admin-App auf einem anderen Host, müssen wir CORS erlauben.

origins_str = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://localhost:5174"
)

# Robust parsing: split by comma, strip whitespace, strip trailing slashes
origins = [
    origin.strip().rstrip("/") 
    for origin in origins_str.split(",") 
    if origin.strip()
]

# Warnung wenn in Produktion noch localhost-Origins aktiv sind
is_debug = os.getenv("DEBUG", "").lower() == "true"
has_only_localhost = all("localhost" in o or "127.0.0.1" in o for o in origins)

if not is_debug and has_only_localhost:
    print("⚠️  WARNUNG: ALLOWED_ORIGINS enthält nur localhost-Adressen!")
    print("   In Produktion ALLOWED_ORIGINS auf die echten Domains setzen!")
    print("   Beispiel: ALLOWED_ORIGINS=https://admin.example.com,https://app.example.com")

print(f"🔒 CORS Config: Allowed Origins: {origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # Erlaubte Domains (aus .env ALLOWED_ORIGINS)
    allow_credentials=True,         # Cookies erlauben (für HttpOnly JWT-Cookie)
    # Explizite Methoden statt "*" — verhindert unerwartete HTTP-Verben
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # Explizite Header statt "*" — nur was Frontend tatsächlich sendet
    allow_headers=[
        "Authorization",      # Bearer Token (JWT)
        "Content-Type",       # JSON-Requests
        "X-Session-Token",    # Highlander Session-Token
        "X-Requested-With",   # AJAX-Marker (manche Libraries)
        "X-API-Key",          # API-Key für Sync-Endpoints
    ],
    max_age=3600,  # Preflight-Antworten 1h cachen → weniger OPTIONS-Requests
)


# ============================================================================
# SECURITY HEADERS MIDDLEWARE
# ============================================================================
# Die Frontend-Nginx-Container setzen eigene Security-Headers.
# Für API-Responses brauchen wir sie ebenfalls (z.B. /check HTML-Seite).

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # HSTS nur setzen wenn nicht localhost (Entwicklung)
        if request.url.hostname not in ("localhost", "127.0.0.1"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # CSP für die vom Backend ausgelieferten HTML-Seiten (/check, /verify-cert).
        # Diese Seiten sind self-contained: Inline-<style> + ein data:-Foto, KEIN JavaScript.
        # default-src 'none' ⇒ selbst eine (trotz Escaping) injizierte <script> liefe nicht.
        # Swagger/ReDoc (nur in DEBUG) ausnehmen — deren Assets bräuchten eine laxe CSP.
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html") and not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
        return response

app.add_middleware(SecurityHeadersMiddleware)


# ============================================================================
# ROUTER EINBINDEN
# ============================================================================

# Admin-Routes
from routes.admin import get_session as admin_get_session
app.include_router(admin_routes.router)
app.dependency_overrides[admin_get_session] = get_session

# Auth-Routes
from routes.auth import get_session as auth_get_session
app.include_router(auth_routes.router)
app.dependency_overrides[auth_get_session] = get_session

# PWA-Routes
from routes.pwa import get_session as pwa_get_session
app.include_router(pwa_routes.router)
app.dependency_overrides[pwa_get_session] = get_session

# Schools-Routes (Superadmin only)
from routes.schools import get_session as schools_get_session
app.include_router(schools_routes.router)
app.dependency_overrides[schools_get_session] = get_session

# Sync-Routes (API-Key basiert, für externe Tools)
from routes.sync import get_session as sync_get_session
app.include_router(sync_routes.router)
app.dependency_overrides[sync_get_session] = get_session


# ============================================================================
# HEALTH-CHECK ROUTES
# ============================================================================

@app.get("/", tags=["Health"])
def read_root():
    """
    Health-Check Endpoint.
    Gibt Status und Version zurück.
    """
    return {
        "status": "online",
        "system": "Hagen-ID Backend",
        "version": "1.0.0"
    }


@app.get("/health", tags=["Health"])
def health_check():
    """
    Detaillierter Health-Check für Monitoring.
    Prüft DB-Verbindung.
    """
    try:
        with Session(engine) as session:
            # Einfache Query um DB zu testen
            session.exec(select(School).limit(1)).first()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "version": "1.0.0"
    }


# ============================================================================
# QR-CODE VERIFIZIERUNG (öffentlich zugänglich für Kontrolleure)
# ============================================================================

@app.get("/check", tags=["Verification"])
def verify_qr_page(
    request: Request,
    p: str | None = None,  # Payload: student_id:totp:session_hash:timestamp
    session: Session = Depends(get_session)
):
    """
    Öffentliche Verifizierungsseite für QR-Codes.
    
    Wird aufgerufen wenn jemand den QR-Code mit der Handy-Kamera scannt.
    Gibt eine HTML-Seite mit dem Ergebnis zurück.
    
    URL-Format: /check?p=student_id:totp_code:session_hash:timestamp:signature
    """
    from fastapi.responses import HTMLResponse
    from fastapi import Request
    import time
    import uuid
    
    from services.auth import verify_totp_code
    from services.crypto import verify_qr_code
    from models import Student, Class, School, Photo, AuditLog
    
    # Hilfsfunktion für HTML-Response
    def html_response(valid: bool, title: str, message: str,
                      student_name: str | None = None, class_name: str | None = None,
                      school_name: str | None = None, photo_url: str | None = None):
        # HTML-Escaping aller dynamischen Werte (XSS-Schutz auf öffentlicher Seite).
        # Namen/Klassen/Schulen stammen aus dem CSV-Import und sind nicht vertrauenswürdig.
        from html import escape as _escape
        student_name = _escape(student_name) if student_name else student_name
        class_name = _escape(class_name) if class_name else class_name
        school_name = _escape(school_name) if school_name else school_name
        photo_url = _escape(photo_url, quote=True) if photo_url else photo_url

        # Farben basierend auf Validität
        bg_color = "#10b981" if valid else "#ef4444"  # Grün oder Rot
        icon = "✓" if valid else "✗"
        
        # Foto-HTML (nur wenn vorhanden und gültig)
        photo_html = ""
        if valid and photo_url:
            photo_html = f'''
            <div style="width:120px;height:150px;border-radius:12px;overflow:hidden;
                        border:3px solid white;margin:0 auto 1rem;">
                <img src="{photo_url}" style="width:100%;height:100%;object-fit:cover;" />
            </div>
            '''
        
        # Info-HTML (nur wenn gültig)
        info_html = ""
        if valid and student_name:
            info_html = f'''
            <div style="font-size:1.5rem;font-weight:bold;margin-bottom:0.5rem;">
                {student_name}
            </div>
            <div style="opacity:0.9;margin-bottom:0.5rem;">
                {class_name or ""}
            </div>
            <div style="opacity:0.7;font-size:0.9rem;">
                {school_name or ""}
            </div>
            '''
        
        html = f'''<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Hagen-ID</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: {bg_color};
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            text-align: center;
            padding: 2rem;
        }}
        .card {{
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 2rem;
            max-width: 400px;
            width: 100%;
        }}
        .icon {{
            font-size: 4rem;
            margin-bottom: 1rem;
        }}
        .title {{
            font-size: 1.2rem;
            opacity: 0.9;
            margin-bottom: 1.5rem;
        }}
        .message {{
            margin-top: 1.5rem;
            padding-top: 1rem;
            border-top: 1px solid rgba(255,255,255,0.2);
            font-size: 0.9rem;
            opacity: 0.8;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <div class="title">{title}</div>
        {photo_html}
        {info_html}
        <div class="message">{message}</div>
    </div>
</body>
</html>'''
        return HTMLResponse(content=html)
    
    # Kein Payload?
    if not p:
        return html_response(
            valid=False,
            title="Ungültiger Link",
            message="Kein QR-Code-Daten übermittelt."
        )
    
    # Payload parsen (neues Format mit Signatur)
    parts = p.split(":")
    if len(parts) != 5:
        return html_response(
            valid=False,
            title="Ungültiges Format",
            message="Der QR-Code hat ein ungültiges Format. Bitte App aktualisieren."
        )
    
    student_id_str, totp_code, session_hash_prefix, timestamp_str, signature = parts
    
    # Student laden
    try:
        student_uuid = uuid.UUID(student_id_str)
    except ValueError:
        return html_response(
            valid=False,
            title="Ungültige ID",
            message="Die ID ist ungültig."
        )
    
    student = session.get(Student, student_uuid)
    if not student:
        return html_response(
            valid=False,
            title="Nicht gefunden",
            message="Person wurde nicht gefunden."
        )
    
    student_name = f"{student.first_name} {student.last_name}"
    
    if not student.is_active:
        return html_response(
            valid=False,
            title="Deaktiviert",
            message="Dieser Ausweis wurde deaktiviert.",
        )
    
    if student.deleted_at:
        return html_response(
            valid=False,
            title="Gelöscht",
            message="Diese Person ist nicht mehr registriert."
        )

    # Schul-Kontext + (nur bei aktivem Trace) die IP des Verifizierers für das Audit.
    # /check wird von einem Kontrolleur gescannt — ein Dritt-Zugriff, kein Selbst-Zugriff.
    from routes.auth import get_client_ip
    from services import audit
    _sc = session.get(Class, student.class_id) if student.class_id else None
    _school_id = _sc.school_id if _sc else None
    _verifier_ip = audit.ip_if_traced(
        session, school_id=_school_id, subject_id=str(student.id), ip=get_client_ip(request)
    )

    # TOTP prüfen
    if not verify_totp_code(student.totp_secret, totp_code):
        return html_response(
            valid=False,
            title="Abgelaufen",
            message="Der QR-Code ist abgelaufen. Bitte neuen Code anzeigen lassen.",
        )
    
    # Session-Hash prüfen (Highlander)
    stored_hash = student.active_session_token_hash
    if stored_hash is None:
        return html_response(
            valid=False,
            title="Nicht angemeldet",
            message="Keine aktive Anmeldung. Bitte erneut einloggen.",
        )
    
    stored_hash_prefix = stored_hash[:8].lower()
    received_prefix = session_hash_prefix.lower()

    # Konstantzeit-Vergleich (Timing-Hygiene bei Geheimnis-Vergleichen)
    import hmac as _hmac
    if not _hmac.compare_digest(stored_hash_prefix, received_prefix):
        return html_response(
            valid=False,
            title="Anderes Gerät",
            message="Dieser Ausweis wurde auf einem anderen Gerät aktiviert.",
        )
    
    # Timestamp prüfen
    qr_timestamp = None
    try:
        qr_timestamp = int(timestamp_str)
        current_timestamp = int(time.time()) // 30
        if abs(current_timestamp - qr_timestamp) > 1:
            return html_response(
                valid=False,
                title="Abgelaufen",
                message="Der QR-Code ist abgelaufen. Bitte App öffnen."
            )
    except ValueError:
        pass

    # ------------------------------------------------------------------
    # SIGNATUR VERIFIZIEREN (ECDSA P-256)
    # ------------------------------------------------------------------
    # Der eigentliche Fälschungsschutz: Die PWA signiert den Payload
    # "student_id:totp:session_hash:timestamp" offline mit ihrem privaten
    # Geräte-Schlüssel. Der öffentliche Schlüssel liegt in student.id_public_key.
    # Ohne diese Prüfung könnte jeder mit gültigem TOTP+Session einen QR bauen.

    # Nicht signierte / Legacy-Payloads (oder Student ohne Key) ablehnen.
    if (signature in ("SIGN_CLIENT_SIDE", "NO_KEY", "ERROR", "CACHED")
            or not student.id_public_key
            or qr_timestamp is None):
        return html_response(
            valid=False,
            title="Nicht verifizierbar",
            message="Dieser Ausweis ist nicht signiert. Bitte in der App neu einloggen.",
        )

    # WICHTIG: Über den ROH-Timestamp aus dem QR verifizieren (exakt das, was der
    # Client signiert hat) — nicht über int(), das den String kanonisieren würde.
    if not verify_qr_code(
        student_id_str, totp_code, session_hash_prefix,
        timestamp_str, signature, student.id_public_key
    ):
        audit.record(session, "id_verified", subject_id=str(student.id),
                     school_id=_school_id, target_table="students",
                     result="signature_invalid", ip=_verifier_ip)
        return html_response(
            valid=False,
            title="Signatur ungültig",
            message="Dieser QR-Code ist nicht echt.",
        )

    # Alles OK! Zusatzinfos laden
    class_name = None
    school_name = None
    
    if student.class_id:
        student_class = session.get(Class, student.class_id)
        if student_class:
            class_name = student_class.name
            school = session.get(School, student_class.school_id)
            school_name = school.name if school else None
    
    # Foto-URL (jetzt Data URI)
    photo = session.exec(
        select(Photo).where(Photo.student_id == student.id)
    ).first()
    
    photo_url = None
    if photo and photo.blob_data:
        if photo.blob_data.startswith("data:image"):
            photo_url = photo.blob_data
        else:
            # blob_data enthält den reinen Base64-String ohne Data-URI-Präfix.
            # MIME-Type JPEG als Default; Browser sind hier tolerant.
            photo_url = f"data:image/jpeg;base64,{photo.blob_data}"
    
    audit.record(
        session, "id_verified", subject_id=str(student.id), school_id=_school_id,
        target_table="students", result="valid", ip=_verifier_ip,
        extra={"card_type": "teacher" if (class_name or "").lower() == "lehrerkollegium" else "student"},
    )
    return html_response(
        valid=True,
        title="Ausweis gültig",
        message="Dieser digitale Ausweis ist gültig.",
        student_name=student_name,
        class_name=class_name,
        school_name=school_name,
        photo_url=photo_url
    )


# ============================================================================
# SCHULBESCHEINIGUNG: Dauerhafte QR-Verifizierung (öffentlich)
# ============================================================================

@app.get("/verify-cert", tags=["Verification"])
def verify_certificate_page(
    request: Request,
    c: str | None = None,  # Dauerhafter Verifizierungs-Token aus dem QR-Code
    session: Session = Depends(get_session)
):
    """
    Öffentliche Verifizierungsseite für die Schulbescheinigung.

    Anders als /check (rotierender Ausweis-QR) nutzt diese Seite einen
    DAUERHAFTEN Token und zeigt den aktuellen Live-Status: ist die Person
    aktuell aktiv eingeschrieben? Es werden nur Name, Klasse und Schule
    angezeigt (kein Foto, kein Geburtsdatum).
    """
    from fastapi.responses import HTMLResponse
    from datetime import date as _date
    from models import Student, Class, School

    def render(valid: bool, title: str, message: str,
               name: str | None = None, class_name: str | None = None,
               school_name: str | None = None, valid_until: str | None = None):
        # HTML-Escaping aller dynamischen Werte (XSS-Schutz auf öffentlicher Seite).
        # Namen/Klassen/Schulen stammen aus dem CSV-Import und sind nicht vertrauenswürdig.
        from html import escape as _escape
        bg_color = "#10b981" if valid else "#ef4444"
        icon = "✓" if valid else "✗"
        checked = _date.today().strftime("%d.%m.%Y")

        info_html = ""
        if name:
            info_html += f'<div style="font-size:1.5rem;font-weight:bold;margin-bottom:0.5rem;">{_escape(name)}</div>'
        if class_name:
            info_html += f'<div style="opacity:0.9;margin-bottom:0.25rem;">Klasse {_escape(class_name)}</div>'
        if school_name:
            info_html += f'<div style="opacity:0.75;font-size:0.9rem;">{_escape(school_name)}</div>'
        if valid and valid_until:
            info_html += f'<div style="opacity:0.85;font-size:0.9rem;margin-top:0.5rem;">eingeschrieben bis {_escape(valid_until)}</div>'

        html = f'''<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Hagen-ID</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: {bg_color}; min-height: 100vh; display: flex;
            align-items: center; justify-content: center; color: white;
            text-align: center; padding: 2rem;
        }}
        .card {{
            background: rgba(255,255,255,0.1); backdrop-filter: blur(10px);
            border-radius: 20px; padding: 2rem; max-width: 400px; width: 100%;
        }}
        .icon {{ font-size: 4rem; margin-bottom: 1rem; }}
        .title {{ font-size: 1.2rem; opacity: 0.9; margin-bottom: 1.5rem; }}
        .message {{
            margin-top: 1.5rem; padding-top: 1rem;
            border-top: 1px solid rgba(255,255,255,0.2);
            font-size: 0.9rem; opacity: 0.8;
        }}
        .checked {{ margin-top: 1rem; font-size: 0.8rem; opacity: 0.6; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <div class="title">{title}</div>
        {info_html}
        <div class="message">{message}</div>
        <div class="checked">Geprüft am {checked}</div>
    </div>
</body>
</html>'''
        return HTMLResponse(content=html)

    if not c:
        return render(False, "Ungültiger Link", "Kein Verifizierungs-Code übermittelt.")

    student = session.exec(
        select(Student).where(Student.cert_verify_token == c)
    ).first()
    from services import audit
    if not student:
        audit.record(session, "cert_verify_failed", result="unknown_token")
        return render(False, "Nicht gefunden",
                      "Zu diesem Code wurde keine gültige Bescheinigung gefunden.")

    name = f"{student.first_name} {student.last_name}"

    # Klasse, Schule und Gültigkeit ermitteln
    class_name = None
    school_name = None
    valid_until = None
    _school_id = None
    if student.class_id:
        student_class = session.get(Class, student.class_id)
        if student_class:
            class_name = student_class.name
            _school_id = student_class.school_id
            vu = student.validity_end_override or student_class.validity_end
            valid_until = vu.strftime("%d.%m.%Y") if vu else None
            school = session.get(School, student_class.school_id)
            school_name = school.name if school else None

    # IP des Verifizierers nur bei aktivem Trace (Dritt-Zugriff, kein Selbst-Zugriff).
    from routes.auth import get_client_ip
    _verifier_ip = audit.ip_if_traced(
        session, school_id=_school_id, subject_id=str(student.id), ip=get_client_ip(request)
    )

    # Lehrkräfte (Dienstbescheinigung) bekommen ein eigenes Status-Wording —
    # "eingeschrieben" wäre für Personal schlicht falsch.
    _is_teacher = (class_name or "").strip().lower() == "lehrerkollegium"
    if _is_teacher:
        class_name = None  # "Klasse: Lehrerkollegium" auf der Seite unterdrücken

    # Live-Status prüfen (automatischer "Widerruf" über den Status)
    if student.deleted_at is not None:
        audit.record(session, "cert_verified", subject_id=str(student.id),
                     school_id=_school_id, target_table="students",
                     result="deleted", ip=_verifier_ip)
        return render(False,
                      "Nicht mehr tätig" if _is_teacher else "Nicht mehr eingeschrieben",
                      "Diese Person ist nicht mehr an der Schule registriert.",
                      name, class_name, school_name)
    if not student.is_active:
        audit.record(session, "cert_verified", subject_id=str(student.id),
                     school_id=_school_id, target_table="students",
                     result="inactive", ip=_verifier_ip)
        return render(False, "Nicht aktiv",
                      "Diese Person ist derzeit nicht aktiv an der Schule tätig."
                      if _is_teacher else
                      "Diese Person ist derzeit nicht aktiv eingeschrieben.",
                      name, class_name, school_name)

    audit.record(session, "cert_verified", subject_id=str(student.id),
                 school_id=_school_id, target_table="students",
                 result="active", ip=_verifier_ip)
    if _is_teacher:
        return render(True, "Aktiv als Lehrkraft tätig",
                      "Diese Person ist aktuell als Lehrkraft an der Schule tätig.",
                      name, class_name, school_name, valid_until)
    return render(True, "Aktiv eingeschrieben",
                  "Diese Person ist aktuell aktiv an der Schule eingeschrieben.",
                  name, class_name, school_name, valid_until)


# ============================================================================
# DEVELOPMENT ROUTES (nur wenn DEBUG=true in .env)
# ============================================================================

# SICHERHEIT: Der /seed Endpoint existiert NUR in der Entwicklungsumgebung.
# In Produktion (ohne DEBUG=true) ist der Endpoint nicht registriert.
if os.getenv("DEBUG", "").lower() == "true":
    @app.post("/seed", tags=["Development"])
    def seed_data(session: Session = Depends(get_session)):
        """
        Erstellt Test-Daten für Entwicklung.
        
        WARNUNG: Nur für Entwicklung! In Produktion deaktivieren.
        """
        # Prüfen ob schon Daten existieren
        if session.exec(select(School)).first():
            return {"msg": "Daten existieren bereits. Seed übersprungen."}
        
        # ========== SCHULE ==========
        school = School(
            name="Käthe-Kollwitz-Berufskolleg",
            path_slug="kkbk",
            primary_color="#1a73e8"
        )
        session.add(school)
        session.flush()  # ID generieren
        
        # ========== LEVELS ==========
        levels = [
            Level(school_id=school.id, name="Sozialpädagogik"),
            Level(school_id=school.id, name="Gesundheit & Soziales"),
            Level(school_id=school.id, name="Gestaltung"),
        ]
        for level in levels:
            session.add(level)
        session.flush()
        
        # ========== KLASSEN ==========
        from datetime import date
        
        classes = [
            Class(school_id=school.id, level_id=levels[0].id, name="E1A", validity_end=date(2025, 7, 31)),
            Class(school_id=school.id, level_id=levels[0].id, name="E1B", validity_end=date(2025, 7, 31)),
            Class(school_id=school.id, level_id=levels[2].id, name="GTA11", validity_end=date(2025, 7, 31)),
            Class(school_id=school.id, level_id=None, name="Lehrerkollegium", validity_end=date(2030, 7, 31)),
        ]
        for cls in classes:
            session.add(cls)
        session.flush()
        
        # ========== TEST-SCHÜLER ==========
        import pyotp
        
        test_students = [
            Student(
                school_internal_id="10001",
                first_name="Max",
                last_name="Mustermann",
                dob=date(2005, 3, 15),
                class_id=classes[0].id,
                generated_login_code="MMustermann7x3k",
                totp_secret=pyotp.random_base32(),
                is_active=True
            ),
            Student(
                school_internal_id="10002",
                first_name="Erika",
                last_name="Musterfrau",
                dob=date(2004, 8, 22),
                class_id=classes[0].id,
                generated_login_code="EMusterfrau9p2m",
                totp_secret=pyotp.random_base32(),
                is_active=True
            ),
        ]
        for student in test_students:
            session.add(student)
        
        # ========== ADMIN-USER ==========
        import bcrypt
        
        admin_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
        admin = User(
            email="admin@example.com",
            password_hash=admin_hash,
            school_id=school.id,
            role=UserRole.SCHOOL_ADMIN,
            is_active=True,
            password_changed_at=datetime.utcnow()
        )
        session.add(admin)

        session.commit()
        
        return {
            "msg": "Seed erfolgreich",
            "created": {
                "schools": 1,
                "levels": len(levels),
                "classes": len(classes),
                "students": len(test_students),
                "admins": 1
            }
        }