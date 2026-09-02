"""
PWA-Routen für Hagen-ID (Schüler-App)

Diese Routen werden von der Progressive Web App verwendet.
Alle Endpoints benötigen einen gültigen Student-Token.

Endpoints:
    GET  /pwa/me                    - Schülerdaten für Ausweis-Anzeige
    GET  /pwa/photo/{id}            - Schülerfoto als Bild (nur eigenes)
    POST /pwa/photo                 - Eigenes Foto hochladen
    GET  /pwa/schulbescheinigung.pdf - Schulbescheinigung als PDF
    POST /pwa/push/subscribe        - Push-Benachrichtigungen aktivieren

Hinweis: Die QR-Code-Erzeugung läuft vollständig CLIENTSEITIG (die PWA signiert
offline mit ihrem ECDSA-P-256-Key). Die Verifizierung läuft ausschließlich über
GET /check — dort werden TOTP, Highlander-Session, Timestamp-Frische und die
Signatur geprüft. Es gibt bewusst keinen serverseitigen QR- oder Verify-Endpunkt.
"""

from typing import Optional
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Response, Request, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select
from pydantic import BaseModel
import base64
import json
import os
import secrets

# Services und Models
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.auth import AuthService
from services import audit
from routes.auth import get_client_ip
from models import Student, Class, School, Photo, PushSubscription


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter(
    prefix="/pwa",
    tags=["PWA (Schüler-App)"]
)

bearer_scheme = HTTPBearer(auto_error=False)


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class StudentCardData(BaseModel):
    """
    Alle Daten die die PWA für den Ausweis braucht.
    
    Diese Daten werden gecached und ermöglichen Offline-Betrieb.
    """
    # Persönliche Daten
    id: str
    first_name: str
    last_name: str
    dob: str  # Geburtsdatum als String (DD.MM.YYYY)
    
    # Schul-/Klasseninfos
    class_name: Optional[str]
    school_name: Optional[str]
    school_logo_url: Optional[str]
    school_color: Optional[str]  # Primärfarbe für Branding
    
    # Gültigkeit
    valid_until: Optional[str]  # Ablaufdatum als String
    is_teacher: bool  # True wenn Lehrerkollegium
    
    # TOTP für QR-Code
    totp_secret: str
    
    # Foto
    has_photo: bool
    photo_url: Optional[str]
    photo_b64: Optional[str] = None  # Base64-JPEG direkt inline (kein separater Endpoint nötig)

    # Schulbescheinigung: True wenn die Klasse des Schülers dafür freigeschaltet ist
    schulbescheinigung_enabled: bool = False

    # Der QR braucht den Session-Hash-Prefix (Highlander). Da der Session-Token
    # in einem HttpOnly-Cookie steckt (JS-unlesbar), liefert der Server den
    # Prefix hier mit — er steckt ohnehin in jedem QR, also kein zusätzliches Leak.
    session_hash_prefix: Optional[str] = None


class PushSubscribeRequest(BaseModel):
    """Request zum Aktivieren von Push-Benachrichtigungen"""
    endpoint: str
    p256dh: str
    auth: str


class RegisterKeyRequest(BaseModel):
    """
    Request zum Registrieren des geräte-erzeugten Signier-Schlüssels.

    Die PWA erzeugt ihr ECDSA-P-256-Keypair lokal (non-extractable) und schickt
    NUR den Public Key — als 'raw' exportierten, Base64-codierten unkomprimierten
    Punkt (0x04||X||Y, 65 Bytes → 88 Base64-Zeichen). Der Private Key verlässt
    das Gerät nie.
    """
    public_key: str  # Base64(raw uncompressed P-256 point)


# ============================================================================
# DEPENDENCY: Session (wird von main.py überschrieben)
# ============================================================================

def get_session():
    """Placeholder - wird von main.py überschrieben."""
    raise NotImplementedError("Session not configured")


# ============================================================================
# DEPENDENCY: Aktueller Schüler aus Token
# ============================================================================

