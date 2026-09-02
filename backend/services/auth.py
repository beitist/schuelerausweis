"""
Authentifizierungs-Service für Hagen-ID

Zwei Arten von Login:
1. Admin-Login: E-Mail + Passwort → JWT Token
2. Schüler-Login: Nachname + Geburtsdatum (+ opt. Schülernummer) → JWT + TOTP Secret

Sicherheitsfeatures:
- Rate Limiting gegen Brute-Force (5 Versuche pro 15 Minuten)
- JWT mit Ablaufzeit
- Password-Hashing mit bcrypt
- TOTP für Offline-QR-Codes
"""

import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, date
from typing import Optional, Tuple
from enum import Enum

import bcrypt
from jose import jwt, JWTError
import pyotp
from sqlmodel import Session, select

# Models importieren
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from models import User, Student, UserRole


# ============================================================================
# KONFIGURATION (aus .env)
# ============================================================================

# SICHERHEITSKRITISCH: JWT_SECRET muss in Produktion gesetzt werden —
# es gibt bewusst keinen eingebauten Default.
JWT_SECRET = os.getenv("JWT_SECRET")

# Validierung: Mindestens 32 Zeichen für kryptografische Sicherheit
# In Dev-Umgebung (DEBUG=true) wird ein Fallback erlaubt
if not JWT_SECRET:
    if os.getenv("DEBUG", "").lower() == "true":
        # Nur für lokale Entwicklung: Temporäres Secret mit Warnung
        JWT_SECRET = "dev-secret-ONLY-FOR-LOCAL-DEVELOPMENT-32chars"
        print("⚠️  WARNUNG: JWT_SECRET nicht gesetzt! Nutze unsicheren Dev-Fallback.")
    else:
        raise ValueError(
            "JWT_SECRET muss in der .env-Datei gesetzt werden! "
            "Mindestens 32 Zeichen für ausreichende Sicherheit."
        )
elif len(JWT_SECRET) < 32:
    raise ValueError(
        f"JWT_SECRET ist zu kurz ({len(JWT_SECRET)} Zeichen). "
        "Mindestens 32 Zeichen erforderlich!"
    )

# Bekannte Beispiel-/Dev-Secrets aus dem Repository dürfen in Produktion NICHT
# verwendet werden — sonst könnte jeder mit Repo-Zugriff Tokens fälschen.
_KNOWN_WEAK_SECRETS = {
    "dev-secret-change-in-production-abc123xyz",      # .env.example
    "dev-secret-ONLY-FOR-LOCAL-DEVELOPMENT-32chars",  # Dev-Fallback (siehe oben)
    "dev-secret",
    "changeme", "change-me", "secret", "your-secret-key",
}
if os.getenv("DEBUG", "").lower() != "true" and JWT_SECRET in _KNOWN_WEAK_SECRETS:
    raise ValueError(
        "JWT_SECRET ist ein bekanntes Beispiel-Secret aus dem Repository. "
        "Bitte ein eigenes, zufälliges Secret setzen: "
        'python -c "import secrets; print(secrets.token_hex(32))"'
    )

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 Tage Standard

# Logging Konfiguration
logger = logging.getLogger(__name__)
# Log-Level basierend auf DEBUG-Umgebungsvariable
if os.getenv("DEBUG", "").lower() == "true":
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

# Rate Limiting Konfiguration
RATE_LIMIT_ATTEMPTS = int(os.getenv("RATE_LIMIT_ATTEMPTS", "5"))
RATE_LIMIT_WINDOW_MINUTES = int(os.getenv("RATE_LIMIT_WINDOW_MINUTES", "15"))


# ============================================================================
# TOKEN TYPES
# ============================================================================

class TokenType(str, Enum):
    """
    Unterscheidung zwischen Admin- und Schüler-Tokens.
    Wird im JWT-Payload gespeichert.
    """
    ADMIN = "admin"     # Für User-Tabelle (Lehrer/Admins)
    STUDENT = "student" # Für Student-Tabelle (Schüler)


