"""
Regressionstest für die ECDSA-P-256-QR-Signaturprüfung in /check.

Die PWA erzeugt das Keypair selbst auf dem Gerät (non-extractable) und schickt
nur den Public Key. Der Server verifiziert ausschließlich.

Diese Tests bilden den ECHTEN WebCrypto-Draht-Vertrag nach:
- Public Key:  X9.62 UncompressedPoint (65 B, 0x04||X||Y) -> Base64   [= WebCrypto exportKey('raw')]
- Signatur:    raw r||s (64 B, IEEE P1363) -> Base64                 [= WebCrypto sign(ECDSA/SHA-256)]
`cryptography` liefert/erwartet DER — die Tests konvertieren daher bewusst
DER<->raw, damit sie exakt das prüfen, was der Browser produziert.

Ausführen:  cd backend && python -m pytest tests/test_qr_signature.py -q
"""

import os
os.environ.setdefault("JWT_SECRET", "test_secret_min_32_chars_for_testing_only_123456")

import time
import base64
from datetime import date

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pyotp
from sqlmodel import SQLModel, Session, create_engine
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes, serialization

from models import School, Class, Student
from services.auth import generate_totp_code
import main as mainmod


SESSION_HASH = "abcdef12" + "0" * 56  # 64-Hex; Prefix "abcdef12"
SESSION_PREFIX = SESSION_HASH[:8]


# ---------------------------------------------------------------------------
# WebCrypto-Nachbau: erzeugt Keys/Signaturen EXAKT im Browser-Wire-Format
# ---------------------------------------------------------------------------

def gen_p256_keypair():
    """Wie crypto.subtle.generateKey({name:'ECDSA', namedCurve:'P-256'}, ...)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    raw_pub = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )  # 65 B, 0x04||X||Y
    return priv, base64.b64encode(raw_pub).decode()


def sign_p256(priv, payload: str) -> str:
    """Wie crypto.subtle.sign({name:'ECDSA', hash:'SHA-256'}, ...) -> raw r||s, Base64."""
    der = priv.sign(payload.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # IEEE P1363
    return base64.b64encode(raw).decode()


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    priv, pub_b64 = gen_p256_keypair()
    secret = pyotp.random_base32()
    with Session(engine) as s:
        school = School(name="S"); s.add(school); s.commit(); s.refresh(school)
        cls = Class(school_id=school.id, name="E1A"); s.add(cls); s.commit(); s.refresh(cls)
        student = Student(
            school_internal_id="1", first_name="Max", last_name="M",
            dob=date(2005, 1, 1), class_id=cls.id, is_active=True,
            totp_secret=secret, id_public_key=pub_b64,          # registrierter P-256-Key
            active_session_token_hash=SESSION_HASH,
        )
        s.add(student); s.commit(); s.refresh(student)
        yield s, student, priv, secret


def build_qr(student_id, secret, priv, sign_priv=None):
    ts = int(time.time()) // 30
    code = generate_totp_code(secret)
    sig = sign_p256(sign_priv or priv, f"{student_id}:{code}:{SESSION_PREFIX}:{ts}")
    return f"{student_id}:{code}:{SESSION_PREFIX}:{ts}:{sig}"


def body(resp):
    return resp.body.decode()


class _FakeClient:
    host = "203.0.113.9"


class FakeRequest:
    """
    Minimaler Request-Ersatz für den Direktaufruf von verify_qr_page().

    /check nimmt ein `request` entgegen (für die IP des Verifizierers, die
    aber nur bei aktivem Trace geloggt wird). get_client_ip() braucht
    lediglich .headers.get(...) und .client.host.
    """
    def __init__(self):
        self.headers = {}
        self.client = _FakeClient()
        self.method = "GET"


def check(s, p):
    return mainmod.verify_qr_page(request=FakeRequest(), p=p, session=s)


def test_authentic_p256_qr_is_valid(setup):
    """Der Kern: eine ECHTE WebCrypto-kompatible P-256-Signatur muss verifizieren."""
    s, student, priv, secret = setup
    resp = check(s, build_qr(str(student.id), secret, priv))
    assert "Ausweis gültig" in body(resp), body(resp)[:400]


def test_forged_signature_rejected(setup):
    """Angreifer signiert mit einem FREMDEN Key -> Signatur ungültig."""
    s, student, priv, secret = setup
    attacker, _ = gen_p256_keypair()
    resp = check(s, build_qr(str(student.id), secret, priv, sign_priv=attacker))
    assert "Signatur ungültig" in body(resp), body(resp)[:400]


def test_unsigned_placeholder_rejected(setup):
    s, student, priv, secret = setup
    ts = int(time.time()) // 30
    code = generate_totp_code(secret)
    p = f"{student.id}:{code}:{SESSION_PREFIX}:{ts}:NO_KEY"
    resp = check(s, p)
    assert "Nicht verifizierbar" in body(resp), body(resp)[:400]


def test_student_without_registered_key_rejected(setup):
    """Registrierungs-Fenster: solange kein Key registriert ist -> nicht verifizierbar."""
    s, student, priv, secret = setup
    student.id_public_key = None
    s.add(student); s.commit()
    resp = check(s, build_qr(str(student.id), secret, priv))
    assert "Nicht verifizierbar" in body(resp), body(resp)[:400]


def test_stale_ed25519_key_does_not_crash(setup):
    """32-Byte-Key im Ed25519-Format (kein P-256-Punkt) -> 'Signatur ungültig', kein Crash."""
    s, student, priv, secret = setup
    student.id_public_key = base64.b64encode(b"\x00" * 32).decode()  # 32 B = Ed25519-Format
    s.add(student); s.commit()
    resp = check(s, build_qr(str(student.id), secret, priv))
    assert "Signatur ungültig" in body(resp), body(resp)[:400]


def test_tampered_session_prefix_rejected(setup):
    s, student, priv, secret = setup
    ts = int(time.time()) // 30
    code = generate_totp_code(secret)
    sig = sign_p256(priv, f"{student.id}:{code}:{SESSION_PREFIX}:{ts}")
    tampered = f"{student.id}:{code}:ffffffff:{ts}:{sig}"  # Session-Prefix passt nicht mehr
    resp = check(s, tampered)
    assert "Anderes Gerät" in body(resp) or "Signatur ungültig" in body(resp), body(resp)[:400]


def test_leading_zero_totp_still_verifies(setup):
    """
    ~10 % aller TOTP-Codes haben eine führende Null. Der Server verifiziert über den
    ROH-String — eine Kanonisierung (int()) würde diese Ausweise sporadisch brechen.
    """
    s, student, priv, secret = setup
    # Payload mit künstlichem führende-Null-Code bauen und dagegen signieren
    ts = int(time.time()) // 30
    code = "048512"
    sig = sign_p256(priv, f"{student.id}:{code}:{SESSION_PREFIX}:{ts}")
    p = f"{student.id}:{code}:{SESSION_PREFIX}:{ts}:{sig}"
    # TOTP-Prüfung wird hier fehlschlagen (Code ist erfunden) — entscheidend ist nur,
    # dass die SIGNATUR über den Roh-String korrekt gebildet/geprüft würde.
    # Direkter Krypto-Vertragstest:
    from services.crypto import verify_qr_code
    assert verify_qr_code(str(student.id), code, SESSION_PREFIX, str(ts), sig, student.id_public_key) is True
