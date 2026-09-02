"""
Sync-API für externe Tools (z.B. Windows Desktop Client)

Ermöglicht die programmatische Synchronisation von Schülerdaten
zwischen Schulverwaltungssoftware (SchILD) und Hagen-ID.

Authentifizierung: API-Key per X-API-Key Header

Endpoints:
    GET  /api/sync/manifest  - Aktueller Datenbestand (Hashes für Diff)
    POST /api/sync/new       - Neue Schüler anlegen (Bulk)
    POST /api/sync/change    - Bestehende Schüler aktualisieren (Bulk)
    POST /api/sync/suspend   - Schüler deaktivieren (Bulk)
"""

import hashlib
from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlmodel import Session, select, func
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import School, Student, Class, Photo, AuditLog, SyncIpAllowlistEntry
from routes.auth import get_client_ip
from services import ipallow, audit

# Lehrer sind Student-Datensätze in der Klasse "Lehrerkollegium" und werden
# (Sync-Tool = Source of Truth) wie Schüler über die Sync-API verwaltet — der
# Klassenname ist das Signal für "Lehrer". Die Klasse erhält beim Anlegen die
# Lehrer-Gültigkeit (heute + 5 Jahre); siehe get_or_create_class_id().
TEACHER_CLASS_NAME = "Lehrerkollegium"


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter(
    prefix="/api/sync",
    tags=["Sync API (Externe Tools)"]
)


# ============================================================================
# DEPENDENCY: Session (wird von main.py überschrieben)
# ============================================================================

def get_session():
    """Placeholder - wird von main.py überschrieben."""
    raise NotImplementedError("Session not configured")


# ============================================================================
# DEPENDENCY: API-Key Validierung
# ============================================================================

def validate_api_key(
    request: Request,
    x_api_key: str = Header(..., description="API-Key der Schule (Format: hgn_sk_...)"),
    session: Session = Depends(get_session)
) -> School:
    """
    Validiert den API-Key und gibt die zugehörige Schule zurück.

    Der Key wird SHA256-gehasht und per INDEXIERTEM Lookup auf `schools.api_key_hash`
    aufgelöst (kein Vergleich gegen alle Schulen).

    SICHERHEIT: Die Schule wird damit AUS DEM KEY abgeleitet und nie vom Client
    übergeben — die Mandantentrennung ist auf diesem Kanal strukturell erzwungen.

    IP-Allowlist: Ist sie an der Schule aktiv, muss die Client-IP zusätzlich in
    einem hinterlegten Bereich liegen; sonst 403. Der Key wird damit zum zweiten
    Faktor. Aus (Default) = keine Einschränkung.
    """
    if not x_api_key or not x_api_key.startswith("hgn_sk_"):
        raise HTTPException(
            status_code=401,
            detail="Ungültiger API-Key. Format: hgn_sk_..."
        )

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()

    school = session.exec(
        select(School).where(School.api_key_hash == key_hash)
    ).first()

    if not school:
        raise HTTPException(
            status_code=401,
            detail="API-Key ungültig oder widerrufen"
        )

    # IP-Allowlist durchsetzen (nur wenn an der Schule aktiv)
    if school.sync_ip_allowlist_enabled:
        client_ip = get_client_ip(request)
        cidrs = [
            e.cidr for e in session.exec(
                select(SyncIpAllowlistEntry).where(SyncIpAllowlistEntry.school_id == school.id)
            ).all()
        ]
        if not ipallow.is_sync_allowed(True, client_ip, cidrs):
            # Blockierter Sync ist forensisch relevant → MIT IP protokollieren.
            audit.record(session, "sync_ip_blocked", school_id=school.id,
                         target_table="schools", ip=client_ip,
                         extra={"source": "sync_api"})
            raise HTTPException(
                status_code=403,
                detail="Zugriff von dieser IP-Adresse ist für diese Schule nicht erlaubt"
            )

    # RLS-Mandant = die aus dem Key abgeleitete Schule. Inert wenn RLS aus.
    from services import rls as _rls
    _rls.set_tenant(session, school.id)

    return school


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class ManifestStudent(BaseModel):
    """Ein Schüler im Manifest (für Diff-Vergleich)"""
    school_internal_id: str
    first_name: str
    last_name: str
    dob: str
    email: Optional[str] = None
    class_name: Optional[str] = None
    is_active: bool
    has_photo: bool
    photo_hash: Optional[str] = None
    data_hash: str  # SHA256 über Stammdaten


