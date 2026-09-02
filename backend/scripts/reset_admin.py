#!/usr/bin/env python3
"""
Admin-Passwort-Reset — direkt gegen die DB, unabhängig vom App-Startup.

Warum es das gibt: Der RESET_ADMIN_PASSWORD-Weg über Env-Vars läuft im
Backend-Container beim Start und fasst nur die per DEFAULT_ADMIN_EMAIL /
DEFAULT_SUPERADMIN_EMAIL konfigurierten Konten an. Dieses Skript ist
deterministisch, zeigt was existiert und schreibt klar ins Terminal.

Ausführen IM BACKEND-CONTAINER (Coolify → Backend-Ressource → Terminal):

    python scripts/reset_admin.py
        → listet alle Admin-User (E-Mail, Rolle, aktiv, Schule)

    python scripts/reset_admin.py reset <email> <passwort>
        → setzt das Passwort eines BESTEHENDEN Users (und aktiviert ihn)

    python scripts/reset_admin.py create-superadmin <email> <passwort>
        → legt einen NEUEN Superadmin an (schulübergreifend, school_id=NULL)

Nutzt DATABASE_URL aus der Umgebung. Braucht KEIN JWT_SECRET.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# backend/ importierbar machen (Skript liegt in backend/scripts/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from sqlmodel import Session, create_engine, select

from models import User, School


MAX_PW_BYTES = 72  # bcrypt-Limit


def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("❌ DATABASE_URL ist nicht gesetzt. Im Backend-Container ausführen (dort ist sie gesetzt).")
        sys.exit(1)
    return create_engine(url)


def _role(u: User) -> str:
    r = u.role.value if hasattr(u.role, "value") else u.role
    return str(r)


def list_users(session: Session) -> None:
    users = session.exec(select(User).order_by(User.email)).all()
    if not users:
        print("⚠️  Es gibt noch KEINE Admin-User in der Datenbank.")
        print("    → Lege einen an:  python scripts/reset_admin.py create-superadmin <email> <passwort>")
        return
    print(f"Vorhandene Admin-User ({len(users)}):\n")
    print(f"  {'E-MAIL':<38} {'ROLLE':<13} AKTIV  SCHULE")
    print(f"  {'-'*38} {'-'*13} -----  ------")
    for u in users:
        print(f"  {u.email:<38} {_role(u):<13} {str(u.is_active):<5}  {u.school_id}")
    print("\nPasswort setzen:  python scripts/reset_admin.py reset <email> <passwort>")


def _hash(pw: str) -> str:
    if not pw or len(pw.encode()) > MAX_PW_BYTES:
        print(f"❌ Passwort fehlt oder zu lang (max. {MAX_PW_BYTES} Bytes).")
        sys.exit(1)
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def reset(session: Session, email: str, pw: str) -> None:
    email = email.lower().strip()
    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        print(f"❌ Kein User mit E-Mail '{email}' gefunden.\n")
        list_users(session)
        print("\n→ Für einen NEUEN Account:  python scripts/reset_admin.py create-superadmin <email> <passwort>")
        sys.exit(1)
    user.password_hash = _hash(pw)
    user.is_active = True
    user.password_changed_at = datetime.utcnow()  # entwertet alte Tokens
    session.add(user)
    session.commit()
    print(f"✅ Passwort für '{email}' gesetzt und Konto aktiviert (Rolle: {_role(user)}).")
    print("   Jetzt einloggen — und dieses Skript/Passwort nicht im Klartext herumliegen lassen.")


def create_superadmin(session: Session, email: str, pw: str) -> None:
    email = email.lower().strip()
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        print(f"⚠️  '{email}' existiert bereits (Rolle {_role(existing)}). Nutze stattdessen 'reset'.")
        sys.exit(1)
    session.add(User(
        email=email,
        password_hash=_hash(pw),
        school_id=None,       # Superadmin ist schulübergreifend
        role="superadmin",
        is_active=True,
        password_changed_at=datetime.utcnow(),
    ))
    session.commit()
    print(f"✅ Neuer Superadmin '{email}' angelegt.")


def main() -> None:
    args = sys.argv[1:]
    engine = _engine()
    with Session(engine) as session:
        if not args or args[0] in ("list", "-l", "--list"):
            list_users(session)
        elif args[0] == "reset" and len(args) == 3:
            reset(session, args[1], args[2])
        elif args[0] == "create-superadmin" and len(args) == 3:
            create_superadmin(session, args[1], args[2])
        else:
            print(__doc__)
            sys.exit(2)


if __name__ == "__main__":
    main()
