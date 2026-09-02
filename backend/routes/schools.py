"""
Schools-Routen für Hagen-ID

Nur für Superadmins zugänglich!
Ermöglicht die Verwaltung aller Schulen im System.

Endpoints:
    GET    /schools          - Liste aller Schulen
    GET    /schools/{id}     - Details einer Schule
    POST   /schools          - Neue Schule anlegen
    PUT    /schools/{id}     - Schule bearbeiten
    DELETE /schools/{id}     - Schule löschen
"""

import re
from typing import Optional, List
from datetime import date, datetime
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select, func
from pydantic import BaseModel
import json

# Models
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import School, User, Class, Student, Level, AuditLog, SyncIpAllowlistEntry
from routes.auth import get_current_admin, get_client_ip
from services import ipallow


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter(
    prefix="/schools",
    tags=["Schulen (Superadmin)"]
)


# ============================================================================
# DEPENDENCIES
# ============================================================================

def get_session():
    """Placeholder - wird von main.py überschrieben."""
    raise NotImplementedError("Session not configured")

def require_superadmin(current_user: User = Depends(get_current_admin)) -> User:
    """
    Prüft ob der aktuelle User ein Superadmin ist.
    Wirft 403 wenn nicht Superadmin.
    """
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    
    if role_value.lower() != 'superadmin':
        raise HTTPException(
            status_code=403,
            detail="Nur Superadmins können Schulen verwalten"
        )
    
    return current_user


# ============================================================================
# HELPER: Slug-Sanitizer
# ============================================================================

def sanitize_slug(raw: Optional[str]) -> Optional[str]:
    """
    Bereinigt einen URL-Pfad-Slug.
    Entfernt Spaces, Slashes, Sonderzeichen. Nur [a-z0-9-] erlaubt.
    Leerer String → None.
    """
    if not raw:
        return None
    # Alles was kein Buchstabe, Zahl oder Bindestrich ist, entfernen
    cleaned = re.sub(r'[^a-z0-9-]', '', raw.lower().strip())
    # Mehrfache Bindestriche zusammenfassen, führende/trailing entfernen
    cleaned = re.sub(r'-+', '-', cleaned).strip('-')
    return cleaned or None


# ============================================================================
# HELPER: Logo laden und als Base64-Data-URI cachen
# ============================================================================