# ============================================================================
# RATE LIMITING
# ============================================================================
# DB-basierter Limiter in services/ratelimit.py: neustart-/worker-fest und mit
# Lockout pro Konto UND pro IP. Import erfolgt in den Login-Methoden unten.


# ============================================================================
# PASSWORD HASHING
# ============================================================================

def hash_password(password: str) -> str:
    """
    Hasht ein Passwort mit bcrypt.
    
    bcrypt inkludiert automatisch einen Salt und ist
    resistent gegen Rainbow-Table-Angriffe.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verifiziert ein Passwort gegen seinen Hash."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


# ============================================================================
# SESSION TOKEN HASHING (für Highlander-System)
# ============================================================================

def generate_session_token() -> str:
    """
    Generiert einen kryptografisch sicheren Session-Token.
    
    32 Bytes = 256 Bit Entropie, URL-safe encoded.
    Dieser Token wird dem Client zurückgegeben und muss bei jedem Request mitgesendet werden.
    """
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """
    Hasht einen Session-Token mit SHA256.
    
    Der Hash wird in der Datenbank gespeichert, nicht der Token selbst.
    So kann bei einem Datenbank-Leak kein Angreifer gültige Tokens extrahieren.
    """
    return hashlib.sha256(token.encode()).hexdigest()


# ============================================================================
# JWT TOKEN HANDLING
# ============================================================================

def create_token(
    subject: str,
    token_type: TokenType,
    school_id: Optional[int] = None,
    role: Optional[str] = None,
    extra_data: Optional[dict] = None,
    expires_at: Optional[datetime] = None
) -> str:
    """
    Erstellt einen JWT Token.

    Args:
        subject: User-ID (UUID als String für Schüler, int als String für Admins)
        token_type: ADMIN oder STUDENT
        school_id: ID der Schule (für Berechtigungsprüfungen)
        role: Rolle bei Admins (superadmin, school_admin, teacher)
        extra_data: Zusätzliche Daten für den Payload
        expires_at: Fester Ablaufzeitpunkt (überschreibt die Standard-Laufzeit).
                    Für Schüler = Ausweis-Ablauf, damit die PWA bis zum
                    Schuljahresende eingeloggt bleibt.

    Returns:
        Signierter JWT Token als String
    """
    expire = expires_at if expires_at is not None else datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)
    
    payload = {
        "sub": subject,                    # Standard JWT "subject"
        "type": token_type.value,          # admin oder student
        "school_id": school_id,
        "role": role,
        "exp": expire,                     # Ablaufzeit
        "iat": datetime.utcnow(),          # Ausstellungszeit
    }
    
    if extra_data:
        payload.update(extra_data)
    
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """
    Dekodiert und validiert einen JWT Token.
    
    Returns:
        Payload als dict oder None bei ungültigem Token
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def create_admin_token(user: User) -> str:
    """Convenience: Erstellt Token für einen Admin-User."""
    return create_token(
        subject=str(user.id),
        token_type=TokenType.ADMIN,
        school_id=user.school_id,
        role=user.role.value if isinstance(user.role, UserRole) else user.role
    )


def create_student_token(
    student: Student, school_id: int, expires_at: Optional[datetime] = None
) -> str:
    """
    Erstellt Token für einen Schüler.

    WICHTIG: Das TOTP-Secret gehört NICHT in den Token! JWT-Tokens sind nur
    signiert, nicht verschlüsselt — jeder kann sie dekodieren. Das TOTP-Secret
    liefert stattdessen der /pwa/me Endpoint.

    expires_at: fester Ablauf (= Ausweis-Ablauf). Ohne Angabe gilt die
    Standard-Laufzeit (JWT_EXPIRE_MINUTES).
    """
    return create_token(
        subject=str(student.id),
        token_type=TokenType.STUDENT,
        school_id=school_id,
        extra_data={
            "name": f"{student.first_name} {student.last_name}"
        },
        expires_at=expires_at
    )


