"""
Authentifizierungs-Routen für Hagen-ID

Endpoints:
    POST /auth/admin/login    - Admin-Login mit E-Mail + Passwort
    POST /auth/admin/logout   - Admin-Logout (löscht das Cookie)
    POST /auth/student/login  - Schüler-Login mit Nachname + Geburtsdatum + PIN
    POST /auth/student/logout - Schüler-Logout (löscht die Cookies)
    GET  /auth/me             - Aktueller Benutzer (aus Token)
    /auth/admin/users         - User-Verwaltung (Liste, Anlegen, Bearbeiten, Löschen)
"""

import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select
from pydantic import BaseModel, EmailStr

# Services
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.auth import (
    AuthService, 
    decode_token, 
    TokenType,
    generate_totp_code,
    get_totp_uri
)
from models import User, Student, Class, School, UserRole


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter(
    prefix="/auth",
    tags=["Authentifizierung"]
)

# Bearer Token Schema für Swagger UI
bearer_scheme = HTTPBearer(auto_error=False)


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class AdminLoginRequest(BaseModel):
    """
    Request für Admin-Login.

    Hier bewusst `str` statt `EmailStr`: Ein Login-Endpoint darf die Kennung nicht
    vorab per Format-Validierung mit 422 ablehnen — sonst bekommt der Nutzer bei
    einer krummen Eingabe einen kryptischen 422-Validierungsfehler (Array!) statt
    eines klaren „Anmeldedaten ungültig" (401). Die Kennung ist ohnehin kein
    Sicherheitsmerkmal; der Login schlägt bei Nichttreffer sauber mit 401 fehl.
    Das Anlegen von Admins validiert die E-Mail weiterhin streng (CreateAdminRequest).
    """
    email: str
    password: str


class StudentLoginRequest(BaseModel):
    """
    Request für Schüler-Login (Passwordless).

    Der Schüler gibt Nachname + Geburtsdatum + PIN ein.
    Die PIN ist eine 6-stellige Nummer, die auf dem ausgedruckten Ausweis steht.
    school_id ist optional — wenn nicht angegeben, wird global gesucht.
    """
    last_name: str
    dob: date  # Format: YYYY-MM-DD
    school_id: Optional[int] = None  # Optional: Auto-Erkennung wenn nicht gesetzt
    login_pin: str  # 6-stellige PIN (ersetzt school_internal_id)