class ManifestResponse(BaseModel):
    """Antwort des Manifest-Endpoints"""
    school_id: int
    school_name: str
    generated_at: str
    total_count: int
    students: List[ManifestStudent]


class NewStudentRequest(BaseModel):
    """Ein neuer Schüler zum Anlegen"""
    school_internal_id: str
    first_name: str
    last_name: str
    dob: str  # YYYY-MM-DD
    email: Optional[str] = None
    class_name: str
    photo_base64: Optional[str] = None  # Base64-codiertes Bild


class SyncNewRequest(BaseModel):
    """Bulk-Request: Neue Schüler"""
    students: List[NewStudentRequest]


class ChangeStudentRequest(BaseModel):
    """Änderungsdaten für einen bestehenden Schüler"""
    school_internal_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None  # YYYY-MM-DD
    email: Optional[str] = None
    class_name: Optional[str] = None
    photo_base64: Optional[str] = None


class SyncChangeRequest(BaseModel):
    """Bulk-Request: Schüler aktualisieren"""
    students: List[ChangeStudentRequest]


class SyncSuspendRequest(BaseModel):
    """Bulk-Request: Schüler suspendieren"""
    school_internal_ids: List[str]


class SyncResultItem(BaseModel):
    """Ergebnis für einen einzelnen Schüler"""
    school_internal_id: str
    status: str  # "created", "updated", "suspended", "not_found", "error"
    message: Optional[str] = None


class SyncResponse(BaseModel):
    """Standard-Response für Sync-Operationen"""
    success: bool
    processed: int
    results: List[SyncResultItem]


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def compute_data_hash(
    first_name: str,
    last_name: str,
    dob: str,
    class_name: str,
    email: Optional[str] = None
) -> str:
    """
    Berechnet einen stabilen SHA256-Hash über die Stammdaten eines Schülers.
    Wird vom Windows-Tool zum schnellen Vergleich genutzt.
    """
    parts = [
        first_name.strip().lower(),
        last_name.strip().lower(),
        str(dob),
        class_name.strip().lower() if class_name else "",
        (email or "").strip().lower()
    ]
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_date_str(date_str: str) -> Optional[date]:
    """Parst YYYY-MM-DD oder DD.MM.YYYY zu date."""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def get_or_create_class_id(session, school_id, class_name: str, class_cache: dict) -> str:
    """Holt die Klassen-ID aus dem Cache oder legt die Klasse an.

    Die Klasse "Lehrerkollegium" erhält die Lehrer-Gültigkeit (heute + 5 Jahre, 31.07. —
    analog zum Admin-Dashboard, routes/admin.py), alle anderen die Default-Schuljahres-
    Gültigkeit. So funktioniert die Voll-Sync von Lehrern über denselben Kanal wie Schüler.
    """
    cid = class_cache.get(class_name)
    if cid is not None:
        return cid
    if class_name == TEACHER_CLASS_NAME:
        today = date.today()
        validity = date(today.year + 5, 7, 31)
    else:
        from services.csv_import import CSVImportService
        validity = CSVImportService(session, school_id)._get_default_validity()
    new_class = Class(school_id=school_id, name=class_name, validity_end=validity)
    session.add(new_class)
    session.flush()
    class_cache[class_name] = new_class.id
    return new_class.id


# ============================================================================
# GET /api/sync/manifest
# ============================================================================

