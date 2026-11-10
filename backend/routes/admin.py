"""
Admin-Routen für Hagen-ID

Diese Routen sind nur für authentifizierte Admins/Lehrer zugänglich.
Beinhaltet:
    - CSV-Import (Schild-Sync)
    - Schüler-Verwaltung (manuelle Eingriffe)
    - Dashboard-Daten
"""

from typing import Optional
from datetime import datetime, date

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlmodel import Session, select, func
from pydantic import BaseModel

# Models und Services
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Student, Class, School, Level, Photo, AuditLog, User
from services.csv_import import CSVImportService, ImportPreview, ImportResult

# Auth-Dependency für geschützte Admin-Routen
from routes.auth import get_current_admin

# Autorisierung / Mandantentrennung
from services.authz import (
    assert_school_access,
    assert_student_scope,
    load_student_in_scope,
    load_class_in_scope,
)


# ============================================================================
# ROUTER SETUP
# ============================================================================

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    # SICHERHEIT: Alle Routen erfordern gültigen Admin-Token!
    dependencies=[Depends(get_current_admin)]
)


# ============================================================================
# PYDANTIC SCHEMAS (Request/Response)
# ============================================================================

class DashboardStats(BaseModel):
    """Statistiken fürs Admin-Dashboard"""
    total_students: int
    active_students: int
    total_teachers: int = 0
    active_teachers: int = 0
    total_classes: int
    total_levels: int
    last_import: Optional[datetime] = None


class StudentListItem(BaseModel):
    """Schüler für Listenansicht (ohne sensible Daten)"""
    id: str
    school_internal_id: str
    first_name: str
    last_name: str
    class_name: Optional[str] = None
    is_active: bool
    # Ablaufdatum des Ausweises (von der Klasse)
    validity_end: Optional[date] = None


class StudentDetail(BaseModel):
    """Detailansicht eines Schülers"""
    id: str
    school_internal_id: str
    first_name: str
    last_name: str
    dob: str
    class_name: Optional[str] = None
    class_id: Optional[int] = None
    is_active: bool
    has_photo: bool
    created_login_code: Optional[str] = None
    login_pin: Optional[str] = None  # 6-stellige Login-PIN (für QR-Druck)
    email: Optional[str] = None      # dienstliche E-Mail (Kollegium/Serienmail)


class ImportPreviewResponse(BaseModel):
    """API-Response für Import-Vorschau"""
    total_in_csv: int
    total_in_db: int
    inserts: int
    updates: int
    deletes: int
    unchanged: int
    errors: int
    error_messages: list[str]
    # Nur die ersten 50 Änderungen für Performance
    sample_changes: list[dict]


class ImportResultResponse(BaseModel):
    """API-Response nach Import"""
    success: bool
    message: str
    inserted_count: int
    updated_count: int
    deleted_count: int


# ============================================================================
# DEPENDENCY: Session holen
# ============================================================================

def get_session():
    """
    Dependency für DB-Session.
    Wird in main.py überschrieben mit der echten Engine.
    """
    # Placeholder - wird von main.py überschrieben
    raise NotImplementedError("Session not configured")


# ============================================================================
# DASHBOARD
# ============================================================================

@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard_stats(
    school_id: int = Query(..., description="ID der Schule"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> DashboardStats:
    """
    Holt Statistiken fürs Dashboard.

    Zeigt Übersicht über:
    - Anzahl Schüler (gesamt, aktiv)
    - Anzahl Klassen
    - Letzter Import-Zeitpunkt
    """
    assert_school_access(current_admin, school_id)

    # Alle Klassen der Schule holen
    classes = session.exec(
        select(Class).where(Class.school_id == school_id)
    ).all()

    # Klassen in Schüler-Klassen und Lehrerkollegium aufteilen
    student_class_ids = [c.id for c in classes if c.name != "Lehrerkollegium"]
    teacher_class_ids = [c.id for c in classes if c.name == "Lehrerkollegium"]

    # Schüler zählen (ohne Lehrerkollegium)
    total_students = 0
    active_students = 0

    if student_class_ids:
        total_students = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id.in_(student_class_ids))
            .where(Student.deleted_at.is_(None))
        ).one()

        active_students = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id.in_(student_class_ids))
            .where(Student.deleted_at.is_(None))
            .where(Student.is_active == True)
        ).one()

    # Lehrkräfte zählen
    total_teachers = 0
    active_teachers = 0

    if teacher_class_ids:
        total_teachers = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id.in_(teacher_class_ids))
            .where(Student.deleted_at.is_(None))
        ).one()

        active_teachers = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id.in_(teacher_class_ids))
            .where(Student.deleted_at.is_(None))
            .where(Student.is_active == True)
        ).one()

    # Levels zählen
    total_levels = session.exec(
        select(func.count(Level.id))
        .where(Level.school_id == school_id)
    ).one()

    # Letzter Import aus Audit-Log — auf DIESE Schule beschränkt
    # (sonst würde der Import-Zeitpunkt einer anderen Schule geleakt).
    last_import_log = session.exec(
        select(AuditLog)
        .where(AuditLog.action == "bulk_import")
        .where(AuditLog.school_id == school_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(1)
    ).first()

    # Klassen ohne Lehrerkollegium zählen
    display_classes = len(student_class_ids)

    return DashboardStats(
        total_students=total_students,
        active_students=active_students,
        total_teachers=total_teachers,
        active_teachers=active_teachers,
        total_classes=display_classes,
        total_levels=total_levels,
        last_import=last_import_log.timestamp if last_import_log else None
    )


# ============================================================================
# CSV IMPORT
# ============================================================================

@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_csv_import(
    school_id: int = Query(..., description="ID der Schule"),
    file: UploadFile = File(..., description="CSV-Datei aus Schild"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> ImportPreviewResponse:
    """
    Parst CSV und zeigt Vorschau der Änderungen.

    ACHTUNG: Es werden noch KEINE Änderungen angewendet!
    Der Admin muss die Vorschau prüfen und dann /import/apply aufrufen.

    Workflow:
    1. Upload CSV → POST /admin/import/preview
    2. Admin prüft die Vorschau
    3. Bei OK → POST /admin/import/apply
    """
    assert_school_access(current_admin, school_id)

    # Prüfe Dateityp
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Nur CSV-Dateien erlaubt"
        )
    
    # Content-Type Validierung (verhindert falsche Datei-Uploads)
    if file.content_type and file.content_type not in ['text/csv', 'application/csv', 'text/plain']:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger Content-Type: {file.content_type}. Erwartet text/csv."
        )
    
    # Datei lesen
    content = await file.read()
    
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail="Datei ist leer"
        )
    
    # Max 10MB
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Datei zu groß (max. 10MB)"
        )
    
    # Zeilenlimit prüfen (verhindert DoS durch massive CSV-Dateien)
    # Quick Check: Zähle Newlines ohne komplettes Parsing
    MAX_CSV_ROWS = 10000
    line_count = content.count(b'\n')
    if line_count > MAX_CSV_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Zu viele Zeilen ({line_count}). Maximum: {MAX_CSV_ROWS} Zeilen."
        )
    
    # Import-Service erstellen und Diff berechnen
    service = CSVImportService(session, school_id)
    preview = service.parse_and_diff(content)
    
    # Änderungen für Response aufbereiten (nur Sample)
    sample_changes = []
    for change in preview.changes[:50]:  # Max 50 für Performance
        sample_changes.append({
            "type": change.change_type.value,
            "id": change.school_internal_id,
            "current": change.current_data,
            "new": change.new_data,
            "changed_fields": change.changed_fields
        })
    
    return ImportPreviewResponse(
        total_in_csv=preview.total_in_csv,
        total_in_db=preview.total_in_db,
        inserts=preview.inserts,
        updates=preview.updates,
        deletes=preview.deletes,
        unchanged=preview.unchanged,
        errors=preview.errors,
        error_messages=preview.error_messages[:20],  # Max 20 Fehler zeigen
        sample_changes=sample_changes
    )