def get_current_student(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session)
) -> Student:
    """
    Extrahiert den Schüler aus dem Auth-Cookie — mit Header-Fallback.

    Primär kommen `student_token` + `session_token` als **HttpOnly-Cookies**
    (iOS-persistent, XSS-sicher). Als Fallback werden auch Bearer-Token und
    X-Session-Token-Header akzeptiert.

    Ein Ausweis wird nur für existierende UND aktive Schüler ausgeliefert. Die
    Prüfung (nicht gefunden / abgemeldet / deaktiviert) macht
    `validate_student_token`; nach außen geht aber eine **generische** 401
    zurück (kein Status-Leak) — außer beim Highlander-Fall „kicked_out", der
    eine eigene UX braucht.
    """
    # Token: Cookie zuerst, dann Bearer-Header (Fallback)
    token = request.cookies.get("student_token")
    if not token and credentials:
        token = credentials.credentials

    # Session-Token (Highlander): Cookie zuerst, dann Header
    session_token = request.cookies.get("session_token") or request.headers.get("X-Session-Token")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Nicht authentifiziert",
            headers={"WWW-Authenticate": "Bearer"}
        )

    auth_service = AuthService(session)
    student, error = auth_service.validate_student_token(token, session_token=session_token)

    if error:
        # „Anderes Gerät" braucht eine eigene UX (Frontend zeigt einen Hinweis).
        if error == "kicked_out":
            raise HTTPException(
                status_code=401,
                detail="Du wurdest auf einem anderen Gerät angemeldet. Bitte logge dich erneut ein.",
                headers={"WWW-Authenticate": "Bearer", "X-Kicked-Out": "true"}
            )
        # Alle übrigen Fälle (Token ungültig/abgelaufen, Schüler weg/abgemeldet/inaktiv)
        # → generische 401. Kein „Konto deaktiviert/gelöscht"-Leak nach außen.
        raise HTTPException(
            status_code=401,
            detail="Nicht authentifiziert",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # RLS-Mandant = Schule des Schülers (denormalisiert). Inert wenn RLS aus.
    from services import rls as _rls
    _rls.set_tenant(session, student.school_id if student.school_id is not None else 0)

    return student


# ============================================================================
# HELPER: Schulbescheinigung — Konfiguration & Berechtigung
# ============================================================================

def parse_school_features(school: Optional[School]) -> dict:
    """Parst das features_enabled-JSON einer Schule (leeres Dict bei Fehler)."""
    if not school or not school.features_enabled:
        return {}
    try:
        parsed = json.loads(school.features_enabled)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def schulbescheinigung_config(school: Optional[School]) -> dict:
    """
    Liefert die Schulbescheinigungs-Konfiguration einer Schule:
    - enabled:     Master-Schalter (Feature aktiv?)
    - class_ids:   Liste freigeschalteter Klassen-IDs
    - provisional: Vorläufig-Modus (Hinweis + 3 Monate im Fenster 01.08.–31.10.)
    """
    features = parse_school_features(school)
    raw_ids = features.get("schulbescheinigung_class_ids")
    # Robust gegen fehlerhafte/manuell gesetzte Werte (kein Absturz von /pwa/me)
    if not isinstance(raw_ids, list):
        raw_ids = []
    class_ids = []
    for c in raw_ids:
        try:
            class_ids.append(int(c))
        except (ValueError, TypeError):
            continue
    return {
        "enabled": bool(features.get("schulbescheinigung")),
        "class_ids": class_ids,
        "provisional": bool(features.get("schulbescheinigung_provisional")),
    }


def is_schulbescheinigung_enabled_for(student: Student, session: Session) -> bool:
    """True, wenn der Schüler eine Schulbescheinigung erzeugen darf."""
    if not student.class_id:
        return False
    student_class = session.get(Class, student.class_id)
    if not student_class:
        return False
    school = session.get(School, student_class.school_id)
    cfg = schulbescheinigung_config(school)
    return cfg["enabled"] and student.class_id in cfg["class_ids"]


def public_api_base(request: Request) -> str:
    """
    Ermittelt die öffentliche API-Basis-URL für den dauerhaften QR-Link.

    Reihenfolge: API_PUBLIC_URL (empfohlen) > Reverse-Proxy-Header
    (X-Forwarded-Proto/Host) > request.base_url. Wichtig, damit der QR-Code
    hinter dem Proxy nicht auf eine interne http-URL zeigt.
    """
    env = os.getenv("API_PUBLIC_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("X-Forwarded-Proto")
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
    if proto and host:
        # Bei mehreren Werten (Proxy-Kette) den ersten nehmen
        proto = proto.split(",")[0].strip()
        host = host.split(",")[0].strip()
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# ============================================================================
# MAIN ENDPOINTS
# ============================================================================

@router.get("/me", response_model=StudentCardData)
def get_my_card_data(
    request: Request,
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
) -> StudentCardData:
    """
    Holt alle Daten für die Ausweis-Anzeige.

    Die PWA sollte diese Daten cachen (Service Worker),
    damit der Ausweis auch offline funktioniert.
    """
    # Klassen- und Schulinfos laden
    class_name = None
    school_name = None
    school_logo = None
    school_color = "#1a73e8"
    valid_until = None
    is_teacher = False
    school_id_for_trace = None

    if student.class_id:
        student_class = session.get(Class, student.class_id)
        if student_class:
            class_name = student_class.name
            valid_until = student_class.validity_end.strftime("%d.%m.%Y") if student_class.validity_end else None
            is_teacher = student_class.name.lower() == "lehrerkollegium"
            school_id_for_trace = student_class.school_id

            school = session.get(School, student_class.school_id)
            if school:
                school_name = school.name
                school_logo = school.logo_url
                school_color = school.primary_color or "#1a73e8"

    # Ausweis-Load: im Normalbetrieb NICHT geloggt (Selbst-Zugriff). Nur wenn für diese
    # Schule/ID gerade ein Trace läuft, wird der Load samt IP festgehalten.
    audit.record_card_load(
        session, "id_card_loaded",
        school_id=school_id_for_trace,
        subject_id=str(student.id),
        ip=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        extra={"card_type": "teacher" if is_teacher else "student"},
    )
    
    # Foto laden (direkt als Base64 mitliefern)
    photo = session.exec(
        select(Photo).where(Photo.student_id == student.id)
    ).first()

    has_photo = photo is not None and photo.blob_data is not None
    photo_b64 = None
    if has_photo:
        # Data-URI für direkte Anzeige im <img> Tag
        photo_b64 = f"data:image/jpeg;base64,{photo.blob_data}"

    return StudentCardData(
        id=str(student.id),
        first_name=student.first_name,
        last_name=student.last_name,
        dob=student.dob.strftime("%d.%m.%Y") if student.dob else "",
        class_name=class_name,
        school_name=school_name,
        school_logo_url=school_logo,
        school_color=school_color,
        valid_until=valid_until,
        is_teacher=is_teacher,
        totp_secret=student.totp_secret,
        has_photo=has_photo,
        photo_url=f"/pwa/photo/{student.id}" if has_photo else None,
        photo_b64=photo_b64,
        schulbescheinigung_enabled=is_schulbescheinigung_enabled_for(student, session),
        # Prefix des aktiven Session-Hashes (für den QR / Highlander) — 8 Hex-Zeichen.
        session_hash_prefix=(student.active_session_token_hash[:8].lower()
                             if student.active_session_token_hash else None),
    )


@router.post("/register-key")
def register_device_key(
    data: RegisterKeyRequest,
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
):
    """
    Registriert den auf dem Gerät erzeugten ECDSA-P-256-Public-Key.

    SICHERHEITSMODELL:
    Der private Schlüssel entsteht in der PWA (`crypto.subtle.generateKey(..., false, …)`)
    und verlässt das Gerät nie. Hier kommt AUSSCHLIESSLICH der Public Key an; der Server
    signiert nie, er verifiziert nur (siehe services/crypto.py, /check).

    Auth: `get_current_student` erzwingt Bearer-Token UND den Highlander-Session-Token.
    Damit kann nur das aktuell eingeloggte Gerät seinen Schlüssel registrieren — und
    ein neuer Login (neues Gerät) entwertet zuvor den alten Schlüssel (login_student).

    Restrisiko (bewusst akzeptiert): Wer ein gültiges JWT + Session-Token besitzt,
    könnte einen EIGENEN Public Key registrieren. Der Schlüssel ist aber an genau
    diese Highlander-Session gebunden — ein neuer Login (neues Gerät) entwertet ihn.
    """
    from services.crypto import is_valid_public_key

    if not is_valid_public_key(data.public_key):
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Public Key (erwartet Base64 eines rohen P-256-Punkts, 65 Bytes)"
        )

    student.id_public_key = data.public_key
    student.id_key_generated_at = datetime.utcnow()
    session.add(student)
    session.commit()

    return {"success": True, "message": "Geräte-Schlüssel registriert"}


@router.get("/schulbescheinigung.pdf")
def get_schulbescheinigung_pdf(
    request: Request,
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
) -> Response:
    """
    Erzeugt die Schulbescheinigung als PDF (mit dauerhaftem Verifizierungs-QR).

    Voraussetzung: Die Klasse des Schülers ist für die Schulbescheinigung
    freigeschaltet (Master-Toggle + Klassen-Filter der Schule).

    Der eingebettete QR-Code verweist auf /verify-cert und zeigt beim Scannen
    den aktuellen Live-Status (aktiv eingeschrieben) an.
    """
    from services.schulbescheinigung import (
        render_certificate_pdf, school_year_str, is_provisional_window,
        add_months, data_uri_to_bytes, render_template, teacher_service_end,
        DEFAULT_TEMPLATE_STUDENT, DEFAULT_TEMPLATE_TEACHER,
    )

    # Nur aktive Schüler erhalten eine Bescheinigung
    if not student.is_active or student.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Konto ist nicht aktiv")

    if not student.class_id:
        raise HTTPException(status_code=404, detail="Keine Klasse zugeordnet")
    student_class = session.get(Class, student.class_id)
    if not student_class:
        raise HTTPException(status_code=404, detail="Klasse nicht gefunden")
    school = session.get(School, student_class.school_id)

    # Berechtigung serverseitig erzwingen (nicht nur Button-Sichtbarkeit)
    cfg = schulbescheinigung_config(school)
    if not (cfg["enabled"] and student.class_id in cfg["class_ids"]):
        raise HTTPException(
            status_code=403,
            detail="Schulbescheinigung ist für Ihre Klasse nicht freigeschaltet"
        )

    # Dauerhaften Verifizierungs-Token sicherstellen (lazy erzeugt)
    if not student.cert_verify_token:
        student.cert_verify_token = secrets.token_urlsafe(16)
        session.add(student)
        session.commit()
        session.refresh(student)

    today = date.today()

    # Lehrkräfte (Klasse "Lehrerkollegium") erhalten eine DIENSTbescheinigung:
    # eigener Titel/Template, keine Klassen-Zeile, kein "Vorläufig"-Fenster
    # (das Fenster modelliert den Schuljahresbeginn-Übergang von Schülern).
    is_teacher = student_class.name.strip().lower() == "lehrerkollegium"

    is_provisional = (not is_teacher) and cfg["provisional"] and is_provisional_window(today)

    # Gültigkeit: im Vorläufig-Fall 3 Monate, sonst reguläres Ausweis-Enddatum
    if is_provisional:
        valid_until = add_months(today, 3)
    else:
        valid_until = student.validity_end_override or student_class.validity_end

    # Lehrkräfte kurz vor der Pension: Dienstzeit endet formal mit dem Schulhalbjahr,
    # in dem der 67. Geburtstag liegt — die Bescheinigung darf nie länger laufen.
    if is_teacher and student.dob:
        service_end = teacher_service_end(student.dob)
        if valid_until is None or service_end < valid_until:
            valid_until = service_end

    valid_until_str = valid_until.strftime("%d.%m.%Y") if valid_until else "unbegrenzt"

    # Verify-URL: robuste öffentliche API-Basis (Env > Proxy-Header > base_url)
    verify_url = f"{public_api_base(request)}/verify-cert?c={student.cert_verify_token}"

    logo_bytes = data_uri_to_bytes(school.logo_data) if school else None

    # Template wählen (Schuladmin-editierbar, NULL = Standard) und Platzhalter füllen.
    # {Bildungsgang} ist vorgesehen, löst aber vorerst leer auf (SchILD-Feld folgt).
    if is_teacher:
        template = (school.cert_template_teacher if school else None) or DEFAULT_TEMPLATE_TEACHER
    else:
        template = (school.cert_template_student if school else None) or DEFAULT_TEMPLATE_STUDENT
    body = render_template(template, {
        "Name": student.last_name,
        "Vorname": student.first_name,
        "GebDatum": student.dob.strftime("%d.%m.%Y") if student.dob else "",
        "Bildungsgang": "",   # noch nicht im Sync — bewusst leer
        "Klasse": "" if is_teacher else student_class.name,
        "Schule": school.name if school else "",
        "Schuljahr": school_year_str(today),
    })

    pdf_bytes = render_certificate_pdf(
        school_name=school.name if school else "",
        student_name=f"{student.first_name} {student.last_name}",
        dob_str=student.dob.strftime("%d.%m.%Y") if student.dob else "",
        class_name=student_class.name,
        school_year=school_year_str(today),
        issue_date_str=today.strftime("%d.%m.%Y"),
        valid_until_str=valid_until_str,
        is_provisional=is_provisional,
        verify_url=verify_url,
        logo_bytes=logo_bytes,
        contact_email=school.contact_email if school else None,
        primary_color=school.primary_color if school else None,
        body=body,
        is_teacher=is_teacher,
        school_address=school.address if school else None,
        school_number=school.school_number if school else None,
        school_phone=school.phone if school else None,
    )

    # Audit: Schulbescheinigung heruntergeladen (+ID). Selbst-Zugriff → IP nur bei aktivem Trace.
    _school_id = school.id if school else None
    audit.record(
        session, "cert_downloaded",
        school_id=_school_id,
        subject_id=str(student.id),
        target_table="students",
        ip=audit.ip_if_traced(session, school_id=_school_id, subject_id=str(student.id),
                              ip=get_client_ip(request)),
        extra={"provisional": is_provisional},
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{"dienstbescheinigung" if is_teacher else "schulbescheinigung"}.pdf"'}
    )


@router.get("/photo/{student_id}")
def get_student_photo(
    student_id: str,
    current_student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
) -> Response:
    """
    Gibt das Schülerfoto als Bild zurück.
    
    SICHERHEIT:
    Nur der Schüler selbst darf sein Foto abrufen.
    Externe (Kontrolleure) bekommen das Foto via /check Endpoint (eingebettet).
    """
    if str(current_student.id) != student_id:
        raise HTTPException(status_code=403, detail="Zugriff verweigert")
    import uuid
    
    try:
        uuid_id = uuid.UUID(student_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültige ID")
    
    photo = session.exec(
        select(Photo).where(Photo.student_id == uuid_id)
    ).first()
    
    if not photo or not photo.blob_data:
        raise HTTPException(status_code=404, detail="Foto nicht gefunden")
    
    # Base64 decodieren
    try:
        image_data = base64.b64decode(photo.blob_data)
    except Exception:
        raise HTTPException(status_code=500, detail="Foto-Daten beschädigt")
    
    # Content-Type erraten (einfache Heuristik)
    content_type = "image/jpeg"  # Default
    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        content_type = "image/png"
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        content_type = "image/webp"
    
    return Response(
        content=image_data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=86400"  # 24h cachen
        }
    )


# ============================================================================
# FOTO SELF-UPLOAD (NUR LEHRER)
# ============================================================================

@router.post("/photo")
async def upload_own_photo(
    file: UploadFile = File(...),
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
):
    """
    Ermöglicht Lehrern das Hochladen ihres eigenen Fotos.

    Nur für Mitglieder der Klasse "Lehrerkollegium" erlaubt.
    Schüler können ihr Foto nicht selbst ändern.
    """
    # Prüfen: Ist der User ein Lehrer?
    if not student.class_id:
        raise HTTPException(status_code=403, detail="Foto-Upload nicht erlaubt")

    student_class = session.get(Class, student.class_id)
    if not student_class or student_class.name.lower() != "lehrerkollegium":
        raise HTTPException(status_code=403, detail="Foto-Upload ist nur für Lehrkräfte verfügbar")

    # Datei validieren
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Nur Bilddateien erlaubt")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Datei zu groß (max. 10 MB)")

    # Bild komprimieren (max 800px, JPEG 85%)
    from services.image import compress_image, hash_image
    b64_data = compress_image(content)
    photo_hash = hash_image(b64_data)

    existing_photo = session.exec(
        select(Photo).where(Photo.student_id == student.id)
    ).first()

    if existing_photo:
        existing_photo.blob_data = b64_data
        existing_photo.hash = photo_hash
        session.add(existing_photo)
    else:
        photo = Photo(
            student_id=student.id,
            blob_data=b64_data,
            hash=photo_hash
        )
        session.add(photo)

    session.commit()
    return {"success": True, "message": "Foto gespeichert"}


