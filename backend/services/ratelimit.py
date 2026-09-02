"""
DB-basiertes Login-Rate-Limiting.

Die Zähler liegen bewusst in der Tabelle `login_attempts` statt in-memory:
  (a) sie überleben Prozess-Neustarts,
  (b) sie gelten prozessübergreifend (mit `--workers 2` sonst doppelt so
      viele Versuche),
  (c) der Aufrufer begrenzt pro KONTO **und** pro IP — der Konto-Bucket macht
      IP-Rotation (Mobilfunk/Proxies) wirkungslos.

Grenzen kommen aus DB-Override/Env-Vars, sind aber mit harten Min/Max geklemmt,
damit man den Schutz nicht versehentlich wegkonfiguriert.
"""

import os
from datetime import datetime, timedelta
from typing import Tuple

from sqlmodel import Session, select, delete, func

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import LoginAttempt


# Verteidigungslinie: Grenzen dürfen nicht beliebig lasch konfigurierbar sein.
_ATTEMPTS_MIN, _ATTEMPTS_MAX, _ATTEMPTS_DEFAULT = 3, 20, 5
_WINDOW_MIN, _WINDOW_MAX, _WINDOW_DEFAULT = 5, 120, 15


def _clamp(raw, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


# Keys in der app_settings-Tabelle (Superadmin-Live-Override)
RL_ATTEMPTS_KEY = "rate_limit_attempts"
RL_WINDOW_KEY = "rate_limit_window_minutes"


def get_bounds() -> dict:
    """Erzwungene Grenzen + Defaults — für die Admin-UI (Slider-Range/Validierung)."""
    return {
        "attempts": {"min": _ATTEMPTS_MIN, "max": _ATTEMPTS_MAX, "default": _ATTEMPTS_DEFAULT},
        "window": {"min": _WINDOW_MIN, "max": _WINDOW_MAX, "default": _WINDOW_DEFAULT},
    }


def get_limits(session=None) -> Tuple[int, int]:
    """
    (max_versuche, fenster_minuten) — mit erzwungenen Min/Max.

    Reihenfolge: DB-Override (app_settings, per Superadmin-UI) → Env → Default.
    Ohne `session` wird nur Env/Default genutzt (z.B. reine Unit-Tests). Der DB-Lookup
    ist tolerant: schlägt er fehl (Tabelle fehlt o.ä.), fällt er auf Env/Default zurück.
    """
    a_src = w_src = None
    if session is not None:
        try:
            from services import appsettings
            a_src = appsettings.get_value(session, RL_ATTEMPTS_KEY)
            w_src = appsettings.get_value(session, RL_WINDOW_KEY)
        except Exception:
            a_src = w_src = None
    if a_src is None:
        a_src = os.getenv("RATE_LIMIT_ATTEMPTS")
    if w_src is None:
        w_src = os.getenv("RATE_LIMIT_WINDOW_MINUTES")
    attempts = _clamp(a_src, _ATTEMPTS_MIN, _ATTEMPTS_MAX, _ATTEMPTS_DEFAULT)
    window = _clamp(w_src, _WINDOW_MIN, _WINDOW_MAX, _WINDOW_DEFAULT)
    return attempts, window


def is_blocked(session: Session, identifier: str) -> Tuple[bool, int]:
    """
    Ist dieser Bucket gesperrt? Returns (blockiert, verbleibende_sekunden).
    """
    attempts, window = get_limits(session)
    cutoff = datetime.utcnow() - timedelta(minutes=window)
    timestamps = session.exec(
        select(LoginAttempt.created_at)
        .where(LoginAttempt.identifier == identifier)
        .where(LoginAttempt.created_at > cutoff)
    ).all()
    if len(timestamps) >= attempts:
        unblock_at = min(timestamps) + timedelta(minutes=window)
        remaining = (unblock_at - datetime.utcnow()).total_seconds()
        return True, max(0, int(remaining))
    return False, 0


def is_blocked_any(session: Session, *identifiers: str) -> Tuple[bool, int]:
    """Gesperrt, wenn IRGENDEINER der Buckets gesperrt ist (Konto ODER IP)."""
    worst = 0
    blocked = False
    for ident in identifiers:
        b, secs = is_blocked(session, ident)
        if b:
            blocked = True
            worst = max(worst, secs)
    return blocked, worst


def remaining_attempts(session: Session, identifier: str) -> int:
    """Verbleibende Versuche für einen Bucket (für die Nutzer-Meldung)."""
    attempts, window = get_limits(session)
    cutoff = datetime.utcnow() - timedelta(minutes=window)
    n = session.exec(
        select(func.count(LoginAttempt.id))
        .where(LoginAttempt.identifier == identifier)
        .where(LoginAttempt.created_at > cutoff)
    ).one()
    return max(0, attempts - n)


def record_failure(session: Session, *identifiers: str) -> None:
    """Einen Fehlversuch auf allen angegebenen Buckets vermerken (committet selbst)."""
    for ident in identifiers:
        session.add(LoginAttempt(identifier=ident))
    session.commit()


def clear(session: Session, *identifiers: str) -> None:
    """Bei erfolgreichem Login die Buckets leeren (committet selbst)."""
    for ident in identifiers:
        session.exec(delete(LoginAttempt).where(LoginAttempt.identifier == ident))
    session.commit()


def purge_old(session: Session, older_than_minutes: int = 24 * 60) -> int:
    """Aufräumen: Zeilen älter als `older_than_minutes` löschen (für den Cronjob)."""
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    ids = session.exec(
        select(LoginAttempt.id).where(LoginAttempt.created_at < cutoff)
    ).all()
    if ids:
        session.exec(delete(LoginAttempt).where(LoginAttempt.id.in_(ids)))
        session.commit()
    return len(ids)