@router.get("/manifest", response_model=ManifestResponse)
def get_manifest(
    school: School = Depends(validate_api_key),
    session: Session = Depends(get_session)
) -> ManifestResponse:
    """
    Gibt den aktuellen Datenbestand der Schule zurück.

    Enthält pro Schüler:
    - Stammdaten (Vorname, Nachname, Geb., Klasse, E-Mail)
    - data_hash: SHA256 über Stammdaten (für schnellen Diff)
    - photo_hash: SHA256 des Fotos (oder null)
    - is_active / has_photo Flags

    Das Windows-Tool vergleicht diese Daten mit SchILD
    und schickt nur die Deltas über /new, /change, /suspend.
    """
    # Alle Klassen der Schule laden (inkl. Lehrerkollegium — Lehrer sind Teil der Voll-Sync)
    classes = session.exec(
        select(Class)
        .where(Class.school_id == school.id)
    ).all()
    class_map = {c.id: c.name for c in classes}
    class_ids = list(class_map.keys())

    if not class_ids:
        return ManifestResponse(
            school_id=school.id,
            school_name=school.name,
            generated_at=datetime.utcnow().isoformat() + "Z",
            total_count=0,
            students=[]
        )

    # Alle nicht-gelöschten Schüler laden
    students = session.exec(
        select(Student)
        .where(Student.class_id.in_(class_ids))
        .where(Student.deleted_at.is_(None))
    ).all()

    # Foto-Hashes bulk laden
    student_ids = [s.id for s in students]
    photos = []
    if student_ids:
        photos = session.exec(
            select(Photo).where(Photo.student_id.in_(student_ids))
        ).all()
    photo_map = {p.student_id: p for p in photos}

    # Manifest bauen
    manifest_students = []
    for student in students:
        class_name = class_map.get(student.class_id, "")
        photo = photo_map.get(student.id)

        data_hash = compute_data_hash(
            first_name=student.first_name,
            last_name=student.last_name,
            dob=str(student.dob),
            class_name=class_name,
            email=student.email
        )

        manifest_students.append(ManifestStudent(
            school_internal_id=student.school_internal_id,
            first_name=student.first_name,
            last_name=student.last_name,
            dob=str(student.dob),
            email=student.email,
            class_name=class_name,
            is_active=student.is_active,
            # Selbstheilung: Ist das Foto-Blob mit dem AKTUELLEN SECRETS_KEY nicht
            # entschlüsselbar (tolerant-read liefert den Fernet-Token "gAAAAA…" zurück,
            # z.B. nach Key-Wechsel), melden wir "kein Foto" — das Sync-Tool lädt es
            # dann beim nächsten Lauf neu hoch und der Bestand repariert sich selbst.
            has_photo=(photo is not None and photo.blob_data is not None
                       and not photo.blob_data.startswith("gAAAAA")),
            photo_hash=(photo.hash if photo is not None and photo.blob_data is not None
                        and not photo.blob_data.startswith("gAAAAA") else None),
            data_hash=data_hash
        ))

    # Audit: Der volle Roster-Read über die Sync-API ist der größte PII-Kanal.
    # Er wird protokolliert (Schule + Anzahl), damit ein kompromittierter Key
    # nicht spurlos den ganzen Bestand ziehen kann.
    from services import audit
    audit.record(session, "sync_manifest_read", school_id=school.id,
                 target_table="students", extra={"count": len(manifest_students), "source": "sync_api"})

    return ManifestResponse(
        school_id=school.id,
        school_name=school.name,
        generated_at=datetime.utcnow().isoformat() + "Z",
        total_count=len(manifest_students),
        students=manifest_students
    )


# ============================================================================
# POST /api/sync/new
# ============================================================================

