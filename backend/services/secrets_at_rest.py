"""
Verschlüsselung sensibler Felder at-rest (PII, Login-PINs, TOTP-Seeds, Foto-Blobs).

Zweck: Ein reiner **DB-Dump** (geklautes Backup, `pg_dump`, SQLi-Dump, geleakte DB-Credentials)
soll KEINE nutzbaren Klartext-Daten enthalten. Der Schlüssel liegt in der **ENV
(`SECRETS_KEY`), NICHT in der DB** → ein Dump allein ist wertlos. Schützt NICHT vor
Full-Server-Compromise (dann hat der Angreifer Key + DB).

Verhalten:
- `SECRETS_KEY` gesetzt → Fernet (AES-128-CBC + HMAC-SHA256) beim Schreiben, Entschlüsseln beim Lesen.
- `SECRETS_KEY` NICHT gesetzt → **Passthrough** (Klartext) — für Dev/Tests ohne Key.
- Lesen ist **tolerant**: Legacy-Klartext (vor der Migration) wird unverändert zurückgegeben,
  neue Werte verschlüsselt. So ist der Übergang bruchfrei; ein Startup-Job verschlüsselt Altbestand.

WICHTIG: Fernet ist NICHT deterministisch (gleicher Klartext → anderer Ciphertext). Daher kann man
auf verschlüsselten Spalten NICHT per SQL `==` matchen. Der Login vergleicht die PIN deshalb in
Python (Kandidaten über Name+Geburtsdatum laden, dann PIN prüfen) — siehe services/auth.py.

Key erzeugen:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import hashlib
import hmac as _hmac
import os
from datetime import date
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, String

_cache: dict = {}


def _fernet() -> Optional[Fernet]:
    k = os.getenv("SECRETS_KEY")
    if not k:
        return None
    if k not in _cache:
        _cache[k] = Fernet(k.encode())
    return _cache[k]


def is_enabled() -> bool:
    return os.getenv("SECRETS_KEY") is not None and os.getenv("SECRETS_KEY") != ""


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    f = _fernet()
    if f is None or plaintext is None:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt(token: Optional[str]) -> Optional[str]:
    f = _fernet()
    if f is None or token is None:
        return token
    try:
        return f.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return token  # Legacy-Klartext (noch nicht migriert) → unverändert


def _index_key() -> Optional[bytes]:
    k = os.getenv("SECRETS_KEY")
    if not k:
        return None
    # Eigenen HMAC-Key aus SECRETS_KEY ableiten (nicht denselben Key wie für Fernet nutzen).
    return hashlib.sha256(b"hagenid-blind-index:" + k.encode()).digest()


def blind_index(normalized: Optional[str]) -> Optional[str]:
    """
    Deterministischer, geheimer Index für EXAKTE Suche auf verschlüsselten Feldern
    (Login-Lookup, Admin-Exakt-Suche). Der Aufrufer normalisiert vorher (z.B.
    `last_name.strip().lower()` bzw. `dob.isoformat()`).

    Mit Key: HMAC-SHA256(abgeleiteter Key, Wert) → hex. Ohne Key: der normalisierte
    Wert selbst (Passthrough), damit Matching in Dev/Tests weiter funktioniert.
    Gleiche Klartexte → gleicher Index (das ist gewollt); das leakt Gleichheit, aber
    nicht den Wert — akzeptabel für ein Login-/Suchfeld.
    """
    if normalized is None:
        return None
    ik = _index_key()
    if ik is None:
        return normalized
    return _hmac.new(ik, normalized.encode(), hashlib.sha256).hexdigest()


class EncryptedStr(TypeDecorator):
    """Verschlüsselt transparent beim Schreiben, entschlüsselt (tolerant) beim Lesen."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)


class EncryptedDate(TypeDecorator):
    """
    Wie EncryptedStr, aber für `date`: Python-seitig bleibt es ein `date`, gespeichert
    wird der verschlüsselte ISO-String. Tolerant: eine noch nicht migrierte DATE-Spalte
    (liefert ein `date`-Objekt) bzw. Legacy-Klartext-ISO wird korrekt zurückgegeben.
    """
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        iso = value if isinstance(value, str) else value.isoformat()
        return encrypt(iso)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, date):
            return value  # Spalte ist noch DATE (vor der Migration)
        dec = decrypt(value)
        try:
            return date.fromisoformat(dec)
        except (ValueError, TypeError):
            return None
