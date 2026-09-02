"""
Routen-Inventar-Test — das mechanische Netz gegen „Routen, an die niemand gedacht hat".

Hintergrund: `authz.py` ist eine Bibliothek, die man aufrufen MUSS — kein
Choke-Point. Der Schutz jeder Route beruht also darauf, dass niemand den
Aufruf vergisst.

Dieser Test erzwingt Disziplin per Mechanismus: JEDE Route der App muss hier bewusst einer
Zugriffs-Kategorie zugeordnet sein. Wer eine neue Route hinzufügt, ohne sie hier einzutragen,
bekommt einen roten Build — und muss sich damit aktiv Gedanken über deren Absicherung machen.

Kategorien:
- public   : bewusst ohne Auth erreichbar (öffentliche Verifizierungs-/Info-Seiten)
- auth     : Login/Logout/Session-Identität (die Authentifizierung selbst)
- admin    : erfordert Admin-/Superadmin-Login (school-scoped oder superadmin-only)
- student  : erfordert Schüler-JWT (PWA, Self-Access)
- apikey   : erfordert Schul-API-Key (Sync-Kanal)

Ausführen:  cd backend && python -m pytest tests/test_route_inventory.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.routing import APIRoute
import main


ALLOWED_CATEGORIES = {"public", "auth", "admin", "student", "apikey"}

# ---------------------------------------------------------------------------
# REGISTRY: (HTTP-Methode, Pfad) -> Kategorie
# Beim Hinzufügen einer Route MUSS hier eine Zeile ergänzt werden, sonst schlägt
# der Test unten fehl. Das ist Absicht.
# ---------------------------------------------------------------------------
ROUTE_CLASSIFICATION = {
    # --- public (kein Login nötig) ---
    ("GET", "/"): "public",
    ("GET", "/health"): "public",
    ("GET", "/check"): "public",
    ("GET", "/verify-cert"): "public",
    ("GET", "/schools/public"): "public",
    ("GET", "/schools/public/{slug}"): "public",

    # --- auth (Authentifizierung selbst) ---
    ("POST", "/auth/admin/login"): "auth",
    ("POST", "/auth/admin/logout"): "auth",
    ("POST", "/auth/student/login"): "auth",
    ("POST", "/auth/student/logout"): "auth",
    ("GET", "/auth/me"): "auth",

    # --- admin / superadmin ---
    ("GET", "/admin/audit-logs"): "admin",
    ("GET", "/admin/classes"): "admin",
    ("GET", "/admin/classes/{class_id}/print-data"): "admin",
    ("GET", "/admin/classes/{class_id}/export.csv"): "admin",
    ("GET", "/admin/print/qr.png"): "admin",
    ("GET", "/admin/dashboard"): "admin",
    ("POST", "/admin/import/apply"): "admin",
    ("POST", "/admin/import/preview"): "admin",
    ("GET", "/admin/students"): "admin",
    ("POST", "/admin/students"): "admin",
    ("GET", "/admin/students/{student_id}"): "admin",
    ("DELETE", "/admin/students/{student_id}"): "admin",
    ("PATCH", "/admin/students/{student_id}/activate"): "admin",
    ("PATCH", "/admin/students/{student_id}/deactivate"): "admin",
    ("GET", "/admin/students/{student_id}/photo"): "admin",
    ("POST", "/admin/students/{student_id}/photo"): "admin",
    ("PATCH", "/admin/students/{student_id}/pin"): "admin",
    ("GET", "/admin/traces"): "admin",
    ("POST", "/admin/traces"): "admin",
    ("DELETE", "/admin/traces/{trace_id}"): "admin",
    ("GET", "/admin/system/status"): "admin",
    ("GET", "/admin/settings/rate-limit"): "admin",
    ("PUT", "/admin/settings/rate-limit"): "admin",
    ("POST", "/auth/admin/create"): "admin",
    ("GET", "/auth/admin/users"): "admin",
    ("GET", "/auth/admin/users/{user_id}"): "admin",
    ("PATCH", "/auth/admin/users/{user_id}"): "admin",
    ("DELETE", "/auth/admin/users/{user_id}"): "admin",
    ("GET", "/schools"): "admin",
    ("POST", "/schools"): "admin",
    ("GET", "/schools/{school_id}"): "admin",
    ("PUT", "/schools/{school_id}"): "admin",
    ("DELETE", "/schools/{school_id}"): "admin",
    ("GET", "/schools/{school_id}/api-key"): "admin",
    ("POST", "/schools/{school_id}/api-key"): "admin",
    ("DELETE", "/schools/{school_id}/api-key"): "admin",
    ("POST", "/schools/{school_id}/logo/refresh"): "admin",
    ("GET", "/schools/{school_id}/settings"): "admin",
    ("PUT", "/schools/{school_id}/settings"): "admin",
    ("GET", "/schools/{school_id}/sync-allowlist"): "admin",
    ("POST", "/schools/{school_id}/sync-allowlist"): "admin",
    ("PUT", "/schools/{school_id}/sync-allowlist"): "admin",
    ("DELETE", "/schools/{school_id}/sync-allowlist/{entry_id}"): "admin",

    # --- student (PWA, Schüler-JWT) ---
    ("GET", "/pwa/me"): "student",
    ("POST", "/pwa/photo"): "student",
    ("GET", "/pwa/photo/{student_id}"): "student",
    ("POST", "/pwa/push/subscribe"): "student",
    ("DELETE", "/pwa/push/unsubscribe"): "student",
    ("POST", "/pwa/register-key"): "student",
    ("GET", "/pwa/schulbescheinigung.pdf"): "student",

    # --- apikey (Sync-Kanal, Schul-API-Key) ---
    ("GET", "/api/sync/manifest"): "apikey",
    ("POST", "/api/sync/new"): "apikey",
    ("POST", "/api/sync/change"): "apikey",
    ("POST", "/api/sync/suspend"): "apikey",
}


def _actual_routes() -> set:
    """Alle (Methode, Pfad)-Paare der App, ohne HEAD/OPTIONS (automatisch von Starlette)."""
    pairs = set()
    for r in main.app.routes:
        if not isinstance(r, APIRoute):
            continue  # Mounts/Static/Docs ignorieren
        for method in r.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            pairs.add((method, r.path))
    return pairs


def test_every_route_is_classified():
    """Jede reale Route MUSS im Registry stehen — neue, nicht klassifizierte Route → rot."""
    actual = _actual_routes()
    unclassified = actual - set(ROUTE_CLASSIFICATION)
    assert not unclassified, (
        "Nicht klassifizierte Route(n) gefunden. Bitte in ROUTE_CLASSIFICATION "
        "(tests/test_route_inventory.py) eintragen und die Absicherung prüfen:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(unclassified))
    )


def test_no_stale_registry_entries():
    """Kein verwaister Registry-Eintrag (Route entfernt, Eintrag vergessen)."""
    actual = _actual_routes()
    stale = set(ROUTE_CLASSIFICATION) - actual
    assert not stale, (
        "Verwaiste Einträge in ROUTE_CLASSIFICATION (Route existiert nicht mehr):\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(stale))
    )


def test_categories_are_valid():
    """Nur bekannte Kategorien zulassen (Tippfehler fangen)."""
    bad = {k: v for k, v in ROUTE_CLASSIFICATION.items() if v not in ALLOWED_CATEGORIES}
    assert not bad, f"Unbekannte Kategorie(n): {bad}"