def end_of_school_year(today: date) -> date:
    """
    Schuljahresende (31.07.). Liegt der 31.07. schon in der Vergangenheit,
    das nächste Jahr. Fallback für die Session-Laufzeit, wenn eine Klasse kein
    validity_end hat.
    """
    end = date(today.year, 7, 31)
    if today > end:
        end = date(today.year + 1, 7, 31)
    return end


def student_session_expiry(student: Student, session: Session) -> datetime:
    """
    Ablauf der Schüler-Session/Cookies = **Ausweis-Ablauf**:
    validity_end der Klasse (bzw. student.validity_end_override), Fallback
    Schuljahresende. So bleibt die PWA eingeloggt, solange der Ausweis gilt.

    Nie in der Vergangenheit (mind. jetzt + 1 Tag), damit ein bereits abgelaufener
    Ausweis den Login nicht sofort wieder wertlos macht.
    """
    from models import Class

    end_date = None
    if student.class_id:
        cls = session.get(Class, student.class_id)
        if cls:
            end_date = student.validity_end_override or cls.validity_end
    if end_date is None:
        end_date = end_of_school_year(datetime.utcnow().date())

    exp = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)
    floor = datetime.utcnow() + timedelta(days=1)
    return max(exp, floor)


# ============================================================================
# AUTH SERVICE KLASSE
# ============================================================================

