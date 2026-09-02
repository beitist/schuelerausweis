"""
RLS-Choke-Point — setzt pro Request den Mandanten auf der DB-Verbindung.

Modell: Die App verbindet als Superuser `hagenid`. Für Mandanten-Queries wechselt
sie per `SET ROLE hagenid_app` in eine eingeschränkte Rolle (RLS greift dann)
und setzt `app.current_school_id`; die Policies filtern darüber. Superadmin/öffentlich → **kein**
SET ROLE → Superuser → RLS-Bypass.

WARUM session.info statt ContextVar:
FastAPI führt Dependency und Endpoint in GETRENNTEN `run_in_threadpool`-Calls mit je kopiertem
Context aus — eine in der Auth-Dependency gesetzte ContextVar ist im Endpoint **unsichtbar**.
Die Folge wäre fail-open: nach dem ersten Mid-Request-`commit()` liefe alles wieder als
Superuser → RLS umgangen. Lösung:
  - Der Mandant wird an **`session.info`** gehängt — dieselbe Session-Instanz wird von FastAPI
    (dependency-cache) in Dependency UND Endpoint injiziert, also ein GETEILTES Objekt (kein
    Context-Copy-Problem).
  - Ein **`after_begin`**-Session-Event appliziert den Mandanten bei JEDEM Transaktionsbeginn
    (auch bei der neuen Transaktion NACH einem commit) → überlebt Mid-Request-Commits.
  - `set_tenant()` appliziert zusätzlich SOFORT auf der laufenden Transaktion (die während der
    Auth-Phase schon begonnen wurde; `after_begin` würde sonst erst beim nächsten Begin greifen).
  - Ein **checkin**-Event setzt die Pool-Connection beim Zurückgeben zurück (Hygiene).

Alles hinter `RLS_ENABLED` und nur auf Postgres — aus/SQLite = komplett No-Op.
"""

import os

from sqlalchemy import event
from sqlmodel import Session

RLS_ENABLED = os.getenv("RLS_ENABLED", "false").lower() == "true"
APP_ROLE = "hagenid_app"

_INFO_KEY = "rls_school_id"
_UNSET = object()


def _apply(connection, school_id) -> None:
    """SET ROLE + Mandanten-GUC (oder Reset) auf einer SQLAlchemy-Connection."""
    if connection.dialect.name != "postgresql":
        return
    if school_id is None:
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql("RESET app.current_school_id")
    else:
        connection.exec_driver_sql(f"SET ROLE {APP_ROLE}")
        connection.exec_driver_sql(
            "SELECT set_config('app.current_school_id', %s, false)", (str(school_id),)
        )


def set_tenant(session, school_id) -> None:
    """
    Von den Auth-Dependencies aufgerufen, sobald die Schule feststeht.
    Bindet den Mandanten an die Session (für `after_begin`) UND appliziert ihn sofort auf
    der bereits laufenden Transaktion (deren erste Query lief in der Auth-Phase privilegiert
    — korrekt, sie ermittelt ja erst die Identität).
    """
    if not RLS_ENABLED:
        return
    session.info[_INFO_KEY] = school_id
    _apply(session.connection(), school_id)


def clear_tenant(session) -> None:
    """Superadmin/privilegiert: kein Mandant → Superuser → RLS-Bypass."""
    if not RLS_ENABLED:
        return
    session.info[_INFO_KEY] = None
    _apply(session.connection(), None)


def _after_begin(session, transaction, connection):
    """Bei JEDEM Transaktionsbeginn (auch nach commit) den Mandanten (re-)applizieren."""
    sid = session.info.get(_INFO_KEY, _UNSET)
    if sid is _UNSET:
        return  # Session ohne Auth (public/Startup/Seed) → privilegiert lassen
    _apply(connection, sid)