@router.post("/new", response_model=SyncResponse)
def sync_new_students(
    data: SyncNewRequest,
    school: School = Depends(validate_api_key),
    session: Session = Depends(get_session)
) -> SyncResponse:
    """
    Legt neue Schüler an (Bulk).

    Für jeden Schüler wird automatisch generiert:
    - Login-Code, TOTP-Secret, 6-stellige PIN
    - Klasse wird bei Bedarf neu erstellt

    Schüler deren school_internal_id bereits existiert
    werden übersprungen (→ status "already_exists").

    Max. 200 Schüler pro Request.
    """
    if len(data.students) > 200:
        raise HTTPException(
            status_code=400,
            detail="Maximal 200 Schüler pro Request"
        )

    from services.csv_import import generate_login_code, generate_totp_secret, hash_photo
    from services.utils import generate_login_pin

    # Klassen-Cache laden
    classes = session.exec(
        select(Class).where(Class.school_id == school.id)
    ).all()
    class_cache = {c.name: c.id for c in classes}

    # Bestehende IDs laden
    class_ids = list(class_cache.values())
    existing_ids = set()
    if class_ids:
        existing_students = session.exec(
            select(Student.school_internal_id)
            .where(Student.class_id.in_(class_ids))
            .where(Student.deleted_at.is_(None))
        ).all()
        existing_ids = set(existing_students)

    results = []

    for student_data in data.students:
        sid = student_data.school_internal_id

        # Bereits vorhanden?
        if sid in existing_ids:
            results.append(SyncResultItem(
                school_internal_id=sid,
                status="already_exists",
                message="Schüler existiert bereits"
            ))
            continue

        # Datum parsen
        dob = parse_date_str(student_data.dob)
        if not dob:
            results.append(SyncResultItem(
                school_internal_id=sid,
                status="error",
                message=f"Ungültiges Datum: {student_data.dob}"
            ))
            continue

        # Klasse holen/erstellen (Lehrerkollegium bekommt Lehrer-Gültigkeit)
        class_name = student_data.class_name.strip()
        class_id = get_or_create_class_id(session, school.id, class_name, class_cache)

        # Schüler anlegen
        student = Student(
            school_internal_id=sid,
            first_name=student_data.first_name.strip(),
            last_name=student_data.last_name.strip(),
            dob=dob,
            email=student_data.email.strip() if student_data.email else None,
            class_id=class_id,
            generated_login_code=generate_login_code(
                student_data.first_name, student_data.last_name
            ),
            totp_secret=generate_totp_secret(),
            login_pin=generate_login_pin(),
            is_active=True
        )
        session.add(student)
        session.flush()

        # Foto speichern
        if student_data.photo_base64:
            photo = Photo(
                student_id=student.id,
                blob_data=student_data.photo_base64,
                hash=hash_photo(student_data.photo_base64)
            )
            session.add(photo)

        existing_ids.add(sid)
        results.append(SyncResultItem(
            school_internal_id=sid,
            status="created"
        ))

    session.commit()

    # Audit-Log
    created_count = sum(1 for r in results if r.status == "created")
    if created_count > 0:
        audit = AuditLog(
            school_id=school.id,
            action="sync_new",
            target_table="students",
            details=f'{{"created": {created_count}, "source": "sync_api"}}'
        )
        session.add(audit)
        session.commit()

    return SyncResponse(
        success=True,
        processed=len(results),
        results=results
    )


# ============================================================================
# POST /api/sync/change
# ============================================================================