# ============================================================================
# PUSH NOTIFICATIONS
# ============================================================================

@router.post("/push/subscribe")
def subscribe_push(
    data: PushSubscribeRequest,
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
):
    """
    Registriert das Gerät für Push-Benachrichtigungen.
    
    Die PWA ruft dies auf, nachdem der Nutzer die
    Browser-Permission erteilt hat.
    """
    # Prüfen ob bereits registriert (gleicher Endpoint)
    existing = session.exec(
        select(PushSubscription)
        .where(PushSubscription.student_id == student.id)
        .where(PushSubscription.endpoint == data.endpoint)
    ).first()
    
    if existing:
        # Update Keys falls geändert
        existing.p256dh = data.p256dh
        existing.auth = data.auth
        session.add(existing)
    else:
        # Neue Subscription anlegen
        subscription = PushSubscription(
            student_id=student.id,
            endpoint=data.endpoint,
            p256dh=data.p256dh,
            auth=data.auth
        )
        session.add(subscription)
    
    session.commit()
    
    return {"success": True, "message": "Push-Benachrichtigungen aktiviert"}


@router.delete("/push/unsubscribe")
def unsubscribe_push(
    student: Student = Depends(get_current_student),
    session: Session = Depends(get_session)
):
    """Deaktiviert Push-Benachrichtigungen für dieses Gerät."""
    # Alle Subscriptions des Schülers löschen
    subscriptions = session.exec(
        select(PushSubscription).where(PushSubscription.student_id == student.id)
    ).all()
    
    for sub in subscriptions:
        session.delete(sub)
    
    session.commit()

    return {"success": True, "message": "Push-Benachrichtigungen deaktiviert"}