def _on_checkin(dbapi_conn, record):
    """Pool-Connection beim Zurückgeben zurücksetzen (Hygiene)."""
    try:
        cur = dbapi_conn.cursor()
        cur.execute("RESET ROLE")
        cur.execute("RESET app.current_school_id")
        cur.close()
        # WICHTIG: psycopg2 öffnet für die RESETs eine Transaktion. Ohne commit ginge die
        # Connection MIT offener Transaktion in den Pool zurück → beim nächsten Checkout
        # scheitert `pool_pre_ping` mit "set_session cannot be used inside a transaction"
        # (autocommit lässt sich in einer offenen Transaktion nicht setzen) → Startup-Crash-Loop.
        # commit() macht die RESETs dauerhaft (Session-GUCs) UND schließt die Transaktion.
        dbapi_conn.commit()
    except Exception:
        record.invalidate()


def ensure_rls_setup(engine) -> None:
    """
    Legt die eingeschränkte Rolle `hagenid_app` an, vergibt die Grants
    und aktiviert RLS + Mandanten-Policies. Idempotent, nur Postgres, nur wenn RLS_ENABLED.
    Läuft beim Startup als der (privilegierte) Login-Role — VOR dem ersten Request.

    Die Tenant-Tabellen werden aus dem SQLModel-Metadata abgeleitet: **jede** Tabelle mit
    einer `school_id`-Spalte bekommt die Policy automatisch (`schools` filtert auf die eigene
    `id`). So kann keine künftig hinzugefügte Tenant-Tabelle die Isolation still verpassen —
    Mechanismus statt Disziplin.
    """
    if not RLS_ENABLED or engine.dialect.name != "postgresql":
        return

    from sqlalchemy import text as _text
    from sqlmodel import SQLModel
    import models  # noqa: F401 — registriert alle Tabellen im Metadata

    tenant_tables = [t.name for t in SQLModel.metadata.sorted_tables
                     if "school_id" in t.columns]

    def _policy(table: str, col: str) -> list:
        return [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_iso ON {table}",
            f"CREATE POLICY tenant_iso ON {table} "
            f"USING ({col} = current_setting('app.current_school_id', true)::int) "
            f"WITH CHECK ({col} = current_setting('app.current_school_id', true)::int)",
        ]

    stmts = [
        # Eingeschränkte Rolle (kein LOGIN, kein Superuser) — idempotent
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}') "
        f"THEN CREATE ROLE {APP_ROLE} NOSUPERUSER NOLOGIN; END IF; END $$",
        # Dem App-Login-Role erlauben, per SET ROLE in die Rolle zu wechseln
        f"GRANT {APP_ROLE} TO CURRENT_USER",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ]
    stmts += _policy("schools", "id")          # Mandant = die eigene Schul-id
    for t in tenant_tables:
        stmts += _policy(t, "school_id")

    with engine.begin() as conn:
        for s in stmts:
            conn.execute(_text(s))

    print(f"✅ RLS aktiv: Rolle {APP_ROLE} + Policies auf schools + "
          f"{len(tenant_tables)} Tenant-Tabellen ({', '.join(tenant_tables)})")


def register(engine) -> None:
    """
    Hängt `after_begin` (an die Session-Klasse) und `checkin` (an die Engine) an.
    Idempotent; No-Op, wenn RLS aus ist oder die Engine nicht Postgres ist.
    """
    if not RLS_ENABLED or engine.dialect.name != "postgresql":
        return
    if not event.contains(Session, "after_begin", _after_begin):
        event.listen(Session, "after_begin", _after_begin)
    if not event.contains(engine, "checkin", _on_checkin):
        event.listen(engine, "checkin", _on_checkin)


def unregister(engine) -> None:
    """Events wieder lösen (v.a. für Tests, damit sie nicht zwischen Suites leaken)."""
    if event.contains(Session, "after_begin", _after_begin):
        event.remove(Session, "after_begin", _after_begin)
    if event.contains(engine, "checkin", _on_checkin):
        event.remove(engine, "checkin", _on_checkin)
