"""
Test für den Druckseiten-QR-Endpoint (serverseitige QR-Erzeugung, kein Drittanbieter).

Ausführen:  cd backend && python -m pytest tests/test_print_qr.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import User
import routes.admin as admin


def test_print_qr_returns_png():
    adm = User(id=1, email="a@x.de", password_hash="x", role="school_admin",
               school_id=1, is_active=True)
    resp = admin.get_print_qr(data="https://ausweis.example.com/kkbk", current_admin=adm)
    assert resp.media_type.startswith("image/png")
    assert resp.body[:8] == b"\x89PNG\r\n\x1a\n"      # PNG-Magic
    assert "max-age" in resp.headers.get("cache-control", "")