class AuthService:
    """
    Hauptservice für Authentifizierung.
    
    Verwendung:
        auth = AuthService(session)
        
        # Admin Login
        token = auth.login_admin("admin@schule.de", "password123", "192.168.1.1")
        
        # Schüler Login
        token, totp = auth.login_student("Mustermann", date(2005, 3, 15), school_id=1)
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    # ========== ADMIN LOGIN ==========
    
    def login_admin(
        self,
        email: str,
        password: str,
        client_ip: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Admin-Login mit E-Mail und Passwort.
        
        Args:
            email: E-Mail des Admins
            password: Passwort im Klartext
            client_ip: IP-Adresse für Rate Limiting
        
        Returns:
            Tuple von (token, error_message)
            - Bei Erfolg: (token, None)
            - Bei Fehler: (None, error_message)
        """
        from services import ratelimit

        email_norm = email.lower().strip()
        # Zwei Buckets: pro IP UND pro Konto — Konto-Bucket macht IP-Rotation wirkungslos.
        ip_id = f"admin-ip:{client_ip}"
        acct_id = f"admin-acct:{email_norm}"

        # Rate Limiting prüfen (gesperrt, wenn IP ODER Konto gesperrt)
        blocked, remaining_seconds = ratelimit.is_blocked_any(self.session, ip_id, acct_id)
        if blocked:
            logger.warning(f"Login blocked (rate limit) for {email_norm[:3]}*** / {client_ip}")
            minutes = remaining_seconds // 60 + 1
            return None, f"Zu viele Fehlversuche. Bitte {minutes} Minuten warten."

        # User suchen
        user = self.session.exec(
            select(User).where(User.email == email_norm)
        ).first()

        # Logging nur im DEBUG-Modus, E-Mail maskiert
        if user:
            logger.debug(f"User found: {email[:3]}***")
        else:
            logger.debug(f"User not found: {email[:3]}***")

        if not user or not verify_password(password, user.password_hash):
            ratelimit.record_failure(self.session, ip_id, acct_id)
            remaining = ratelimit.remaining_attempts(self.session, acct_id)
            logger.info(f"Failed login attempt for {email[:3]}*** from {client_ip}")
            from services import audit
            audit.record(self.session, "admin_login_failed",
                         subject_id=email_norm, ip=client_ip,
                         extra={"remaining": remaining})
            return None, f"Ungültige Anmeldedaten. Noch {remaining} Versuche."

        if not user.is_active:
            logger.warning(f"Inactive user attempted login: {email[:3]}***")
            return None, "Konto deaktiviert. Bitte Administrator kontaktieren."

        # Erfolg → beide Buckets leeren
        logger.info(f"Successful login: {email[:3]}*** from {client_ip}")
        ratelimit.clear(self.session, ip_id, acct_id)
        token = create_admin_token(user)

        return token, None
    
    # ========== SCHÜLER LOGIN ==========
    
    def login_student(
        self,
        last_name: str,
        dob: date,
        school_id: Optional[int],
        client_ip: str,
        login_pin: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Schüler-Login mit Nachname + Geburtsdatum + PIN.

        school_id ist optional — wenn nicht angegeben, wird global gesucht
        (Auto-Erkennung der Schule anhand der Credentials).

        HIGHLANDER-PRINZIP:
        Bei jedem Login wird ein neuer Session-Token generiert.
        → Nur EIN Gerät kann gleichzeitig eingeloggt sein!

        Returns:
            Tuple von (jwt_token, totp_secret, session_token, error_message)

        SICHERHEIT: Es wird bewusst KEIN privater Schlüssel ausgeliefert.
        Die PWA erzeugt ihr Signier-Keypair selbst auf dem Gerät (non-extractable)
        und registriert nur den Public Key (POST /pwa/register-key).
        """
        from services import ratelimit

        # Zwei Buckets:
        # - pro KONTO (Schule+Nachname+Geburtsdatum): das eigentliche Ziel des Angreifers.
        #   Damit ist die PIN-Brute-Force nicht per IP-Rotation umgehbar.
        # - pro IP: fängt breites Durchprobieren vieler Konten von einer Quelle ab.
        acct_id = f"student-acct:{school_id if school_id is not None else '*'}:{last_name.lower().strip()}:{dob.isoformat()}"
        ip_id = f"student-ip:{client_ip}"

        blocked, remaining_seconds = ratelimit.is_blocked_any(self.session, acct_id, ip_id)
        if blocked:
            minutes = remaining_seconds // 60 + 1
            return None, None, None, f"Zu viele Fehlversuche. Bitte {minutes} Minuten warten."

        from models import Class

        # Schüler suchen: Nachname + Geburtsdatum + PIN.
        # SICHERHEIT: case-insensitiver EXAKT-Vergleich statt ILIKE — sonst würden
        # LIKE-Wildcards (last_name="%") ALLE Schüler matchen und das Konto-Rate-Limit
        # aushebeln → PIN-Brute-Force/Enumeration über die ganze Kohorte.
        # Name UND Geburtsdatum sind at-rest verschlüsselt → Match läuft über die
        # Blind-Index-Spalten (HMAC), nicht über die Klartext-Felder. Die PIN steht ebenfalls
        # nicht im SQL-Filter (Fernet nicht deterministisch) — sie wird in Python geprüft.
        from services.secrets_at_rest import blind_index
        query = (
            select(Student)
            .where(Student.last_name_bidx == blind_index(last_name.strip().lower()))
            .where(Student.dob_bidx == blind_index(dob.isoformat()))
            .where(Student.is_active == True)
            .where(Student.deleted_at.is_(None))
        )

        # Wenn school_id angegeben → auf Klassen dieser Schule beschränken
        if school_id is not None:
            classes = self.session.exec(
                select(Class.id).where(Class.school_id == school_id)
            ).all()
            if not classes:
                return None, None, None, "Schule nicht gefunden."
            query = query.where(Student.class_id.in_(classes))

        # PIN-Prüfung in Python (student.login_pin liefert dank EncryptedStr den Klartext).
        students = [s for s in self.session.exec(query).all() if s.login_pin == login_pin]
        
        if len(students) == 0:
            ratelimit.record_failure(self.session, acct_id, ip_id)
            remaining = ratelimit.remaining_attempts(self.session, acct_id)
            from services import audit
            audit.record(self.session, "student_login_failed",
                         school_id=school_id, ip=client_ip,
                         extra={"last_name": last_name.strip(), "dob": dob.isoformat(), "remaining": remaining})
            return None, None, None, f"Login fehlgeschlagen. Noch {remaining} Versuche."
        
        if len(students) > 1:
            # Sollte nicht passieren (Name + DoB + PIN ist eindeutig)
            return None, None, None, "Fehler: Mehrere Schüler gefunden. Bitte Admin kontaktieren."
        
        student = students[0]
        
        # =====================================
        # HIGHLANDER: Neuen Session-Token generieren
        # =====================================
        session_token = generate_session_token()
        session_token_hash = hash_session_token(session_token)
        
        # Hash in DB speichern - invalidiert automatisch alle alten Sessions!
        student.active_session_token_hash = session_token_hash
        
        # =====================================
        # GERÄTE-SCHLÜSSEL: alten Public Key invalidieren
        # =====================================
        # Der Server erzeugt bewusst KEINEN Schlüssel (siehe services/crypto.py).
        # Das neue Gerät erzeugt sein Keypair selbst (non-extractable) und
        # registriert den Public Key via POST /pwa/register-key.
        # Hier wird der Schlüssel des VORHERIGEN Geräts entwertet — passend zum
        # Highlander-Prinzip: ein Login = ein Gerät = ein Schlüssel.
        # Bis zur Registrierung hat der Schüler keinen gültigen Signier-Schlüssel;
        # /check meldet dann sauber "nicht verifizierbar" (fail-closed).
        student.id_public_key = None
        student.id_key_generated_at = None

        self.session.add(student)
        self.session.commit()

        # Erfolg → beide Buckets leeren
        ratelimit.clear(self.session, acct_id, ip_id)
        if school_id is None and student.class_id:
            student_class = self.session.get(Class, student.class_id)
            school_id = student_class.school_id if student_class else None
        # Token läuft mit dem AUSWEIS ab (Schuljahresende), nicht nach 7 Tagen —
        # damit die PWA über das Schuljahr eingeloggt bleibt (iOS-Persistenz via Cookie).
        expires_at = student_session_expiry(student, self.session)
        jwt_token = create_student_token(student, school_id, expires_at=expires_at)

        return jwt_token, student.totp_secret, session_token, None
    
    # ========== TOKEN VALIDIERUNG ==========
    
    def validate_admin_token(self, token: str) -> Tuple[Optional[User], Optional[str]]:
        """
        Validiert einen Admin-Token und gibt den User zurück.
        
        Returns:
            Tuple von (user, error_message)
        """
        payload = decode_token(token)
        
        if not payload:
            return None, "Ungültiger oder abgelaufener Token."
        
        if payload.get("type") != TokenType.ADMIN.value:
            return None, "Falscher Token-Typ."
        
        user_id = payload.get("sub")
        if not user_id:
            return None, "Ungültiger Token."
        
        user = self.session.get(User, int(user_id))
        if not user:
            return None, "Benutzer nicht gefunden."

        if not user.is_active:
            return None, "Konto deaktiviert."

        # Session-Revocation über den Passwortwechsel. Ein Token, das VOR der
        # letzten Passwortänderung ausgestellt wurde, ist ungültig — so wirft ein
        # Passwort-Reset (z.B. bei Verdacht) alle bestehenden Admin-Sitzungen raus.
        # NULL → kein Wechsel bekannt (Altbestand) → keine Ablehnung.
        if user.password_changed_at is not None:
            import calendar
            iat = payload.get("iat")
            if iat is not None and int(iat) < calendar.timegm(user.password_changed_at.utctimetuple()):
                return None, "Sitzung abgelaufen (Passwort geändert). Bitte neu einloggen."

        return user, None
    
    def validate_student_token(
        self, 
        token: str,
        session_token: Optional[str] = None
    ) -> Tuple[Optional[Student], Optional[str]]:
        """
        Validiert einen Schüler-Token und gibt den Student zurück.
        
        HIGHLANDER-PRÜFUNG:
        Wenn session_token angegeben ist, wird geprüft ob der Hash mit dem
        gespeicherten active_session_token_hash übereinstimmt.
        Falls nicht: Der Schüler wurde auf einem anderen Gerät eingeloggt → Session ungültig!
        
        Args:
            token: JWT Token
            session_token: Optional Session-Token für Highlander-Prüfung
        
        Returns:
            Tuple von (student, error_message)
            error_message enthält "kicked_out" wenn Session durch anderes Login invalidiert wurde
        """
        payload = decode_token(token)
        
        if not payload:
            return None, "Ungültiger oder abgelaufener Token."
        
        if payload.get("type") != TokenType.STUDENT.value:
            return None, "Falscher Token-Typ."
        
        student_id = payload.get("sub")
        if not student_id:
            return None, "Ungültiger Token."
        
        import uuid
        try:
            uuid_id = uuid.UUID(student_id)
        except ValueError:
            return None, "Ungültige Schüler-ID."
        
        student = self.session.get(Student, uuid_id)
        if not student:
            return None, "Schüler nicht gefunden."
        
        if not student.is_active:
            return None, "Konto deaktiviert."
        
        if student.deleted_at:
            return None, "Konto gelöscht."
        
        # =====================================
        # HIGHLANDER-PRÜFUNG: Session noch gültig? — FAIL-CLOSED
        # =====================================
        # SICHERHEIT: Der Session-Token ist PFLICHT. Fehlte er, ließe sich die
        # Ein-Geräte-Revocation umgehen (gültiges, schuljahr-langes JWT OHNE Token =
        # nicht kickbar). Die PWA liefert ihn als HttpOnly-Cookie immer mit.
        import hmac

        if not session_token:
            return None, "Session abgelaufen. Bitte erneut einloggen."

        received_hash = hash_session_token(session_token)
        stored_hash = student.active_session_token_hash

        if stored_hash is None:
            # Schüler hat sich nirgends eingeloggt - Token ungültig
            return None, "Session abgelaufen. Bitte erneut einloggen."

        # Konstantzeit-Vergleich (Timing-Hygiene bei Geheimnis-Vergleichen)
        if not hmac.compare_digest(received_hash, stored_hash):
            # HIGHLANDER TRIGGERED: Jemand anderes hat sich eingeloggt!
            return None, "kicked_out"  # Spezieller Error-Code für Frontend

        return student, None



# ============================================================================
# TOTP UTILITIES
# ============================================================================

def generate_totp_code(secret: str) -> str:
    """
    Generiert den aktuellen TOTP-Code.
    
    Dieser Code wechselt alle 30 Sekunden und kann
    offline auf dem Gerät des Schülers generiert werden.
    """
    totp = pyotp.TOTP(secret)
    return totp.now()


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Verifiziert einen TOTP-Code.
    
    Erlaubt einen Zeitfenster von ±1 Intervall (30s),
    um Uhrzeit-Differenzen auszugleichen.
    """
    totp = pyotp.TOTP(secret)
    # valid_window=1 erlaubt ±30 Sekunden
    return totp.verify(code, valid_window=1)


def get_totp_uri(secret: str, student_name: str, school_name: str) -> str:
    """
    Generiert eine otpauth:// URI für QR-Code.

    Kann von Auth-Apps (Google Authenticator, etc.) gescannt werden.
    Die PWA selbst nutzt diese URI nicht — sie generiert Codes direkt aus dem Secret.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(
        name=student_name,
        issuer_name=f"Hagen-ID ({school_name})"
    )