# Hinweis: Zwischen Preview und Apply wird nichts gecacht —
# die CSV wird bei /apply erneut hochgeladen und geparst.

@router.post("/import/apply", response_model=ImportResultResponse)
async def apply_csv_import(
    school_id: int = Query(..., description="ID der Schule"),
    file: UploadFile = File(..., description="CSV-Datei aus Schild"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> ImportResultResponse:
    """
    Wendet den CSV-Import an.

    WARNUNG: Diese Aktion kann nicht rückgängig gemacht werden!
    (Außer durch erneuten Import mit korrekten Daten)

    Soft-Deletes werden erst nach 4 Wochen endgültig gelöscht.
    """
    assert_school_access(current_admin, school_id)

    content = await file.read()
    
    # Import durchführen
    service = CSVImportService(session, school_id)
    preview = service.parse_and_diff(content)
    
    # Bei zu vielen Fehlern abbrechen
    if preview.errors > preview.total_in_csv * 0.1:  # Mehr als 10% Fehler
        raise HTTPException(
            status_code=400,
            detail=f"Zu viele Fehler ({preview.errors}). Bitte CSV prüfen."
        )
    
    # Änderungen anwenden
    result = service.apply_changes(preview)
    
    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=result.audit_summary
        )
    
    # Audit-Log schreiben (mit handelndem User und Schul-Kontext)
    import json
    audit = AuditLog(
        user_id=current_admin.id,
        school_id=school_id,
        action="bulk_import",
        target_table="students",
        details=json.dumps({
            "school_id": school_id,
            "inserted": len(result.inserted_ids),
            "updated": len(result.updated_ids),
            "deleted": len(result.deleted_ids),
        })
    )
    session.add(audit)
    session.commit()
    
    return ImportResultResponse(
        success=True,
        message=result.audit_summary,
        inserted_count=len(result.inserted_ids),
        updated_count=len(result.updated_ids),
        deleted_count=len(result.deleted_ids)
    )


# ============================================================================
# SCHÜLER-VERWALTUNG (Manuelle Eingriffe)
# ============================================================================

@router.get("/students", response_model=list[StudentListItem])
def list_students(
    school_id: int = Query(..., description="ID der Schule"),
    class_id: Optional[int] = Query(None, description="Filter nach Klasse"),
    search: Optional[str] = Query(None, description="Suche nach Name"),
    include_inactive: bool = Query(False, description="Auch deaktivierte zeigen"),
    exclude_teachers: bool = Query(True, description="Lehrerkollegium ausschließen"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> list[StudentListItem]:
    """
    Listet Schüler mit optionalen Filtern.

    Für die Suche nach Namen (z.B. bei Deaktivierung wegen Schulverweis).
    Lehrerkollegium wird standardmäßig ausgeschlossen (eigener Bereich).
    """
    assert_school_access(current_admin, school_id)

    # Basis-Query: Alle Klassen der Schule
    class_ids_query = select(Class.id).where(Class.school_id == school_id)
    if class_id:
        class_ids_query = class_ids_query.where(Class.id == class_id)
    if exclude_teachers:
        class_ids_query = class_ids_query.where(Class.name != "Lehrerkollegium")
    class_ids = session.exec(class_ids_query).all()
    
    if not class_ids:
        return []
    
    # Query mit validity_end aus Class (OHNE Namens-Suche/-Sortierung im SQL — die
    # Namen sind at-rest verschlüsselt). Nur die günstigen Filter (Klasse/aktiv) im SQL.
    query = (
        select(Student, Class.name.label("class_name"), Class.validity_end)
        .join(Class, Student.class_id == Class.id)
        .where(Student.class_id.in_(class_ids))
        .where(Student.deleted_at.is_(None))
    )

    if not include_inactive:
        query = query.where(Student.is_active == True)

    rows = session.exec(query).all()  # (student, class_name, validity_end)

    # Suche + Sortierung nach Name laufen IN-MEMORY auf den entschlüsselten Werten
    # (bei ≤2000 SuS/Schule = Millisekunden). Pagination danach ebenfalls in Python.
    if search:
        term = search.strip().lower()
        rows = [
            r for r in rows
            if term in f"{r[0].first_name} {r[0].last_name} {r[0].school_internal_id}".lower()
        ]

    rows.sort(key=lambda r: ((r[0].last_name or "").lower(), (r[0].first_name or "").lower()))
    rows = rows[offset:offset + limit]

    return [
        StudentListItem(
            id=str(student.id),
            school_internal_id=student.school_internal_id,
            first_name=student.first_name,
            last_name=student.last_name,
            class_name=class_name,
            is_active=student.is_active,
            validity_end=validity_end
        )
        for student, class_name, validity_end in rows
    ]


@router.get("/students/{student_id}", response_model=StudentDetail)
def get_student(
    student_id: str,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> StudentDetail:
    """Holt Details zu einem einzelnen Schüler."""
    import uuid

    try:
        uuid_id = uuid.UUID(student_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültige Schüler-ID")

    result = session.exec(
        select(Student, Class.name.label("class_name"))
        .outerjoin(Class, Student.class_id == Class.id)
        .where(Student.id == uuid_id)
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Schüler nicht gefunden")

    student, class_name = result

    # Scope-Prüfung: gehört der Schüler zur Schule des Admins?
    assert_student_scope(student, current_admin, session)

    # Prüfe ob Foto existiert
    has_photo = session.exec(
        select(Photo.id).where(Photo.student_id == student.id)
    ).first() is not None
    
    return StudentDetail(
        id=str(student.id),
        school_internal_id=student.school_internal_id,
        first_name=student.first_name,
        last_name=student.last_name,
        dob=str(student.dob),
        class_name=class_name,
        class_id=student.class_id,
        is_active=student.is_active,
        has_photo=has_photo,
        created_login_code=student.generated_login_code,
        login_pin=student.login_pin,
        email=student.email
    )


@router.patch("/students/{student_id}/deactivate")
def deactivate_student(
    student_id: str,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Deaktiviert einen Schüler (z.B. bei Schulverweis).

    Der Schüler kann sich nicht mehr einloggen, aber die Daten
    bleiben für den nächsten CSV-Import erhalten.
    """
    import json

    student = load_student_in_scope(student_id, current_admin, session)

    student.is_active = False
    session.add(student)

    # Audit-Log (mit handelndem User; details als echtes JSON — kein String-Injection)
    audit = AuditLog(
        user_id=current_admin.id,
        action="manual_deactivate",
        target_table="students",
        details=json.dumps({
            "student_id": str(student.id),
            "name": f"{student.first_name} {student.last_name}",
        })
    )
    session.add(audit)
    session.commit()

    return {"success": True, "message": f"{student.first_name} {student.last_name} deaktiviert"}


@router.patch("/students/{student_id}/activate")
def activate_student(
    student_id: str,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Reaktiviert einen deaktivierten Schüler."""
    student = load_student_in_scope(student_id, current_admin, session)

    student.is_active = True
    session.add(student)
    session.commit()

    return {"success": True, "message": f"{student.first_name} {student.last_name} aktiviert"}


@router.delete("/students/{student_id}")
def delete_student(
    student_id: str,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Löscht einen Schüler (Soft-Delete).

    WICHTIG: Nur möglich wenn der Schüler bereits deaktiviert ist!
    Das verhindert versehentliches Löschen aktiver Ausweise.

    Der Schüler wird nicht wirklich gelöscht, sondern deleted_at wird gesetzt.
    Nach 4 Wochen wird er automatisch endgültig gelöscht (Cleanup-Job).
    """
    import json
    from datetime import datetime

    student = load_student_in_scope(student_id, current_admin, session)

    # Sicherheitscheck: Nur deaktivierte Personen können gelöscht werden
    if student.is_active:
        raise HTTPException(
            status_code=400,
            detail="Kann nicht gelöscht werden: Bitte zuerst deaktivieren."
        )

    # Bereits gelöscht?
    if student.deleted_at:
        raise HTTPException(status_code=400, detail="Bereits zum Löschen vorgemerkt")

    # Soft-Delete: Setze deleted_at
    student.deleted_at = datetime.utcnow()
    session.add(student)

    # Audit-Log (mit handelndem User; details als echtes JSON)
    # Hinweis: AuditLog hat kein student_id Feld - nutzen user_id für Admin-Aktionen
    # und speichern die Student-Referenz in details als JSON
    audit = AuditLog(
        user_id=current_admin.id,
        action="manual_delete",
        target_table="students",
        details=json.dumps({
            "student_id": str(student.id),
            "name": f"{student.first_name} {student.last_name}",
            "info": "Manuell gelöscht (wird in 4 Wochen entfernt)",
        })
    )
    session.add(audit)
    session.commit()
    
    return {
        "success": True, 
        "message": f"{student.first_name} {student.last_name} zum Löschen vorgemerkt (4 Wochen)"
    }


@router.post("/students/{student_id}/photo")
async def upload_photo(
    student_id: str,
    file: UploadFile = File(...),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Manuelles Foto-Upload für einen Schüler.

    Für den Fall, dass das Schild-Foto fehlt oder falsch ist.
    (Service Point Szenario)
    """
    import base64

    student = load_student_in_scope(student_id, current_admin, session)
    uuid_id = student.id

    # Datei validieren
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Nur Bilder erlaubt")

    content = await file.read()

    # Max 10MB (wird dann komprimiert)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Bild zu groß (max. 10MB)")

    # Bild komprimieren (max 800px, JPEG 85%)
    from services.image import compress_image, hash_image, UnsupportedImageError
    try:
        photo_b64 = compress_image(content)
    except UnsupportedImageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    photo_hash = hash_image(photo_b64)
    
    # Existierendes Foto updaten oder neues erstellen
    existing = session.exec(
        select(Photo).where(Photo.student_id == uuid_id)
    ).first()
    
    if existing:
        existing.blob_data = photo_b64
        existing.hash = photo_hash
        session.add(existing)
    else:
        photo = Photo(
            student_id=uuid_id,
            blob_data=photo_b64,
            hash=photo_hash
        )
        session.add(photo)
    
    session.commit()
    
    return {"success": True, "message": "Foto gespeichert"}


@router.get("/print/qr.png")
def get_print_qr(
    data: str = Query(..., max_length=512, description="Zu kodierender Text/URL"),
    current_admin: User = Depends(get_current_admin),
):
    """
    QR-Code als PNG für die Druckseite (Login-QR-Karten).

    Die QR-Erzeugung läuft bewusst serverseitig statt über einen externen
    Dienst: Im Schulkontext sollen keine Daten an Drittanbieter fließen, und
    die CSP der Frontends (`img-src 'self' data: blob:`) lässt fremde
    Bildquellen ohnehin nicht zu. Das Frontend holt das PNG per fetch
    (connect-src erlaubt die API) und zeigt es als blob:-URL.
    Reine Text→Bild-Kodierung, keine DB-Zugriffe; Auth wie alle Admin-Routen.
    """
    from fastapi.responses import Response
    from services.schulbescheinigung import make_qr_png

    buf = make_qr_png(data)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/students/{student_id}/photo")
def get_student_photo(
    student_id: str,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Holt das Foto eines Schülers für die Admin-Ansicht.

    Gibt das Foto als Base64-String zurück, damit das Frontend es
    direkt als Data-URL verwenden kann.
    """
    import base64
    from fastapi.responses import Response

    student = load_student_in_scope(student_id, current_admin, session)
    uuid_id = student.id

    # Foto laden
    photo = session.exec(
        select(Photo).where(Photo.student_id == uuid_id)
    ).first()
    
    if not photo or not photo.blob_data:
        raise HTTPException(status_code=404, detail="Foto nicht gefunden")

    blob = photo.blob_data
    # Diagnose: bleibt nach dem ORM-Read ein Fernet-Token übrig, konnte das Blob mit dem
    # AKTUELLEN SECRETS_KEY nicht entschlüsselt werden (tolerant-read gibt den Token zurück)
    # — z.B. nach Key-Wechsel. Laut loggen statt anonymem 500.
    if blob.startswith("gAAAAA"):
        print(f"❌ Foto {photo.id}: blob_data mit aktuellem SECRETS_KEY nicht entschlüsselbar (Key-Wechsel?)")
        raise HTTPException(status_code=500, detail="Foto mit anderem Schlüssel verschlüsselt")
    # Alt-/Fremdformate: Data-URI-Präfix ("data:image/...;base64,XXXX") abstreifen
    if blob.startswith("data:") and "," in blob:
        blob = blob.split(",", 1)[1]

    # Base64 decodieren und als Bild zurückgeben
    try:
        image_data = base64.b64decode(blob)
    except Exception as e:
        print(f"❌ Foto {photo.id}: base64-decode fehlgeschlagen: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Foto-Daten beschädigt")
    
    # Content-Type erkennen
    content_type = "image/jpeg"  # Default
    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        content_type = "image/png"
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        content_type = "image/webp"
    
    return Response(
        content=image_data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600"  # 1h cachen
        }
    )


# ============================================================================
# MANUELLES ERSTELLEN
# ============================================================================

class StudentCreate(BaseModel):
    first_name: str
    last_name: str
    dob: date
    # Optional: Klasse (sonst "Ohne Klasse")
    class_name: Optional[str] = None
    class_id: Optional[int] = None
    
    # Optional: Manuelle ID (sonst automatisch generiert)
    school_internal_id: Optional[str] = None

    # Optional: (dienstliche) E-Mail — z.B. für Serienmail-Export beim Kollegium.
    email: Optional[str] = None

    # Ist es ein Lehrer?
    is_teacher: bool = False

@router.post("/students", response_model=StudentDetail)
def create_student(
    data: StudentCreate,
    school_id: int = Query(..., description="ID der Schule"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Erstellt manuell einen neuen Schüler oder Lehrer.

    Besonderheit bei Lehrern:
    - Gültigkeit wird automatisch auf den 31.07. nach dem 67. Geburtstag gesetzt.
    - Klasse wird auf "Lehrerkollegium" gesetzt (falls nicht anders angegeben).
    """
    from services.csv_import import generate_login_code, generate_totp_secret
    from services.utils import generate_login_pin

    assert_school_access(current_admin, school_id)

    # 1. Klasse finden oder erstellen
    class_obj = None
    
    # Lehrer-Logik Override
    target_class_name = data.class_name
    if data.is_teacher and not target_class_name:
        target_class_name = "Lehrerkollegium"
        
    if data.class_id:
        # Scope-Prüfung: Klasse muss zur Schule des Admins gehören (Cross-Tenant-Write verhindern)
        class_obj = load_class_in_scope(data.class_id, current_admin, session)
        # Zusätzlich der Superadmin-Fall: beliebige school_id + beliebige class_id müssen zusammenpassen
        if class_obj.school_id != school_id:
            raise HTTPException(status_code=400, detail="Klasse gehört nicht zu dieser Schule")
    elif target_class_name:
        # Suche nach Name
        class_obj = session.exec(
            select(Class)
            .where(Class.school_id == school_id)
            .where(Class.name == target_class_name)
        ).first()
        
        # Erstellen wenn nicht da
        if not class_obj:
            # Standard-Gültigkeit über die Berechnungslogik des Import-Service holen
            service = CSVImportService(session, school_id)
            default_validity = service._get_default_validity()
            
            class_obj = Class(
                school_id=school_id,
                name=target_class_name,
                validity_end=default_validity
            )
            session.add(class_obj)
            session.commit()
            session.refresh(class_obj)
    
    # 2. Gültigkeit berechnen (Override für Lehrer)
    validity_override = None
    if data.is_teacher:
        # Lehrkräfte: Gültigkeit bis zum Schuljahresende (31.07.) in 5 Jahren
        today = date.today()
        validity_override = date(today.year + 5, 7, 31)
            
    # 3. Interne ID bestimmen
    internal_id = data.school_internal_id
    if not internal_id:
        # Fallback: Automatisch generieren
        import secrets
        internal_id = f"MAN-{secrets.token_hex(4).upper()}"
    
    # 3b. Jeder Schüler MUSS eine Klasse haben: Ein Schüler ohne Klasse wäre
    # keiner Schule zuordenbar und damit für Nicht-Superadmins unsichtbar.
    if class_obj is None:
        raise HTTPException(status_code=400, detail="Eine Klasse ist erforderlich")

    # 4. Student anlegen
    student = Student(
        school_internal_id=internal_id,
        first_name=data.first_name,
        last_name=data.last_name,
        dob=data.dob,
        email=data.email.strip() if data.email else None,
        class_id=class_obj.id if class_obj else None,
        generated_login_code=generate_login_code(data.first_name, data.last_name),
        totp_secret=generate_totp_secret(),
        login_pin=generate_login_pin(),  # 6-stellige PIN (ohne führende Null)
        is_active=True,
        validity_end_override=validity_override
    )
    
    session.add(student)
    session.commit()
    session.refresh(student)
    
    return StudentDetail(
        id=str(student.id),
        school_internal_id=student.school_internal_id,
        first_name=student.first_name,
        last_name=student.last_name,
        dob=str(student.dob),
        class_name=class_obj.name if class_obj else None,
        class_id=student.class_id,
        is_active=student.is_active,
        has_photo=False,
        created_login_code=student.generated_login_code,
        login_pin=student.login_pin,
        email=student.email
    )


# ============================================================================
# PIN-VERWALTUNG
# ============================================================================

class PinUpdateRequest(BaseModel):
    """Request zum manuellen Setzen einer PIN"""
    pin: Optional[str] = None  # Wenn None → neue PIN wird generiert


@router.patch("/students/{student_id}/pin")
def update_student_pin(
    student_id: str,
    data: PinUpdateRequest = None,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    Ändert die Login-PIN eines Schülers.

    - Ohne Body / pin=null → Neue zufällige PIN wird generiert
    - Mit pin="123456" → PIN wird manuell gesetzt (muss 6-stellig sein, keine führende 0)
    """
    from services.utils import generate_login_pin

    student = load_student_in_scope(student_id, current_admin, session)

    if data and data.pin:
        # Manuelle PIN: Validierung
        pin = data.pin.strip()
        if not pin.isdigit() or len(pin) != 6:
            raise HTTPException(status_code=400, detail="PIN muss genau 6 Ziffern haben")
        if pin.startswith('0'):
            raise HTTPException(status_code=400, detail="PIN darf nicht mit 0 beginnen")
        student.login_pin = pin
    else:
        # Zufällige neue PIN generieren
        student.login_pin = generate_login_pin()
    
    session.add(student)
    session.commit()
    session.refresh(student)
    
    return {
        "success": True,
        "login_pin": student.login_pin,
        "message": f"PIN für {student.first_name} {student.last_name} aktualisiert"
    }


# ============================================================================
# KLASSEN & DRUCKDATEN
# ============================================================================

class ClassListItem(BaseModel):
    """Klasse für Dropdown/Listenansicht"""
    id: int
    name: str
    student_count: int = 0  # Anzahl aktiver Schüler in der Klasse


class PrintStudentItem(BaseModel):
    """Schüler-Daten für den QR-Code-Druck"""
    id: str
    first_name: str
    last_name: str
    login_pin: Optional[str] = None
    class_name: Optional[str] = None


@router.get("/classes", response_model=list[ClassListItem])
def list_classes(
    school_id: int = Query(..., description="ID der Schule"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> list[ClassListItem]:
    """
    Listet alle Klassen einer Schule auf (für Dropdown bei QR-Druck).
    Gibt auch die Anzahl aktiver Schüler pro Klasse zurück.
    """
    assert_school_access(current_admin, school_id)

    # Alle Klassen der Schule mit Schüleranzahl
    classes = session.exec(
        select(Class).where(Class.school_id == school_id).order_by(Class.name)
    ).all()
    
    result = []
    for cls in classes:
        # Aktive Schüler in dieser Klasse zählen
        count = session.exec(
            select(func.count(Student.id))
            .where(Student.class_id == cls.id)
            .where(Student.is_active == True)
            .where(Student.deleted_at.is_(None))
        ).one()
        
        result.append(ClassListItem(
            id=cls.id,
            name=cls.name,
            student_count=count
        ))
    
    return result


@router.get("/classes/{class_id}/print-data", response_model=list[PrintStudentItem])
def get_class_print_data(
    class_id: int,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
) -> list[PrintStudentItem]:
    """
    Gibt alle aktiven Schüler einer Klasse mit Name und PIN zurück.

    Wird von der QR-Code-Druckseite verwendet, um pro Schüler
    eine Karte mit QR-Code (enthält Login-URL + PIN) zu generieren.

    Schüler ohne PIN erhalten automatisch eine neue PIN.
    """
    from services.utils import generate_login_pin

    # Klasse prüfen + Scope erzwingen (dieser Endpunkt gibt Klartext-PINs aus!)
    cls = load_class_in_scope(class_id, current_admin, session)

    # Alle aktiven Schüler der Klasse laden. Sortierung nach Name in-memory:
    # last_name/first_name sind verschlüsselt, also nicht per SQL sortierbar.
    students = session.exec(
        select(Student)
        .where(Student.class_id == class_id)
        .where(Student.is_active == True)
        .where(Student.deleted_at.is_(None))
    ).all()
    students = sorted(students, key=lambda s: ((s.last_name or "").lower(), (s.first_name or "").lower()))

    result = []
    for student in students:
        # Fehlende PINs automatisch nachgenerieren
        if not student.login_pin:
            student.login_pin = generate_login_pin()
            session.add(student)
        
        result.append(PrintStudentItem(
            id=str(student.id),
            first_name=student.first_name,
            last_name=student.last_name,
            login_pin=student.login_pin,
            class_name=cls.name
        ))
    
    # Falls PINs nachgeneriert wurden, committen
    session.commit()

    return result


@router.get("/classes/{class_id}/export.csv")
def export_class_pins_csv(
    class_id: int,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """
    CSV-Export der aktiven Mitglieder einer Klasse: Name, E-Mail, PIN.

    Zweck: Serienmail (z.B. Lehrerkollegium — Word/Outlook-Seriendruck liest die
    CSV direkt als Datenquelle). Format Excel-DE-tauglich: UTF-8 **mit BOM**
    (sonst zerlegt Excel die Umlaute) und **Semikolon** als Trenner.

    Gibt Klartext-PINs aus → Scope-Check wie print-data + Audit-Log-Eintrag.
    Fehlende PINs werden (wie bei print-data) automatisch nachgeneriert.
    """
    import csv
    import io
    import re as _re
    from fastapi.responses import Response
    from services.utils import generate_login_pin
    from services import audit as _audit

    cls = load_class_in_scope(class_id, current_admin, session)

    students = session.exec(
        select(Student)
        .where(Student.class_id == class_id)
        .where(Student.is_active == True)
        .where(Student.deleted_at.is_(None))
    ).all()
    # Namen sind at-rest verschlüsselt → Sortierung in-memory
    students = sorted(students, key=lambda s: ((s.last_name or "").lower(), (s.first_name or "").lower()))

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Nachname", "Vorname", "E-Mail", "PIN", "Klasse"])
    for s in students:
        if not s.login_pin:
            s.login_pin = generate_login_pin()
            session.add(s)
        writer.writerow([s.last_name, s.first_name, s.email or "", s.login_pin, cls.name])
    session.commit()

    # Export von Klartext-PINs ist forensisch relevant → protokollieren
    _audit.record(
        session, "pins_exported", actor_user_id=current_admin.id,
        school_id=cls.school_id, target_table="students",
        extra={"class_id": class_id, "class_name": cls.name, "count": len(students)},
    )

    safe_name = _re.sub(r"[^A-Za-z0-9_-]+", "-", cls.name).strip("-") or "klasse"
    return Response(
        content="\ufeff" + buf.getvalue(),  # BOM: Excel erkennt UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pins-{safe_name}.csv"'},
    )


# ============================================================================
# AUDIT-LOG-VIEWER (nur Superadmin)
# ============================================================================

class AuditLogItem(BaseModel):
    """Eine Audit-Log-Zeile für den Viewer."""
    id: int
    timestamp: datetime
    action: str
    user_id: Optional[int] = None
    school_id: Optional[int] = None
    subject_id: Optional[str] = None
    target_table: Optional[str] = None
    ip_address: Optional[str] = None
    details: Optional[str] = None


class AuditLogPage(BaseModel):
    """Seite von Audit-Logs + Gesamtzahl (für Pagination)."""
    total: int
    items: list[AuditLogItem]


@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    action: Optional[str] = Query(None, description="Filter: Aktions-Slug, z.B. 'id_verified'"),
    school_id: Optional[int] = Query(None, description="Filter: Schule"),
    subject_id: Optional[str] = Query(None, description="Filter: betroffene ID (Schüler-UUID / E-Mail)"),
    since: Optional[datetime] = Query(None, description="Nur Einträge ab diesem Zeitpunkt"),
    until: Optional[datetime] = Query(None, description="Nur Einträge bis zu diesem Zeitpunkt"),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> AuditLogPage:
    """
    Listet Audit-Logs für den Superadmin-Viewer (neueste zuerst), mit Filtern.

    NUR Superadmin: Das Log enthält schulübergreifende, personenbezogene
    Zugriffsdaten (Verifizierungen, Downloads, fehlgeschlagene Logins).
    """
    _require_superadmin(current_admin)

    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if school_id is not None:
        conditions.append(AuditLog.school_id == school_id)
    if subject_id:
        conditions.append(AuditLog.subject_id == subject_id)
    if since is not None:
        conditions.append(AuditLog.timestamp >= since)
    if until is not None:
        conditions.append(AuditLog.timestamp <= until)

    count_q = select(func.count(AuditLog.id))
    rows_q = select(AuditLog).order_by(AuditLog.timestamp.desc())
    for c in conditions:
        count_q = count_q.where(c)
        rows_q = rows_q.where(c)

    total = session.exec(count_q).one()
    rows = session.exec(rows_q.offset(offset).limit(limit)).all()

    return AuditLogPage(
        total=total,
        items=[
            AuditLogItem(
                id=r.id, timestamp=r.timestamp, action=r.action,
                user_id=r.user_id, school_id=r.school_id, subject_id=r.subject_id,
                target_table=r.target_table, ip_address=r.ip_address, details=r.details,
            )
            for r in rows
        ],
    )


# ============================================================================
# ACCESS-TRACE (nur Superadmin) — zeitlich begrenzte Detail-Nachverfolgung
# ============================================================================
#
# Datenschutz-by-Design: Im Normalbetrieb protokollieren wir sparsam. Für einen
# konkreten Verdachtsfall aktiviert ein Superadmin pro Schule (optional auf eine
# ID eingegrenzt) einen Trace mit Auto-Aus (Standard 48h). Solange er läuft,
# werden zusätzlich Ausweis-Loads und IP-Adressen mitgeschrieben.

from datetime import timedelta
from models import AccessTrace

TRACE_DEFAULT_HOURS = 48
TRACE_MAX_HOURS = 24 * 7   # Obergrenze: 7 Tage


def _require_superadmin(current_admin: User) -> None:
    """Wirft 403, wenn der Aufrufer kein Superadmin ist."""
    role = current_admin.role.value if hasattr(current_admin.role, 'value') else current_admin.role
    if str(role).lower() != 'superadmin':
        raise HTTPException(status_code=403, detail="Nur Superadmins haben Zugriff")


class TraceCreate(BaseModel):
    """Request zum Aktivieren eines Traces."""
    school_id: int
    subject_id: Optional[str] = None   # None = ganze Schule
    reason: Optional[str] = None
    hours: int = TRACE_DEFAULT_HOURS


class TraceItem(BaseModel):
    """Ein Trace für die Anzeige (mit aufgelöstem Schulnamen + Live-Status)."""
    id: int
    school_id: int
    school_name: Optional[str] = None
    subject_id: Optional[str] = None
    reason: Optional[str] = None
    enabled_by: Optional[int] = None
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    active: bool


def _trace_to_item(trace: AccessTrace, school_name: Optional[str], now: datetime) -> TraceItem:
    active = trace.revoked_at is None and trace.expires_at > now
    return TraceItem(
        id=trace.id, school_id=trace.school_id, school_name=school_name,
        subject_id=trace.subject_id, reason=trace.reason, enabled_by=trace.enabled_by,
        created_at=trace.created_at, expires_at=trace.expires_at,
        revoked_at=trace.revoked_at, active=active,
    )


@router.post("/traces", response_model=TraceItem)
def create_trace(
    data: TraceCreate,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> TraceItem:
    """
    Aktiviert einen Trace (nur Superadmin). Auto-Aus nach `hours` (Standard 48h,
    Obergrenze 7 Tage). Das Aktivieren wird selbst im Audit-Log vermerkt.
    """
    _require_superadmin(current_admin)

    school = session.get(School, data.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="Schule nicht gefunden")

    # Dauer begrenzen (1h .. 7 Tage)
    hours = max(1, min(TRACE_MAX_HOURS, int(data.hours or TRACE_DEFAULT_HOURS)))
    now = datetime.utcnow()
    subject = (data.subject_id or "").strip() or None

    trace = AccessTrace(
        school_id=data.school_id,
        subject_id=subject,
        enabled_by=current_admin.id,
        reason=(data.reason or "").strip() or None,
        created_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    session.add(trace)
    session.commit()
    session.refresh(trace)

    # Das Aktivieren selbst ist auditpflichtig (Nachvollziehbarkeit).
    from services import audit
    audit.record(
        session, "trace_enabled",
        actor_user_id=current_admin.id, school_id=data.school_id, subject_id=subject,
        target_table="access_traces",
        extra={"trace_id": trace.id, "hours": hours, "reason": trace.reason or ""},
    )
    return _trace_to_item(trace, school.name, now)


@router.get("/traces", response_model=list[TraceItem])
def list_traces(
    active_only: bool = Query(False, description="Nur aktuell laufende Traces"),
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> list[TraceItem]:
    """Listet Traces (neueste zuerst). Nur Superadmin."""
    _require_superadmin(current_admin)
    now = datetime.utcnow()

    q = select(AccessTrace).order_by(AccessTrace.created_at.desc())
    if active_only:
        q = q.where(AccessTrace.revoked_at.is_(None)).where(AccessTrace.expires_at > now)
    rows = session.exec(q.limit(200)).all()

    # Schulnamen in einem Rutsch auflösen
    school_ids = {r.school_id for r in rows}
    names = {}
    if school_ids:
        for s in session.exec(select(School).where(School.id.in_(school_ids))).all():
            names[s.id] = s.name

    return [_trace_to_item(r, names.get(r.school_id), now) for r in rows]


@router.delete("/traces/{trace_id}")
def revoke_trace(
    trace_id: int,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Beendet einen laufenden Trace vorzeitig (nur Superadmin). Auditiert."""
    _require_superadmin(current_admin)

    trace = session.get(AccessTrace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace nicht gefunden")

    if trace.revoked_at is None:
        trace.revoked_at = datetime.utcnow()
        session.add(trace)
        session.commit()

        from services import audit
        audit.record(
            session, "trace_revoked",
            actor_user_id=current_admin.id, school_id=trace.school_id,
            subject_id=trace.subject_id, target_table="access_traces",
            extra={"trace_id": trace.id},
        )
    return {"success": True, "id": trace_id}


# ============================================================================
# SYSTEM-STATUS (nur Superadmin) — Live-Betriebsübersicht für die Statusseite
# ============================================================================

class SystemStatus(BaseModel):
    """Live-Betriebszustand für die Superadmin-Statusseite."""
    generated_at: datetime
    uptime_seconds: int
    debug: bool
    db_ok: bool
    db_latency_ms: float
    counts: dict
    last_cleanup: Optional[dict] = None
    security: dict
    recent_anomalies: list[dict]


class RateLimitSettings(BaseModel):
    """Einstellbare Login-Rate-Limits."""
    attempts: int
    window_minutes: int


class RateLimitSettingsInfo(RateLimitSettings):
    """Aktuelle Limits + erzwungene Grenzen (für die Admin-UI)."""
    attempts_min: int
    attempts_max: int
    window_min: int
    window_max: int
    is_db_override: bool   # True = per UI gesetzt (überschreibt Env), False = Env/Default


def _rate_limit_info(session: Session) -> RateLimitSettingsInfo:
    from services import ratelimit, appsettings
    attempts, window = ratelimit.get_limits(session)
    b = ratelimit.get_bounds()
    is_db = (appsettings.get_value(session, ratelimit.RL_ATTEMPTS_KEY) is not None
             or appsettings.get_value(session, ratelimit.RL_WINDOW_KEY) is not None)
    return RateLimitSettingsInfo(
        attempts=attempts, window_minutes=window,
        attempts_min=b["attempts"]["min"], attempts_max=b["attempts"]["max"],
        window_min=b["window"]["min"], window_max=b["window"]["max"],
        is_db_override=is_db,
    )


@router.get("/settings/rate-limit", response_model=RateLimitSettingsInfo)
def get_rate_limit_settings(
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> RateLimitSettingsInfo:
    """Aktuelle Login-Rate-Limits + erzwungene Grenzen (nur Superadmin)."""
    _require_superadmin(current_admin)
    return _rate_limit_info(session)


@router.put("/settings/rate-limit", response_model=RateLimitSettingsInfo)
def set_rate_limit_settings(
    data: RateLimitSettings,
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> RateLimitSettingsInfo:
    """
    Login-Rate-Limits live setzen (nur Superadmin, global gültig).

    Werte werden auf die im Code erzwungenen Min/Max **geklammert, bevor** sie gespeichert
    werden — die Sicherheitsgrenzen sind per UI nicht aushebelbar.
    """
    _require_superadmin(current_admin)
    from services import ratelimit, appsettings, audit as audit_svc
    b = ratelimit.get_bounds()
    a = max(b["attempts"]["min"], min(b["attempts"]["max"], int(data.attempts)))
    w = max(b["window"]["min"], min(b["window"]["max"], int(data.window_minutes)))
    appsettings.set_value(session, ratelimit.RL_ATTEMPTS_KEY, str(a))
    appsettings.set_value(session, ratelimit.RL_WINDOW_KEY, str(w))
    audit_svc.record(session, "rate_limit_settings_changed",
                     actor_user_id=current_admin.id, result="ok",
                     extra={"attempts": a, "window_minutes": w})
    return _rate_limit_info(session)


@router.get("/system/status", response_model=SystemStatus)
def system_status(
    current_admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
) -> SystemStatus:
    """
    Live-Systemstatus (nur Superadmin): DB-Erreichbarkeit + Latenz, Uptime, Kennzahlen,
    letzter nächtlicher Job-Lauf, Sicherheits-Anomalien der letzten 24h + jüngste Ereignisse.

    Bewusst KEINE E-Mail-Benachrichtigung — die Anomalien werden hier angezeigt.
    Ein echter „Server-tot"-Alarm braucht einen externen Pinger auf /health.
    """
    import time as _time
    import main  # zur Laufzeit → kein Zirkelimport; liefert START_TIME + _DEBUG
    from models import AccessTrace

    _require_superadmin(current_admin)
    now = datetime.utcnow()

    # DB-Check + Latenz (eine triviale Zählabfrage)
    t0 = _time.time()
    schools_count = session.exec(select(func.count(School.id))).one()
    db_latency = round((_time.time() - t0) * 1000, 2)

    students_total = session.exec(
        select(func.count(Student.id)).where(Student.deleted_at.is_(None))
    ).one()
    students_active = session.exec(
        select(func.count(Student.id))
        .where(Student.deleted_at.is_(None)).where(Student.is_active == True)
    ).one()
    admin_users = session.exec(select(func.count(User.id))).one()
    classes_count = session.exec(select(func.count(Class.id))).one()

    # Letzter Job-Lauf (Heartbeat aus dem DSGVO-Cron)
    last = session.exec(
        select(AuditLog).where(AuditLog.action == "system_cleanup_ran")
        .order_by(AuditLog.timestamp.desc()).limit(1)
    ).first()
    last_cleanup = {"at": last.timestamp.isoformat(), "details": last.details} if last else None

    # Sicherheits-Kennzahlen (letzte 24h)
    since = now - timedelta(hours=24)

    def _count_since(action: str) -> int:
        return session.exec(
            select(func.count(AuditLog.id))
            .where(AuditLog.action == action).where(AuditLog.timestamp >= since)
        ).one()

    active_traces = session.exec(
        select(func.count(AccessTrace.id))
        .where(AccessTrace.revoked_at.is_(None)).where(AccessTrace.expires_at > now)
    ).one()
    schools_with_key = session.exec(
        select(func.count(School.id)).where(School.api_key_hash.is_not(None))
    ).one()
    schools_with_allowlist = session.exec(
        select(func.count(School.id)).where(School.sync_ip_allowlist_enabled == True)
    ).one()

    security = {
        "failed_admin_logins_24h": _count_since("admin_login_failed"),
        "failed_student_logins_24h": _count_since("student_login_failed"),
        "sync_ip_blocked_24h": _count_since("sync_ip_blocked"),
        "active_traces": active_traces,
        "schools_with_api_key": schools_with_key,
        "schools_with_allowlist": schools_with_allowlist,
    }

    # Jüngste sicherheitsrelevante Ereignisse (Liste)
    interesting = ["sync_ip_blocked", "admin_login_failed", "student_login_failed",
                   "trace_enabled", "trace_revoked"]
    rows = session.exec(
        select(AuditLog).where(AuditLog.action.in_(interesting))
        .order_by(AuditLog.timestamp.desc()).limit(20)
    ).all()
    recent_anomalies = [
        {"timestamp": r.timestamp.isoformat(), "action": r.action,
         "school_id": r.school_id, "subject_id": r.subject_id, "ip": r.ip_address}
        for r in rows
    ]

    return SystemStatus(
        generated_at=now,
        uptime_seconds=int((now - main.START_TIME).total_seconds()),
        debug=main._DEBUG,
        db_ok=True,
        db_latency_ms=db_latency,
        counts={
            "schools": schools_count, "students_total": students_total,
            "students_active": students_active, "admin_users": admin_users,
            "classes": classes_count,
        },
        last_cleanup=last_cleanup,
        security=security,
        recent_anomalies=recent_anomalies,
    )