# Maximale Logo-Größe (2 MB) — schützt vor riesigen Downloads
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _is_safe_public_url(url: str) -> bool:
    """
    SSRF-Schutz: Erlaubt nur http(s) und keine internen/privaten IP-Adressen.

    Verhindert, dass ein Admin über die Logo-URL interne Dienste anfragt
    (z.B. http://localhost, http://169.254.169.254 Cloud-Metadata).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        # Alle aufgelösten IP-Adressen prüfen
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True
    except Exception:
        return False


def fetch_logo_data_uri(url: Optional[str]) -> Optional[str]:
    """
    Lädt ein Logo von einer öffentlichen URL und gibt es als Base64-Data-URI zurück.

    Wird beim Setzen/Ändern der logo_url aufgerufen, damit das Logo lokal
    verfügbar ist (z.B. für die serverseitige PDF-Schulbescheinigung).

    Gibt None zurück bei Fehler (ungültige/unsichere URL, zu groß, kein Bild,
    Netzwerkfehler). Der Aufrufer sollte dann das alte logo_data behalten.
    """
    import base64
    from io import BytesIO
    from urllib.request import Request as UrlRequest, build_opener, HTTPRedirectHandler

    if not url or not url.strip():
        return None
    url = url.strip()
    if not _is_safe_public_url(url):
        return None

    # SSRF-Schutz: Redirects NICHT folgen. Sonst könnte eine erlaubte öffentliche
    # URL per 3xx auf eine interne Adresse umleiten, die _is_safe_public_url nie
    # geprüft hat. Ein Redirect-Response wird so einfach nicht als Bild geparst.
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        opener = build_opener(_NoRedirect)
        req = UrlRequest(url, headers={"User-Agent": "HagenID-LogoFetch/1.0"})
        with opener.open(req, timeout=8) as resp:
            # Content-Length vorab prüfen (falls der Server ihn liefert)
            length = resp.headers.get("Content-Length")
            if length and int(length) > MAX_LOGO_BYTES:
                return None
            raw = resp.read(MAX_LOGO_BYTES + 1)
        if len(raw) > MAX_LOGO_BYTES:
            return None

        # SVG (Vektorlogo): Pillow kann kein SVG — per Inhalt erkennen und direkt
        # übernehmen. Im <img>-Tag laufen SVG-Skripte nicht; die Quelle ist ohnehin
        # eine admin-konfigurierte URL. Fürs PDF versucht fpdf2 das SVG zu rendern
        # (try/except dort — schlimmstenfalls fehlt das Logo im PDF).
        head = raw[:512].lstrip()
        if head.startswith(b"<?xml") or b"<svg" in head[:256].lower():
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:image/svg+xml;base64,{b64}"

        # Mit Pillow validieren + Format bestimmen (kein Vertrauen auf Content-Type)
        from PIL import Image
        img = Image.open(BytesIO(raw))
        fmt = (img.format or "PNG").lower()
        img.verify()  # wirft bei defekten Bildern

        mime = {
            "jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp",
        }.get(fmt, "image/png")

        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class SchoolPublicInfo(BaseModel):
    """Öffentliche Schulinfo (ohne Auth, für PWA-Login)"""
    id: int
    name: str
    path_slug: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None


class SchoolListItem(BaseModel):
    """Schule für Listenansicht"""
    id: int
    name: str
    path_slug: Optional[str] = None
    primary_color: Optional[str] = None
    student_count: int = 0
    class_count: int = 0


class SchoolDetail(BaseModel):
    """Detailansicht einer Schule"""
    id: int
    name: str
    path_slug: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    contact_email: Optional[str] = None
    pwa_base_url: Optional[str] = None
    # Statistiken
    student_count: int = 0
    class_count: int = 0
    level_count: int = 0
    admin_count: int = 0


class SchoolCreate(BaseModel):
    """Request zum Erstellen einer Schule"""
    name: str
    path_slug: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#1a73e8"
    contact_email: Optional[str] = None
    pwa_base_url: Optional[str] = None


class SchoolUpdate(BaseModel):
    """Request zum Bearbeiten einer Schule"""
    name: Optional[str] = None
    path_slug: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    contact_email: Optional[str] = None
    pwa_base_url: Optional[str] = None


class SchoolSettings(BaseModel):
    """
    Einstellungen einer Schule.
    
    Kann von School-Admins für ihre eigene Schule bearbeitet werden.
    Superadmins können alle Schulen bearbeiten.
    """
    # Gültigkeit
    validity_strategy: str = "half_year"  # "half_year" oder "custom_date"
    validity_custom_date: Optional[date] = None
    
    # Features (als Dict, wird zu/von JSON konvertiert)
    features_enabled: dict = {}
    
    # Kontakt
    contact_email: Optional[str] = None

    # Briefkopf (PDF-Bescheinigungen)
    address: Optional[str] = None          # mehrzeilig: Straße\nPLZ Ort
    school_number: Optional[str] = None    # amtliche Schulnummer
    phone: Optional[str] = None

    # Branding (auch hier editierbar)
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None

    # Bescheinigungs-Templates (None/leer = eingebauter Standardtext).
    # Platzhalter: {Name} {Vorname} {GebDatum} {Bildungsgang} {Klasse} {Schule} {Schuljahr}
    cert_template_student: Optional[str] = None
    cert_template_teacher: Optional[str] = None


@router.get("/{school_id}/settings", response_model=SchoolSettings)
def get_school_settings(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> SchoolSettings:
    """
    Holt die Einstellungen einer Schule.
    
    Zugriffsrechte:
    - School-Admins: Nur eigene Schule
    - Superadmins: Alle Schulen
    """
    # Rollenprüfung
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    is_superadmin = role_value.lower() == 'superadmin'
    
    # Schule laden
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")
    
    # Berechtigung prüfen
    if not is_superadmin and current_user.school_id != school_id:
        raise HTTPException(
            status_code=403,
            detail="Sie können nur die Einstellungen Ihrer eigenen Schule einsehen"
        )
    
    # Features aus JSON parsen
    features = {}
    if school.features_enabled:
        try:
            features = json.loads(school.features_enabled)
        except json.JSONDecodeError:
            features = {}
    
    # validity_strategy: Enum-Wert als String extrahieren, mit Fallback
    validity_strategy_str = school.validity_strategy
    if validity_strategy_str is None:
        # Fallback falls DB-Wert NULL ist (z.B. nach Migration)
        validity_strategy_str = "half_year"
    elif hasattr(validity_strategy_str, 'value'):
        validity_strategy_str = validity_strategy_str.value
    
    return SchoolSettings(
        validity_strategy=validity_strategy_str,
        validity_custom_date=school.validity_custom_date,
        features_enabled=features,
        contact_email=school.contact_email,
        address=school.address,
        school_number=school.school_number,
        phone=school.phone,
        logo_url=school.logo_url,
        primary_color=school.primary_color,
        cert_template_student=school.cert_template_student,
        cert_template_teacher=school.cert_template_teacher
    )


@router.put("/{school_id}/settings", response_model=SchoolSettings)
def update_school_settings(
    school_id: int,
    data: SchoolSettings,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> SchoolSettings:
    """
    Aktualisiert die Einstellungen einer Schule.
    
    Zugriffsrechte:
    - School-Admins: Nur eigene Schule
    - Superadmins: Alle Schulen
    """
    # Rollenprüfung
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    is_superadmin = role_value.lower() == 'superadmin'
    
    # Schule laden
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")
    
    # Berechtigung prüfen
    if not is_superadmin and current_user.school_id != school_id:
        raise HTTPException(
            status_code=403,
            detail="Sie können nur die Einstellungen Ihrer eigenen Schule bearbeiten"
        )
    
    # Felder aktualisieren
    school.validity_strategy = data.validity_strategy
    school.validity_custom_date = data.validity_custom_date
    school.features_enabled = json.dumps(data.features_enabled) if data.features_enabled else None
    school.contact_email = data.contact_email
    school.address = (data.address or "").strip() or None
    school.school_number = (data.school_number or "").strip() or None
    school.phone = (data.phone or "").strip() or None
    # Bescheinigungs-Templates: leerer/Whitespace-Text = zurück auf Standard (NULL)
    school.cert_template_student = (data.cert_template_student or "").strip() or None
    school.cert_template_teacher = (data.cert_template_teacher or "").strip() or None
    
    # Branding auch updaten wenn angegeben
    if data.logo_url is not None:
        # Bei geänderter Logo-URL das lokale Logo-Cache neu laden
        if data.logo_url != school.logo_url:
            school.logo_data = fetch_logo_data_uri(data.logo_url)
        school.logo_url = data.logo_url
    if data.primary_color is not None:
        school.primary_color = data.primary_color

    session.add(school)
    session.commit()
    session.refresh(school)

    # Response bauen
    features = {}
    if school.features_enabled:
        try:
            features = json.loads(school.features_enabled)
        except json.JSONDecodeError:
            features = {}
    
    return SchoolSettings(
        validity_strategy=school.validity_strategy,
        validity_custom_date=school.validity_custom_date,
        features_enabled=features,
        contact_email=school.contact_email,
        address=school.address,
        school_number=school.school_number,
        phone=school.phone,
        logo_url=school.logo_url,
        primary_color=school.primary_color,
        cert_template_student=school.cert_template_student,
        cert_template_teacher=school.cert_template_teacher
    )



# ============================================================================
# HELPER: Statistiken für eine Schule berechnen
# ============================================================================

def get_school_stats(session: Session, school_id: int) -> dict:
    """Berechnet Statistiken für eine Schule."""
    
    # Klassen zählen
    class_count = session.exec(
        select(func.count(Class.id)).where(Class.school_id == school_id)
    ).one()
    
    # Schüler zählen (über Klassen)
    class_ids = session.exec(
        select(Class.id).where(Class.school_id == school_id)
    ).all()
    
    student_count = 0
    if class_ids:
        student_count = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id.in_(class_ids))
            .where(Student.deleted_at.is_(None))
        ).one()
    
    # Levels zählen
    level_count = session.exec(
        select(func.count(Level.id)).where(Level.school_id == school_id)
    ).one()
    
    # Admins zählen
    admin_count = session.exec(
        select(func.count(User.id)).where(User.school_id == school_id)
    ).one()
    
    return {
        "student_count": student_count,
        "class_count": class_count,
        "level_count": level_count,
        "admin_count": admin_count
    }


# ============================================================================
# ÖFFENTLICHE ROUTES (ohne Auth, für PWA)
# ============================================================================

@router.get("/public", response_model=List[SchoolPublicInfo])
def list_schools_public(
    session: Session = Depends(get_session)
) -> List[SchoolPublicInfo]:
    """
    Öffentliche Liste aller Schulen (für PWA-Schulauswahl).
    Keine Authentifizierung nötig.
    """
    schools = session.exec(select(School).order_by(School.name)).all()
    return [
        SchoolPublicInfo(
            id=school.id,
            name=school.name,
            path_slug=school.path_slug,
            logo_url=school.logo_url,
            primary_color=school.primary_color
        )
        for school in schools
    ]


@router.get("/public/{slug}", response_model=SchoolPublicInfo)
def get_school_by_slug(
    slug: str,
    session: Session = Depends(get_session)
) -> SchoolPublicInfo:
    """
    Öffentlich: Schule per URL-Pfad-Slug auflösen.
    Wird von der PWA verwendet um Schulname + Farbe für die Login-Seite zu laden.
    """
    cleaned = sanitize_slug(slug)
    if not cleaned:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    school = session.exec(
        select(School).where(School.path_slug == cleaned)
    ).first()

    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    return SchoolPublicInfo(
        id=school.id,
        name=school.name,
        path_slug=school.path_slug,
        logo_url=school.logo_url,
        primary_color=school.primary_color
    )


# ============================================================================
# ADMIN ROUTES (Auth erforderlich)
# ============================================================================

@router.get("", response_model=List[SchoolListItem])
def list_schools(
    current_user: User = Depends(require_superadmin),
    session: Session = Depends(get_session)
) -> List[SchoolListItem]:
    """
    Listet alle Schulen im System.
    
    Nur für Superadmins zugänglich.
    """
    schools = session.exec(select(School).order_by(School.name)).all()
    
    result = []
    for school in schools:
        stats = get_school_stats(session, school.id)
        result.append(SchoolListItem(
            id=school.id,
            name=school.name,
            path_slug=school.path_slug,
            primary_color=school.primary_color,
            student_count=stats["student_count"],
            class_count=stats["class_count"]
        ))
    
    return result


@router.get("/{school_id}", response_model=SchoolDetail)
def get_school(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> SchoolDetail:
    """
    Holt Details zu EINER Schule.

    Zugriff: Superadmin → jede Schule; School-Admin/Teacher → nur die EIGENE.
    (Die Liste aller Schulen `GET /schools` sowie Create/Update/Delete bleiben
    superadmin-only.)

    Wichtig für den QR-Druck: Die Druckseite (PrintPins) liest hier die
    `pwa_base_url` der eigenen Schule — School-Admins brauchen deshalb
    Lesezugriff auf die eigene Schule.
    """
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if role_value.lower() != 'superadmin' and current_user.school_id != school_id:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Schule")

    school = session.get(School, school_id)
    
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")
    
    stats = get_school_stats(session, school.id)
    
    return SchoolDetail(
        id=school.id,
        name=school.name,
        path_slug=school.path_slug,
        contact_email=school.contact_email,
        logo_url=school.logo_url,
        primary_color=school.primary_color,
        pwa_base_url=school.pwa_base_url,
        **stats
    )


@router.post("", response_model=SchoolDetail, status_code=201)
def create_school(
    data: SchoolCreate,
    current_user: User = Depends(require_superadmin),
    session: Session = Depends(get_session)
) -> SchoolDetail:
    """
    Erstellt eine neue Schule.

    Der path_slug muss eindeutig sein (falls gesetzt).
    """
    # Slug sanitizen
    slug = sanitize_slug(data.path_slug)

    # Uniqueness prüfen (nur wenn Slug gesetzt)
    if slug:
        existing = session.exec(
            select(School).where(School.path_slug == slug)
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"URL-Pfad '{slug}' bereits vergeben"
            )

    # Schule erstellen
    school = School(
        name=data.name,
        path_slug=slug,
        logo_url=data.logo_url,
        # Logo einmalig laden und lokal cachen (für PDF-Schulbescheinigung)
        logo_data=fetch_logo_data_uri(data.logo_url),
        primary_color=data.primary_color,
        contact_email=data.contact_email,
        pwa_base_url=data.pwa_base_url.rstrip("/") if data.pwa_base_url else None
    )

    session.add(school)
    session.commit()
    session.refresh(school)

    return SchoolDetail(
        id=school.id,
        name=school.name,
        path_slug=school.path_slug,
        contact_email=school.contact_email,
        logo_url=school.logo_url,
        primary_color=school.primary_color,
        pwa_base_url=school.pwa_base_url,
        student_count=0,
        class_count=0,
        level_count=0,
        admin_count=0
    )


@router.put("/{school_id}", response_model=SchoolDetail)
def update_school(
    school_id: int,
    data: SchoolUpdate,
    current_user: User = Depends(require_superadmin),
    session: Session = Depends(get_session)
) -> SchoolDetail:
    """
    Aktualisiert eine bestehende Schule.
    """
    school = session.get(School, school_id)
    
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")
    
    # Slug-Check bei Änderung
    if data.path_slug is not None:
        slug = sanitize_slug(data.path_slug)
        if slug and slug != school.path_slug:
            existing = session.exec(
                select(School).where(School.path_slug == slug)
            ).first()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"URL-Pfad '{slug}' bereits vergeben"
                )
        school.path_slug = slug

    # Felder aktualisieren (nur wenn angegeben)
    if data.name is not None:
        school.name = data.name
    if data.logo_url is not None:
        # Bei geänderter Logo-URL das lokale Logo-Cache neu laden
        if data.logo_url != school.logo_url:
            school.logo_data = fetch_logo_data_uri(data.logo_url)
        school.logo_url = data.logo_url
    if data.primary_color is not None:
        school.primary_color = data.primary_color
    if data.contact_email is not None:
        school.contact_email = data.contact_email
    if data.pwa_base_url is not None:
        school.pwa_base_url = data.pwa_base_url.rstrip("/") or None

    session.add(school)
    session.commit()
    session.refresh(school)
    
    stats = get_school_stats(session, school.id)
    
    return SchoolDetail(
        id=school.id,
        name=school.name,
        path_slug=school.path_slug,
        logo_url=school.logo_url,
        primary_color=school.primary_color,
        pwa_base_url=school.pwa_base_url,
        **stats
    )


@router.delete("/{school_id}")
def delete_school(
    school_id: int,
    current_user: User = Depends(require_superadmin),
    session: Session = Depends(get_session)
):
    """
    Löscht eine Schule.
    
    ACHTUNG: Nur möglich wenn keine Schüler, Klassen oder Levels existieren!
    """
    school = session.get(School, school_id)
    
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")
    
    stats = get_school_stats(session, school.id)
    
    # Prüfen ob noch Daten existieren
    if stats["student_count"] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Schule hat noch {stats['student_count']} Schüler. Bitte zuerst alle Schüler löschen."
        )
    
    if stats["class_count"] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Schule hat noch {stats['class_count']} Klassen. Bitte zuerst alle Klassen löschen."
        )
    
    if stats["level_count"] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Schule hat noch {stats['level_count']} Bildungsgänge. Bitte zuerst alle löschen."
        )
    
    # Admins werden nicht automatisch gelöscht - müssen manuell bearbeitet werden
    if stats["admin_count"] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Schule hat noch {stats['admin_count']} Admin-Benutzer. Bitte zuerst reassignen oder löschen."
        )
    
    # Schule löschen
    session.delete(school)
    session.commit()

    return {"message": f"Schule '{school.name}' erfolgreich gelöscht"}


# ============================================================================
# API-KEY VERWALTUNG
# ============================================================================

@router.get("/{school_id}/api-key")
def get_api_key_status(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Gibt den Status des API-Keys zurück (nicht den Key selbst!)."""
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    is_superadmin = role_value.lower() == 'superadmin'

    if not is_superadmin and current_user.school_id != school_id:
        raise HTTPException(status_code=403, detail="Nur für eigene Schule erlaubt")

    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    return {
        "has_key": school.api_key_hash is not None,
        "created_at": school.api_key_created_at.isoformat() if school.api_key_created_at else None
    }


