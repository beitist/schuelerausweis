"""
Regressionstest: Mit RLS_ENABLED=true + `pool_pre_ping` muss `_on_checkin` nach den
`RESET ROLE`-Statements committen. Sonst geht die Connection MIT offener Transaktion
in den Pool zurück, und der nächste Checkout scheitert im Pre-Ping mit
"set_session cannot be used inside a transaction" → Crash-Loop beim Startup.

Dieser Test bildet genau die Pool-Wiederverwendung nach (pool_size=1 erzwingt Reuse), damit der
zweite Checkout den Pre-Ping auslöst. PG-only (SQLite kennt weder SET ROLE noch das Verhalten).

Ausführen:  cd backend && TEST_DATABASE_URL=... python -m pytest tests/test_rls_checkin.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine, text

import services.rls as rls


def _pg_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PG-only Test: TEST_DATABASE_URL nicht gesetzt.")
    if "test" not in url.rsplit("/", 1)[-1].lower():
        pytest.fail("TEST_DATABASE_URL muss 'test' im DB-Namen haben (Schutz).")
    return url


def test_checkin_commits_so_pre_ping_survives_reuse(monkeypatch):
    monkeypatch.setattr(rls, "RLS_ENABLED", True)
    engine = create_engine(_pg_url(), pool_pre_ping=True, pool_size=1, max_overflow=0)
    rls.register(engine)   # hängt _on_checkin (+ _after_begin) an
    try:
        # >1 Runde: der 2.+ Checkout wiederverwendet die Pool-Connection → Pre-Ping.
        # Ohne commit im Checkin würde hier "set_session cannot be used inside a transaction" fliegen.
        for _ in range(4):
            with engine.connect() as conn:
                assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        rls.unregister(engine)
        engine.dispose()