class LoginResponse(BaseModel):
    """Response nach erfolgreichen Login"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # Sekunden bis Ablauf
    user_type: str   # "admin" oder "student"


class StudentLoginResponse(LoginResponse):
    """
    Erweiterter Response für Schüler (TOTP + Session-Token).

    SICHERHEIT: Hier wird bewusst KEIN privater Schlüssel ausgeliefert.
    Würde der Server das Signier-Keypair erzeugen und den privaten Teil
    übertragen, kennten ihn der Server, der TLS-terminierende Proxy und jedes
    Log. Die PWA erzeugt ihr Signier-Keypair daher selbst auf dem Gerät
    (non-extractable) und registriert nur den Public Key via POST /pwa/register-key.
    """
    totp_secret: str          # Für die PWA zum QR-Code generieren
    session_token: str        # Highlander Session-Token - muss bei jedem Request mitgesendet werden!


class AdminMeResponse(BaseModel):
    """Aktuelle Admin-Infos"""
    id: int
    email: str
    role: str
    school_id: Optional[int]
    school_name: Optional[str]


class StudentMeResponse(BaseModel):
    """Aktuelle Schüler-Infos für die PWA"""
    id: str
    first_name: str
    last_name: str
    class_name: Optional[str]
    school_name: Optional[str]
    totp_secret: str
    current_totp_code: str  # Aktueller TOTP-Code (wechselt alle 30s)
    photo_url: Optional[str]  # URL zum Foto (falls vorhanden)


class ErrorResponse(BaseModel):
    """Fehler-Response"""
    detail: str
    remaining_attempts: Optional[int] = None


# ============================================================================
# DEPENDENCY: Session (wird von main.py überschrieben)
# ============================================================================

def get_session():
    """Placeholder - wird von main.py überschrieben."""
    raise NotImplementedError("Session not configured")


# ============================================================================
# DEPENDENCY: Aktueller User aus Token
# ============================================================================

def get_current_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: Session = Depends(get_session)
) -> User:
    """
    Dependency: Extrahiert Admin-User aus Cookie oder Bearer Token.
    """
    token = None
    
    # 1. Cookie (Priorität für Browser)
    token = request.cookies.get("admin_token")
    
    # 2. Bearer Token (Fallback für API-Clients)
    if not token and credentials:
        token = credentials.credentials
        
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Nicht authentifiziert",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    auth_service = AuthService(session)
    user, error = auth_service.validate_admin_token(token)

    if error:
        raise HTTPException(
            status_code=401,
            detail=error,
            headers={"WWW-Authenticate": "Bearer"}
        )

    # RLS-Mandant für die Handler-Queries setzen (inert wenn RLS aus / SQLite).
    # Superadmin → kein Mandant → privilegiert (Bypass). Nicht-Superadmin ohne Schule
    # (sollte durch authz.py nie vorkommen) → 0 = fail-closed (sieht nichts).
    from services import rls as _rls
    role = user.role.value if hasattr(user.role, "value") else user.role
    if str(role).lower() == "superadmin":
        _rls.clear_tenant(session)
    else:
        _rls.set_tenant(session, user.school_id if user.school_id is not None else 0)

    return user


def get_current_student(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session)
) -> Student:
    """
    Dependency: Extrahiert Student aus Bearer Token.
    
    HIGHLANDER-SYSTEM:
    Prüft zusätzlich den X-Session-Token Header gegen die DB.
    Falls jemand anderes sich eingeloggt hat, wird 401 zurückgegeben.
    
    Verwendung in Routes:
        @router.get("/my-card")
        def my_card(student: Student = Depends(get_current_student)):
            ...
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Nicht authentifiziert",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Session-Token aus Header extrahieren (für Highlander-Prüfung)
    session_token = request.headers.get("X-Session-Token")
    
    auth_service = AuthService(session)
    student, error = auth_service.validate_student_token(
        credentials.credentials,
        session_token=session_token
    )
    
    if error:
        # Spezielle Behandlung für "kicked_out" - anderes Gerät hat sich eingeloggt
        if error == "kicked_out":
            raise HTTPException(
                status_code=401,
                detail="Du wurdest auf einem anderen Gerät angemeldet. Bitte logge dich erneut ein.",
                headers={"WWW-Authenticate": "Bearer", "X-Kicked-Out": "true"}
            )
        raise HTTPException(
            status_code=401,
            detail=error,
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return student


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session)
) -> Optional[dict]:
    """
    Dependency: Gibt User-Info zurück falls Token gültig, sonst None.
    Für Endpoints die optional authentifiziert sein können.
    """
    if not credentials:
        return None
    
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    
    return payload


# ============================================================================
# HELPER: Client-IP extrahieren
# ============================================================================

# Anzahl vertrauenswürdiger Reverse-Proxys, die X-Forwarded-For anhängen.
# Coolify/Traefik: i. d. R. 1. Muss zur tatsächlichen Proxy-Kette passen.
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))