@router.post("/change", response_model=SyncResponse)
def sync_change_students(
    data: SyncChangeRequest,
    school: School = Depends(validate_api_key),
    session: Session = Depends(get_session)
) -> SyncResponse:
    """
    Aktualisiert bestehende Schüler (Bulk).

    Nur gesetzte Felder werden aktualisiert (Partial Update).
    Wenn photo_base64 gesetzt ist, wird das Foto ersetzt.

    Schüler die nicht gefunden werden → status "not_found".

    Max. 200 Schüler pro Request.
    """
    if len(data.students) > 200:
        raise HTTPException(
            status_code=400,
            detail="Maximal 200 Schüler pro Request"
        )

    from services.csv_import import hash_photo

    # Klassen-Cache laden (inkl. Lehrerkollegium — Lehrer werden mitsynchronisiert)
    classes = session.exec(
        select(Class)
        .where(Class.school_id == school.id)
    ).all()
    class_cache = {c.name: c.id for c in classes}
    class_ids = list(class_cache.values())

    # Bestehende Schüler + Lehrer laden
    students_map = {}
    if class_ids:
        existing = session.exec(
            select(Student)
            .where(Student.class_id.in_(class_ids))
            .where(Student.deleted_at.is_(None))
        ).all()
        students_map = {s.school_internal_id: s for s in existing}

    results = []

    for change_data in data.students:
        sid = change_data.school_internal_id
        student = students_map.get(sid)

        if not student:
            results.append(SyncResultItem(
                school_internal_id=sid,
                status="not_found",
                message="Schüler nicht gefunden"
            ))
            continue

        # Felder aktualisieren (nur wenn gesetzt)
        if change_data.first_name is not None:
            student.first_name = change_data.first_name.strip()
        if change_data.last_name is not None:
            student.last_name = change_data.last_name.strip()
        if change_data.email is not None:
            student.email = change_data.email.strip() if change_data.email else None
        if change_data.dob is not None:
            dob = parse_date_str(change_data.dob)
            if not dob:
                results.append(SyncResultItem(
                    school_internal_id=sid,
                    status="error",
                    message=f"Ungültiges Datum: {change_data.dob}"
                ))
                continue
            student.dob = dob

        # Klassenänderung (Lehrerkollegium erlaubt — Voll-Sync)
        if change_data.class_name is not None:
            class_name = change_data.class_name.strip()
            student.class_id = get_or_create_class_id(session, school.id, class_name, class_cache)

        session.add(student)

        # Foto aktualisieren
        if change_data.photo_base64 is not None:
            new_hash = hash_photo(change_data.photo_base64)
            existing_photo = session.exec(
                select(Photo).where(Photo.student_id == student.id)
            ).first()

            if existing_photo:
                existing_photo.blob_data = change_data.photo_base64
                existing_photo.hash = new_hash
                session.add(existing_photo)
            else:
                photo = Photo(
                    student_id=student.id,
                    blob_data=change_data.photo_base64,
                    hash=new_hash
                )
                session.add(photo)

        results.append(SyncResultItem(
            school_internal_id=sid,
            status="updated"
        ))

    session.commit()

    # Audit-Log
    updated_count = sum(1 for r in results if r.status == "updated")
    if updated_count > 0:
        audit = AuditLog(
            school_id=school.id,
            action="sync_change",
            target_table="students",
            details=f'{{"updated": {updated_count}, "source": "sync_api"}}'
        )
        session.add(audit)
        session.commit()

    return SyncResponse(
        success=True,
        processed=len(results),
        results=results
    )


# ============================================================================
# POST /api/sync/suspend
# ============================================================================

@router.post("/suspend", response_model=SyncResponse)
def sync_suspend_students(
    data: SyncSuspendRequest,
    school: School = Depends(validate_api_key),
    session: Session = Depends(get_session)
) -> SyncResponse:
    """
    Deaktiviert Schüler (Bulk) — z.B. bei Abschulung.

    Setzt is_active=False und deleted_at=now().
    Die DSGVO-Garbage-Collection löscht nach 4 Wochen endgültig.

    Max. 500 IDs pro Request.
    """
    if len(data.school_internal_ids) > 500:
        raise HTTPException(
            status_code=400,
            detail="Maximal 500 Schüler pro Request"
        )

    # Klassen der Schule (inkl. Lehrerkollegium — ausgeschiedene Lehrer werden mit deaktiviert)
    class_ids = session.exec(
        select(Class.id)
        .where(Class.school_id == school.id)
    ).all()

    # Schüler + Lehrer laden
    students_map = {}
    if class_ids:
        existing = session.exec(
            select(Student)
            .where(Student.class_id.in_(class_ids))
            .where(Student.deleted_at.is_(None))
        ).all()
        students_map = {s.school_internal_id: s for s in existing}

    results = []

    for sid in data.school_internal_ids:
        student = students_map.get(sid)

        if not student:
            results.append(SyncResultItem(
                school_internal_id=sid,
                status="not_found",
                message="Schüler nicht gefunden"
            ))
            continue

        student.is_active = False
        student.deleted_at = datetime.utcnow()
        session.add(student)

        results.append(SyncResultItem(
            school_internal_id=sid,
            status="suspended"
        ))

    session.commit()

    # Audit-Log
    suspended_count = sum(1 for r in results if r.status == "suspended")
    if suspended_count > 0:
        audit = AuditLog(
            school_id=school.id,
            action="sync_suspend",
            target_table="students",
            details=f'{{"suspended": {suspended_count}, "source": "sync_api"}}'
        )
        session.add(audit)
        session.commit()

    return SyncResponse(
        success=True,
        processed=len(results),
        results=results
    )
