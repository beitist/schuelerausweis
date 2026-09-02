"""
CSP für die vom Backend ausgelieferten HTML-Seiten (/check, /verify-cert).

Testet die SecurityHeadersMiddleware direkt (kein httpx/TestClient nötig): HTML bekommt
eine strenge CSP (default-src 'none'), JSON/Nicht-HTML nicht, und Swagger/ReDoc sind
ausgenommen (sonst laden deren Assets nicht).

Ausführen:  cd backend && python -m pytest tests/test_csp_headers.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from starlette.responses import Response, HTMLResponse

from main import SecurityHeadersMiddleware


def _run(path, make_response):
    mw = SecurityHeadersMiddleware(app=None)

    class _URL:
        hostname = "api.example.com"
    _URL.path = path

    class _Req:
        url = _URL()

    async def call_next(_req):
        return make_response()

    return asyncio.run(mw.dispatch(_Req(), call_next))


def test_html_page_gets_strict_csp():
    resp = _run("/check", lambda: HTMLResponse("<html><style>x{}</style></html>"))
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "img-src data:" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    # kein script-src erlaubt → default-src 'none' greift → injiziertes JS liefe nicht
    assert "script-src" not in csp


def test_json_response_has_no_csp():
    resp = _run("/pwa/me", lambda: Response('{"ok": true}', media_type="application/json"))
    assert "Content-Security-Policy" not in resp.headers


def test_swagger_is_excluded():
    # /docs liefert zwar HTML, darf aber KEINE strenge CSP bekommen (Assets würden brechen)
    resp = _run("/docs", lambda: HTMLResponse("<html>swagger</html>"))
    assert "Content-Security-Policy" not in resp.headers