def _require_key_manager(current_user: User, school_id: int) -> str:
    """
    Berechtigung für die API-Key-Verwaltung.

    SICHERHEIT: Mindestens `school_admin` — ein `teacher` darf den Sync-Key NICHT
    ausstellen. Begründung: Der API-Key ist die mächtigste Credential im System —
    er umgeht ALLE Session-Layer (portabel, session-los, kein Highlander, kein
    Rate-Limit, unbegrenzt gültig) und schaltet den kompletten Roster zum Lesen
    UND Schreiben frei. Ein kompromittierter Teacher-Account könnte sich damit
    einen Persistenz-Schlüssel minten, der die eigene Passwort-Änderung und
    Session-Revocation überlebt.
    „teacher = Sekretariat = volle Rechte" rechtfertigt den UI-Zugriff auf
    Schülerdaten — nicht das Ausstellen eines eigenständigen Dauer-Bulk-PII-Credentials.

    Gibt die Rolle als lowercase-String zurück.
    """
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    role_value = str(role_value).lower()

    if role_value == 'superadmin':
        return role_value

    if role_value != 'school_admin':
        raise HTTPException(
            status_code=403,
            detail="Nur Schul-Admins oder Superadmins dürfen API-Keys verwalten"
        )

    if current_user.school_id != school_id:
        raise HTTPException(status_code=403, detail="Nur für eigene Schule erlaubt")

    return role_value