def get_client_ip(request: Request) -> str:
    """
    Extrahiert die echte Client-IP (berücksichtigt Reverse Proxy).

    SICHERHEIT: X-Forwarded-For ist eine Liste "client, proxy1, proxy2, ...".
    Der Client kann die LINKEN Einträge frei fälschen — der eigene Proxy hängt
    die tatsächliche Quell-IP RECHTS an. Wir nehmen daher den Eintrag, den unser
    vertrauenswürdiger Proxy angehängt hat (von rechts, hinter TRUSTED_PROXY_HOPS).
    Sonst ließe sich das Rate-Limiting über gefälschte XFF-Header umgehen.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        chain = [p.strip() for p in xff.split(",") if p.strip()]
        # Index von rechts: hinter den vertrauten Hops steht die echte Client-IP
        idx = len(chain) - TRUSTED_PROXY_HOPS
        if 0 <= idx < len(chain):
            return chain[idx]

    # Fallback: Direkte Verbindung (kein/leerer Proxy-Header)
    return request.client.host if request.client else "unknown"


# ============================================================================
# LOGIN ROUTES
# ============================================================================

def _auth_cookie_common() -> dict:
    """Gemeinsame Cookie-Attribute (HttpOnly, Secure, SameSite, optional Domain)."""
    kw = dict(
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="lax",
    )
    domain = os.getenv("COOKIE_DOMAIN")  # z.B. ".example.com" für Cross-Subdomain
    if domain:
        kw["domain"] = domain
    return kw


def _set_student_cookies(response: Response, token: str, session_token: str) -> None:
    """
    Setzt die Schüler-Auth als HttpOnly-Cookies.

    Ablauf = Ablauf des JWT (= Ausweis-/Schuljahresende, in login_student gesetzt).
    Server-gesetzte HttpOnly-Cookies sind auf iOS von der 7-Tage-ITP-Löschung
    ausgenommen → die PWA bleibt über das Schuljahr eingeloggt.
    """
    import time as _time
    from services.auth import JWT_EXPIRE_MINUTES
    payload = decode_token(token)
    if payload and payload.get("exp"):
        max_age = max(60, int(payload["exp"]) - int(_time.time()))
    else:
        max_age = JWT_EXPIRE_MINUTES * 60
    common = _auth_cookie_common()
    response.set_cookie(key="student_token", value=token, max_age=max_age, **common)
    response.set_cookie(key="session_token", value=session_token, max_age=max_age, **common)


@router.post("/admin/login")
def admin_login(
    data: AdminLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session)
):
    """
    Admin-Login mit E-Mail und Passwort.
    
    Setzt ein HttpOnly-Cookie mit dem JWT-Token.
    """
    client_ip = get_client_ip(request)
    
    auth_service = AuthService(session)
    token, error = auth_service.login_admin(
        email=data.email,
        password=data.password,
        client_ip=client_ip
    )
    
    if error:
        raise HTTPException(status_code=401, detail=error)
    
    from services.auth import JWT_EXPIRE_MINUTES

    # Cookie setzen (Secure, HttpOnly, SameSite, Cross-Subdomain)
    cookie_domain = os.getenv("COOKIE_DOMAIN")  # z.B. ".example.com" für Cross-Subdomain
    cookie_kwargs = dict(
        key="admin_token",
        value=token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="lax",
        max_age=JWT_EXPIRE_MINUTES * 60
    )
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain
    response.set_cookie(**cookie_kwargs)
    
    return {"message": "Login erfolgreich", "user_type": "admin"}


@router.post("/admin/logout")
def admin_logout(response: Response):
    """
    Loggt den Admin aus (löscht das Cookie).
    """
    cookie_domain = os.getenv("COOKIE_DOMAIN")
    delete_kwargs = dict(
        key="admin_token",
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="lax"
    )
    if cookie_domain:
        delete_kwargs["domain"] = cookie_domain
    response.delete_cookie(**delete_kwargs)
    return {"message": "Erfolgreich ausgeloggt"}


@router.post("/student/logout")
def student_logout(response: Response):
    """
    Loggt den Schüler aus (löscht die HttpOnly-Cookies).

    Serverseitig nötig, weil die PWA die Cookies selbst nicht löschen kann (HttpOnly).
    Die Highlander-Session in der DB bleibt bestehen, bis das Gerät sich neu einloggt.
    """
    common = _auth_cookie_common()
    # delete_cookie akzeptiert dieselben Attribute (ohne max_age)
    for key in ("student_token", "session_token"):
        response.delete_cookie(key=key, **common)
    return {"message": "Erfolgreich ausgeloggt"}


@router.post("/student/login", response_model=StudentLoginResponse)
def student_login(
    data: StudentLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session)
) -> StudentLoginResponse:
    """
    Schüler-Login (Passwordless).
    
    Schüler authentifizieren sich mit Nachname + Geburtsdatum.
    Bei Erfolg erhält die PWA:
    - JWT Token (für API-Zugriff)
    - TOTP Secret (für Offline-QR-Code-Generierung)
    - Session-Token (MUSS bei jedem Request als X-Session-Token Header mitgesendet werden!)
    
    HIGHLANDER-PRINZIP:
    Bei jedem Login wird ein neuer Session-Token generiert.
    Das invalidiert automatisch alle vorherigen Sessions auf anderen Geräten!
    
    Nach 5 Fehlversuchen wird für 15 Minuten blockiert.
    """
    client_ip = get_client_ip(request)
    
    auth_service = AuthService(session)
    token, totp_secret, session_token, error = auth_service.login_student(
        last_name=data.last_name,
        dob=data.dob,
        school_id=data.school_id,
        client_ip=client_ip,
        login_pin=data.login_pin
    )

    if error:
        raise HTTPException(status_code=401, detail=error)

    # Auth als HttpOnly-Cookies setzen (iOS-persistent, XSS-sicher).
    # Der Body liefert die Token aus Kompatibilitätsgründen zusätzlich mit —
    # die PWA selbst nutzt die Cookies (credentials: 'include').
    _set_student_cookies(response, token, session_token)

    import time as _time
    payload = decode_token(token)
    expires_in = max(60, int(payload["exp"]) - int(_time.time())) if payload and payload.get("exp") else 0

    return StudentLoginResponse(
        access_token=token,
        expires_in=expires_in,
        user_type="student",
        totp_secret=totp_secret,
        session_token=session_token
    )


# ============================================================================
# CURRENT USER ROUTES
# ============================================================================

@router.get("/me")
def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session)
):
    """
    Gibt Infos zum aktuell eingeloggten Benutzer zurück.
    
    Erkennt automatisch ob Admin oder Schüler anhand des Token-Typs.
    
    Authentifizierung:
    - Admins: Cookie (admin_token) oder Bearer Token
    - Schüler: Bearer Token
    """
    token = None
    
    # 1. Cookie (für Admin-Browser-Sessions)
    token = request.cookies.get("admin_token")
    
    # 2. Bearer Token (für API-Clients und Schüler-PWA)
    if not token and credentials:
        token = credentials.credentials
    
    if not token:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Ungültiger Token")
    
    token_type = payload.get("type")
    
    if token_type == TokenType.ADMIN.value:
        # Admin
        auth_service = AuthService(session)
        user, error = auth_service.validate_admin_token(token)
        if error:
            raise HTTPException(status_code=401, detail=error)
        
        # School-Name holen
        school_name = None
        if user.school_id:
            school = session.get(School, user.school_id)
            school_name = school.name if school else None
        
        return AdminMeResponse(
            id=user.id,
            email=user.email,
            role=user.role.value if hasattr(user.role, 'value') else user.role,
            school_id=user.school_id,
            school_name=school_name
        )
    
    elif token_type == TokenType.STUDENT.value:
        # Schüler
        auth_service = AuthService(session)
        student, error = auth_service.validate_student_token(token)
        if error:
            raise HTTPException(status_code=401, detail=error)
        
        # Klassen- und Schulname holen
        class_name = None
        school_name = None
        if student.class_id:
            student_class = session.get(Class, student.class_id)
            if student_class:
                class_name = student_class.name
                school = session.get(School, student_class.school_id)
                school_name = school.name if school else None
        
        # Aktuellen TOTP-Code generieren
        current_code = generate_totp_code(student.totp_secret) if student.totp_secret else "000000"
        
        return StudentMeResponse(
            id=str(student.id),
            first_name=student.first_name,
            last_name=student.last_name,
            class_name=class_name,
            school_name=school_name,
            totp_secret=student.totp_secret,
            current_totp_code=current_code,
            photo_url=f"/pwa/photo/{student.id}" if student.photo else None
        )
    
    else:
        raise HTTPException(status_code=401, detail="Unbekannter Token-Typ")


# ============================================================================
# ADMIN-ONLY: User-Verwaltung
# ============================================================================

# bcrypt verarbeitet maximal 72 Bytes — längere Passwörter würden einen
# ValueError werfen. Wir validieren daher explizit vorab.
MAX_PASSWORD_BYTES = 72

# Erlaubte Rollen-Werte (lowercase), abgeleitet aus dem UserRole-Enum.
VALID_ROLES = {r.value for r in UserRole}  # {"superadmin", "school_admin", "teacher"}


class CreateAdminRequest(BaseModel):
    """Request zum Erstellen eines neuen Admins/Users"""
    email: EmailStr
    password: str
    # Optional: Superadmins haben KEINE Schule (school_id = NULL).
    # Für school_admin/teacher ist die Schule dagegen Pflicht.
    school_id: Optional[int] = None
    role: str = "teacher"  # teacher, school_admin, superadmin


class UpdateAdminRequest(BaseModel):
    """
    Request zum Bearbeiten eines bestehenden Users (Partial-Update).

    Alle Felder optional — nur explizit gesendete Felder werden geändert.
    Ein leeres/nicht gesendetes 'password' lässt das Passwort unverändert.
    """
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    school_id: Optional[int] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class AdminUserResponse(BaseModel):
    """
    User-Infos für die Verwaltungs-UI (Liste + Detail).

    Enthält NIEMALS das Passwort bzw. den Hash.
    """
    id: int
    email: str
    role: str
    school_id: Optional[int]
    school_name: Optional[str]
    is_active: bool


# ============================================================================
# HELPER: User-Verwaltung
# ============================================================================

def _role_str(user: User) -> str:
    """Gibt den Rollen-Wert als lowercase-String zurück (Enum ODER String)."""
    role = user.role.value if hasattr(user.role, "value") else user.role
    return str(role).lower()


def _build_admin_user_response(user: User, session: Session) -> AdminUserResponse:
    """Baut das Response-Objekt inkl. Schulname (falls vorhanden)."""
    school_name = None
    if user.school_id:
        school = session.get(School, user.school_id)
        school_name = school.name if school else None
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        role=_role_str(user),
        school_id=user.school_id,
        school_name=school_name,
        is_active=user.is_active
    )


def _count_active_superadmins(session: Session, exclude_id: Optional[int] = None) -> int:
    """
    Zählt aktive Superadmins (optional ohne einen bestimmten User).

    Rollen-Vergleich bewusst in Python (nicht in SQL), da die role-Spalte
    je nach Datensatz als Enum oder String vorliegt — analog zu schools.py.
    """
    users = session.exec(select(User)).all()
    return sum(
        1 for u in users
        if u.is_active and _role_str(u) == "superadmin" and u.id != exclude_id
    )


@router.get("/admin/users", response_model=List[AdminUserResponse])
def list_admin_users(
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> List[AdminUserResponse]:
    """
    Listet Admin-User auf.

    - superadmin: sieht alle User systemweit
    - school_admin: sieht nur User der eigenen Schule
    - teacher: keine Berechtigung
    """
    actor_role = _role_str(current_admin)
    if actor_role == "superadmin":
        users = session.exec(select(User).order_by(User.email)).all()
    elif actor_role == "school_admin":
        users = session.exec(
            select(User)
            .where(User.school_id == current_admin.school_id)
            .order_by(User.email)
        ).all()
    else:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    return [_build_admin_user_response(u, session) for u in users]


@router.get("/admin/users/{user_id}", response_model=AdminUserResponse)
def get_admin_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> AdminUserResponse:
    """Holt einen einzelnen User (für das Bearbeiten-Formular)."""
    actor_role = _role_str(current_admin)
    if actor_role not in ("superadmin", "school_admin"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    target = session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    # school_admin darf nur eigene Schule sehen (und keine Superadmins)
    if actor_role == "school_admin":
        if target.school_id != current_admin.school_id or _role_str(target) == "superadmin":
            raise HTTPException(status_code=403, detail="Keine Berechtigung")

    return _build_admin_user_response(target, session)


@router.post("/admin/create", response_model=AdminUserResponse)
def create_admin(
    data: CreateAdminRequest,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> AdminUserResponse:
    """
    Erstellt einen neuen Admin-User.

    Nur für school_admin oder superadmin.
    - Superadmins haben keine Schule (school_id = NULL).
    - school_admin/teacher benötigen eine gültige Schule.
    - Nur ein Superadmin darf weitere Superadmins anlegen.
    """
    from services.auth import hash_password

    actor_role = _role_str(current_admin)
    if actor_role not in ("superadmin", "school_admin"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    # Rolle validieren
    role = (data.role or "teacher").lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Ungültige Rolle")

    # Rolle <-> Schule-Kohärenz + Berechtigungen
    if role == "superadmin":
        if actor_role != "superadmin":
            raise HTTPException(status_code=403, detail="Kann keinen Superadmin erstellen")
        school_id = None  # Superadmins sind schulübergreifend
    else:
        # school_admin darf nur für die eigene Schule anlegen
        school_id = current_admin.school_id if actor_role == "school_admin" else data.school_id
        if school_id is None:
            raise HTTPException(status_code=400, detail="Für diese Rolle ist eine Schule erforderlich")
        if session.get(School, school_id) is None:
            raise HTTPException(status_code=400, detail="Schule nicht gefunden")

    # Passwort-Länge prüfen (bcrypt-Limit)
    if not data.password or len(data.password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail="Passwort fehlt oder ist zu lang (max. 72 Bytes)")

    # E-Mail-Eindeutigkeit
    existing = session.exec(
        select(User).where(User.email == data.email.lower())
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")

    new_user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        school_id=school_id,
        role=role,
        is_active=True,
        password_changed_at=datetime.utcnow()
    )
    session.add(new_user)
    session.commit()
    session.refresh(new_user)

    return _build_admin_user_response(new_user, session)


@router.patch("/admin/users/{user_id}", response_model=AdminUserResponse)
def update_admin_user(
    user_id: int,
    data: UpdateAdminRequest,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> AdminUserResponse:
    """
    Bearbeitet einen bestehenden User (inkl. Superadmin).

    Schutzmechanismen:
    - Nur superadmin/school_admin dürfen bearbeiten.
    - school_admin nur eigene Schule, keine Superadmins, keine Rollen-Eskalation.
    - Der letzte aktive Superadmin kann nicht herabgestuft/deaktiviert werden.
    - Man kann sich nicht selbst deaktivieren (Aussperr-Schutz).
    - Passwörter werden mit bcrypt gehasht, niemals im Klartext gespeichert.
    """
    from services.auth import hash_password

    actor_role = _role_str(current_admin)
    if actor_role not in ("superadmin", "school_admin"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    target = session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    target_role = _role_str(target)

    # school_admin-Beschränkungen
    if actor_role == "school_admin":
        if target.school_id != current_admin.school_id:
            raise HTTPException(status_code=403, detail="Nur für eigene Schule erlaubt")
        if target_role == "superadmin":
            raise HTTPException(status_code=403, detail="Kann keinen Superadmin bearbeiten")

    # Nur explizit gesendete Felder berücksichtigen (echtes Partial-Update)
    updates = data.model_dump(exclude_unset=True)

    # Neue Rolle bestimmen + validieren
    new_role = target_role
    if updates.get("role") is not None:
        candidate = str(updates["role"]).lower()
        if candidate not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Ungültige Rolle")
        if actor_role == "school_admin" and candidate == "superadmin":
            raise HTTPException(status_code=403, detail="Kann keinen Superadmin erstellen")
        new_role = candidate

    # Schutz: letzter aktiver Superadmin darf nicht wegfallen
    if target_role == "superadmin":
        losing_superadmin = new_role != "superadmin"
        deactivating = updates.get("is_active") is False
        if (losing_superadmin or deactivating) and _count_active_superadmins(session, exclude_id=target.id) == 0:
            raise HTTPException(
                status_code=400,
                detail="Der letzte aktive Superadmin kann nicht herabgestuft oder deaktiviert werden"
            )

    # Aussperr-Schutz: sich selbst nicht deaktivieren
    if updates.get("is_active") is False and target.id == current_admin.id:
        raise HTTPException(status_code=400, detail="Sie können sich nicht selbst deaktivieren")

    # Rolle <-> Schule-Kohärenz
    if new_role == "superadmin":
        new_school_id = None
    elif actor_role == "school_admin":
        # school_admin kann User nicht in andere Schulen verschieben
        new_school_id = current_admin.school_id
    else:
        new_school_id = updates["school_id"] if "school_id" in updates else target.school_id
        if new_school_id is None:
            raise HTTPException(status_code=400, detail="Für diese Rolle ist eine Schule erforderlich")
        if session.get(School, new_school_id) is None:
            raise HTTPException(status_code=400, detail="Schule nicht gefunden")

    # E-Mail ändern (mit Eindeutigkeits-Prüfung)
    if updates.get("email"):
        new_email = str(updates["email"]).lower()
        if new_email != target.email:
            existing = session.exec(
                select(User).where(User.email == new_email)
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="E-Mail bereits registriert")
            target.email = new_email

    # Passwort ändern — nur wenn ein nicht-leeres Passwort gesendet wurde
    if updates.get("password"):
        pw = updates["password"]
        if len(pw.encode()) > MAX_PASSWORD_BYTES:
            raise HTTPException(status_code=400, detail="Passwort ist zu lang (max. 72 Bytes)")
        target.password_hash = hash_password(pw)
        target.password_changed_at = datetime.utcnow()  # entwertet ältere Tokens des Ziels

    target.role = new_role
    target.school_id = new_school_id
    if updates.get("is_active") is not None:
        target.is_active = updates["is_active"]

    session.add(target)
    session.commit()
    session.refresh(target)

    return _build_admin_user_response(target, session)


@router.delete("/admin/users/{user_id}")
def delete_admin_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Löscht einen User.

    Schutzmechanismen:
    - Nur superadmin/school_admin.
    - school_admin nur eigene Schule, keine Superadmins.
    - Man kann sich nicht selbst löschen.
    - Der letzte aktive Superadmin kann nicht gelöscht werden.
    """
    actor_role = _role_str(current_admin)
    if actor_role not in ("superadmin", "school_admin"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    target = session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    if target.id == current_admin.id:
        raise HTTPException(status_code=400, detail="Sie können sich nicht selbst löschen")

    target_role = _role_str(target)

    if actor_role == "school_admin":
        if target.school_id != current_admin.school_id:
            raise HTTPException(status_code=403, detail="Nur für eigene Schule erlaubt")
        if target_role == "superadmin":
            raise HTTPException(status_code=403, detail="Kann keinen Superadmin löschen")

    # Schutz: letzten aktiven Superadmin nicht löschen
    if target_role == "superadmin" and _count_active_superadmins(session, exclude_id=target.id) == 0:
        raise HTTPException(
            status_code=400,
            detail="Der letzte aktive Superadmin kann nicht gelöscht werden"
        )

    session.delete(target)
    session.commit()

    return {"message": "Benutzer gelöscht"}
