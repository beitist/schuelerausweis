"""
Kryptographie-Service für Hagen-ID

ECDSA P-256 Signatur-Verifikation für die QR-Code-Authentifizierung.

WICHTIG — die Vertrauensrichtung:
Würde der SERVER das Keypair erzeugen und den PRIVATEN Schlüssel im
Login-Response an die PWA ausliefern, kennten den Signierschlüssel:
der Server selbst, der TLS-terminierende Proxy, jedes Log — und erst dann das
Gerät. Eine asymmetrische Signatur, deren privaten Schlüssel der Server ausstellt,
ist effektiv nur ein zweites geteiltes Geheimnis: man zahlt die Komplexität der
Asymmetrie und bekommt die Sicherheit der Symmetrie.

Deshalb:
- Die PWA erzeugt das Keypair LOKAL im Browser:
      crypto.subtle.generateKey({name:'ECDSA', namedCurve:'P-256'}, false, ['sign'])
  `extractable = false` heißt: der private Schlüssel ist PER KONSTRUKTION nicht
  auslesbar — auch nicht durch ein XSS. Er liegt als CryptoKey in IndexedDB.
- Nur der PUBLIC KEY wird beim Server registriert (POST /pwa/register-key).
- Der Server SIGNIERT NIE. Er verifiziert ausschließlich.

Warum P-256 und nicht Ed25519?
`extractable: false` gibt es nur für Kurven, die die Engine NATIV unterstützt.
Ed25519 ist zwar inzwischen in allen Engines, aber die Verbreitung über den
Geräte-Bestand (Schülerhandys = Long Tail) dauert Jahre — und ein Ed25519-
POLYFILL würde den Schlüssel wieder in JS-lesbaren Speicher legen und damit
genau die Garantie aufgeben, um die es hier geht. P-256 kann jede Engine
nativ, also funktioniert die Nicht-Exportierbarkeit heute für jeden Schüler.

DER DRAHT-VERTRAG (Client ↔ Server) — mit echter WebCrypto verifiziert:
    Public Key:  exportKey('raw', pub)  -> 65 Bytes, uncompressed point 0x04||X||Y -> Base64
    Signatur:    sign({name:'ECDSA', hash:'SHA-256'}, priv, msg) -> 64 Bytes raw r||s -> Base64
    Nachricht:   "student_id:totp:session_hash:timestamp" als UTF-8 (WebCrypto hasht intern SHA-256)

    ACHTUNG: WebCrypto liefert die Signatur als RAW r||s (IEEE P1363), `cryptography`
    erwartet dagegen DER. Ohne diese Konvertierung schlägt JEDE echte Signatur fehl.
"""

import base64
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature


# Länge der rohen Signatur (r||s) bei P-256: 2 × 32 Byte
_P256_RAW_SIG_LEN = 64
# Länge des unkomprimierten Public-Key-Punkts: 0x04 || X(32) || Y(32)
_P256_RAW_PUB_LEN = 65


def _raw_signature_to_der(raw_sig: bytes) -> bytes:
    """
    Wandelt eine WebCrypto-Signatur (raw r||s, IEEE P1363) in DER um.

    `cryptography.verify()` akzeptiert ausschließlich DER — ohne diese
    Konvertierung würden ALLE echten Ausweise als "Signatur ungültig" abgelehnt.
    """
    if len(raw_sig) != _P256_RAW_SIG_LEN:
        raise ValueError(f"Ungültige Signaturlänge: {len(raw_sig)} (erwartet {_P256_RAW_SIG_LEN})")
    half = _P256_RAW_SIG_LEN // 2
    r = int.from_bytes(raw_sig[:half], "big")
    s = int.from_bytes(raw_sig[half:], "big")
    return utils.encode_dss_signature(r, s)


def load_public_key(public_key_b64: str) -> ec.EllipticCurvePublicKey:
    """
    Lädt einen von WebCrypto exportierten P-256 Public Key (raw, Base64).

    Wirft ValueError bei ungültigem Format — der Aufrufer MUSS das als
    "nicht verifizierbar" behandeln (fail-closed), nicht als gültig.
    """
    raw = base64.b64decode(public_key_b64, validate=True)
    if len(raw) != _P256_RAW_PUB_LEN or raw[0] != 0x04:
        raise ValueError(
            f"Ungültiger P-256 Public Key (Länge {len(raw)}, erstes Byte 0x{raw[0]:02x} "
            f"— erwartet {_P256_RAW_PUB_LEN} Bytes beginnend mit 0x04)"
        )
    # Wirft bei einem Punkt, der nicht auf der Kurve liegt
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def is_valid_public_key(public_key_b64: str) -> bool:
    """Prüft, ob ein Base64-String ein gültiger P-256 Public Key ist (für die Registrierung)."""
    try:
        load_public_key(public_key_b64)
        return True
    except Exception:
        return False


def verify_qr_code(
    student_id: str,
    totp: str,
    session_hash: str,
    timestamp,
    signature_b64: str,
    public_key_b64: Optional[str],
) -> bool:
    """
    Verifiziert die ECDSA-P-256-Signatur eines QR-Codes.

    Die Nachricht wird EXAKT so rekonstruiert, wie die PWA sie signiert hat:
        f"{student_id}:{totp}:{session_hash}:{timestamp}"
    (`timestamp` wird als String eingesetzt — der Aufrufer übergibt den ROH-Wert
    aus dem QR, damit nichts kanonisiert wird.)

    Fail-closed: Bei JEDEM Fehler (ungültiger Key, falsches Format,
    kaputte Base64) → False.

    Returns:
        bool: True nur, wenn die Signatur mathematisch gültig ist.
    """
    if not public_key_b64 or not signature_b64:
        return False

    try:
        payload = f"{student_id}:{totp}:{session_hash}:{timestamp}".encode("utf-8")

        public_key = load_public_key(public_key_b64)

        raw_sig = base64.b64decode(signature_b64, validate=True)
        der_sig = _raw_signature_to_der(raw_sig)

        # ECDSA hasht die Nachricht intern mit SHA-256 (genau wie WebCrypto)
        public_key.verify(der_sig, payload, ec.ECDSA(hashes.SHA256()))
        return True

    except (InvalidSignature, ValueError, TypeError, Exception):
        # InvalidSignature: Signatur passt nicht
        # ValueError:       kaputtes Key-/Signaturformat
        # Alles andere:     ebenfalls fail-closed
        return False