@router.post("/{school_id}/api-key")
def generate_api_key(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Generiert einen neuen API-Key für eine Schule.

    Der Key wird NUR EINMAL angezeigt! Danach nur noch der SHA256-Hash gespeichert.
    Ein neuer Key invalidiert den alten sofort.

    Zugriffsrechte:
    - School-Admins: Nur eigene Schule
    - Superadmins: Alle Schulen
    - Teacher: NICHT erlaubt (siehe _require_key_manager)
    """
    import json

    _require_key_manager(current_user, school_id)

    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    # Neuen API-Key generieren: hgn_sk_ + 32 Zufalls-Bytes als Hex (= 64 Zeichen, 256 Bit)
    raw_key = f"hgn_sk_{secrets.token_hex(32)}"

    # Nur der SHA256-Hash wird gespeichert (der Key selbst nie!).
    # SHA256 ohne Salt ist hier korrekt: High-Entropy-Zufallskey (keine Brute-Force-Fläche)
    # und der Lookup muss deterministisch sein (WHERE api_key_hash = ...).
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    was_replaced = school.api_key_hash is not None

    school.api_key_hash = key_hash
    school.api_key_created_at = datetime.utcnow()
    session.add(school)

    # AUDIT: Die Ausstellung der mächtigsten Credential im System MUSS protokolliert
    # werden — sonst fehlt im Vorfall genau die Info "wer hat wann einen Key gezogen".
    session.add(AuditLog(
        user_id=current_user.id,
        school_id=school_id,
        action="api_key_generated",
        target_table="schools",
        details=json.dumps({
            "school_id": school_id,
            "replaced_existing_key": was_replaced,
        })
    ))
    session.commit()

    return {
        "api_key": raw_key,
        "created_at": school.api_key_created_at.isoformat(),
        "warning": "Diesen Key JETZT sicher speichern! Er wird nicht erneut angezeigt."
    }


@router.delete("/{school_id}/api-key")
def revoke_api_key(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Widerruft den API-Key einer Schule.

    Zugriffsrechte wie beim Ausstellen (mindestens school_admin).
    """
    import json

    _require_key_manager(current_user, school_id)

    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    had_key = school.api_key_hash is not None

    school.api_key_hash = None
    school.api_key_created_at = None
    session.add(school)

    # AUDIT: auch der Widerruf wird protokolliert
    session.add(AuditLog(
        user_id=current_user.id,
        school_id=school_id,
        action="api_key_revoked",
        target_table="schools",
        details=json.dumps({"school_id": school_id, "had_key": had_key})
    ))
    session.commit()

    return {"message": "API-Key widerrufen"}


# ============================================================================
# SYNC-IP-ALLOWLIST (pro Schule) — Verwaltung durch Schul-Admin (eigene Schule)
# ============================================================================
#
# Gleiche Berechtigungsstufe wie die API-Key-Verwaltung (_require_key_manager),
# denn die Allowlist sichert genau diesen Key ab. Default AUS: bestehende Schulen
# bleiben erreichbar, der Schalter ist die Fluchttür (Tests / dynamische IPs).

class AllowlistEntryOut(BaseModel):
    """Ein Allowlist-Eintrag für die Anzeige."""
    id: int
    cidr: str
    note: Optional[str] = None
    created_at: datetime


class AllowlistOut(BaseModel):
    """Allowlist-Status einer Schule + die aktuell erkannte IP des Aufrufers."""
    enabled: bool
    entries: list[AllowlistEntryOut]
    your_ip: Optional[str] = None   # IP des Admin-Browsers (Hilfe fürs Hinzufügen)


class AllowlistToggle(BaseModel):
    enabled: bool


class AllowlistEntryIn(BaseModel):
    cidr: str
    note: Optional[str] = None


def _load_allowlist(session: Session, school_id: int) -> list[SyncIpAllowlistEntry]:
    return session.exec(
        select(SyncIpAllowlistEntry)
        .where(SyncIpAllowlistEntry.school_id == school_id)
        .order_by(SyncIpAllowlistEntry.created_at.desc())
    ).all()


@router.get("/{school_id}/sync-allowlist", response_model=AllowlistOut)
def get_sync_allowlist(
    school_id: int,
    request: Request,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> AllowlistOut:
    """Zeigt Status + Einträge der Sync-IP-Allowlist. Berechtigung wie API-Key-Verwaltung."""
    _require_key_manager(current_user, school_id)
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    entries = _load_allowlist(session, school_id)
    return AllowlistOut(
        enabled=school.sync_ip_allowlist_enabled,
        entries=[AllowlistEntryOut(id=e.id, cidr=e.cidr, note=e.note, created_at=e.created_at)
                 for e in entries],
        your_ip=get_client_ip(request),
    )


@router.put("/{school_id}/sync-allowlist", response_model=AllowlistOut)
def set_sync_allowlist_enabled(
    school_id: int,
    data: AllowlistToggle,
    request: Request,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> AllowlistOut:
    """Schaltet die Allowlist an/aus. Anti-Footgun: Einschalten mit leerer Liste wird abgelehnt."""
    _require_key_manager(current_user, school_id)
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    entries = _load_allowlist(session, school_id)
    if data.enabled and not entries:
        # Sonst würde sich die Schule ausgerechnet mit dem Aktivieren komplett aussperren.
        raise HTTPException(
            status_code=400,
            detail="Bitte zuerst mindestens eine IP/CIDR hinzufügen, bevor die Allowlist aktiviert wird."
        )

    school.sync_ip_allowlist_enabled = bool(data.enabled)
    session.add(school)
    session.add(AuditLog(
        user_id=current_user.id, school_id=school_id,
        action="sync_allowlist_toggled", target_table="schools",
        details=json.dumps({"enabled": school.sync_ip_allowlist_enabled}),
    ))
    session.commit()

    return AllowlistOut(
        enabled=school.sync_ip_allowlist_enabled,
        entries=[AllowlistEntryOut(id=e.id, cidr=e.cidr, note=e.note, created_at=e.created_at)
                 for e in entries],
        your_ip=get_client_ip(request),
    )


@router.post("/{school_id}/sync-allowlist", response_model=AllowlistEntryOut)
def add_sync_allowlist_entry(
    school_id: int,
    data: AllowlistEntryIn,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> AllowlistEntryOut:
    """Fügt eine IP oder einen CIDR-Bereich hinzu (v4/v6). Eingabe wird validiert + normalisiert."""
    _require_key_manager(current_user, school_id)
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    try:
        cidr = ipallow.normalize_cidr(data.cidr)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ungültige IP/CIDR: {data.cidr}")

    # Duplikate vermeiden
    existing = session.exec(
        select(SyncIpAllowlistEntry)
        .where(SyncIpAllowlistEntry.school_id == school_id)
        .where(SyncIpAllowlistEntry.cidr == cidr)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Dieser Bereich ist bereits eingetragen")

    entry = SyncIpAllowlistEntry(
        school_id=school_id, cidr=cidr,
        note=(data.note or "").strip() or None, created_by=current_user.id,
    )
    session.add(entry)
    session.add(AuditLog(
        user_id=current_user.id, school_id=school_id,
        action="sync_allowlist_added", target_table="schools",
        details=json.dumps({"cidr": cidr}),
    ))
    session.commit()
    session.refresh(entry)

    return AllowlistEntryOut(id=entry.id, cidr=entry.cidr, note=entry.note, created_at=entry.created_at)


@router.delete("/{school_id}/sync-allowlist/{entry_id}")
def delete_sync_allowlist_entry(
    school_id: int,
    entry_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict:
    """
    Entfernt einen Eintrag. Wird damit die Liste leer, schaltet sich die Allowlist
    automatisch AUS (sonst wäre die Schule ab sofort komplett gesperrt).
    """
    _require_key_manager(current_user, school_id)
    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    entry = session.get(SyncIpAllowlistEntry, entry_id)
    if not entry or entry.school_id != school_id:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")

    session.delete(entry)
    session.flush()

    # Anti-Lockout: keine Einträge mehr → Allowlist deaktivieren
    auto_disabled = False
    remaining = _load_allowlist(session, school_id)
    if not remaining and school.sync_ip_allowlist_enabled:
        school.sync_ip_allowlist_enabled = False
        session.add(school)
        auto_disabled = True

    session.add(AuditLog(
        user_id=current_user.id, school_id=school_id,
        action="sync_allowlist_removed", target_table="schools",
        details=json.dumps({"cidr": entry.cidr, "auto_disabled": auto_disabled}),
    ))
    session.commit()

    return {"message": "Eintrag entfernt", "auto_disabled": auto_disabled}


# ============================================================================
# LOGO: Lokalen Cache aus der aktuellen logo_url neu laden
# ============================================================================

@router.post("/{school_id}/logo/refresh")
def refresh_logo(
    school_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Lädt das Schullogo aus der hinterlegten logo_url neu und cached es lokal.

    Nützlich, wenn sich das Bild hinter der URL geändert hat (die URL selbst
    aber gleich bleibt). Das lokale Logo wird u.a. für die PDF-Schulbescheinigung
    verwendet.

    Zugriffsrechte:
    - School-Admins: Nur eigene Schule
    - Superadmins: Alle Schulen
    """
    role_value = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    is_superadmin = role_value.lower() == 'superadmin'

    if not is_superadmin and current_user.school_id != school_id:
        raise HTTPException(status_code=403, detail="Nur für eigene Schule erlaubt")

    school = session.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    if not school.logo_url:
        raise HTTPException(status_code=400, detail="Keine Logo-URL hinterlegt")

    logo_data = fetch_logo_data_uri(school.logo_url)
    if not logo_data:
        raise HTTPException(
            status_code=400,
            detail="Logo konnte nicht geladen werden (ungültige/nicht erreichbare URL oder kein Bild)"
        )

    school.logo_data = logo_data
    session.add(school)
    session.commit()

    return {"message": "Logo aktualisiert", "has_logo": True}



