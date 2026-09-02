"""
CSV-Import Service für Hagen-ID (Schild-Sync)

Dieses Modul ist das HERZSTÜCK des Systems.
Es synchronisiert Schülerdaten aus der Schulverwaltung (Schild) mit der Datenbank.

Funktionsweise (Diff-Sync):
    1. Admin lädt CSV-Export aus Schild hoch
    2. System vergleicht anhand der school_internal_id:
       - ID neu? → INSERT (neuer Schüler)
       - ID da, aber Daten anders? → UPDATE (Änderung)
       - ID in DB, aber NICHT im CSV? → Soft-Delete (Abgang)
    3. Vorschau wird generiert (Admin kann prüfen)
    4. Bei Bestätigung werden Änderungen angewendet

CSV-Format (erwartet aus Schild):
    - Trennzeichen: Semikolon (;) - Standard bei deutschen Excel-Exporten
    - Encoding: UTF-8 oder ISO-8859-1 (wird automatisch erkannt)
    - Pflichtfelder: InternID, Vorname, Nachname, Geburtsdatum, Klasse
    - Optional: Foto (Base64 oder Pfad)
"""

import csv
import hashlib
import io
import secrets
import base64
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

import pyotp
from sqlmodel import Session, select

# Models importieren (relativer Import aus parent)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from models import Student, Class, Photo, School


# ============================================================================
# KONFIGURATION: CSV-Spalten-Mapping
# ============================================================================
# Hier wird definiert, welche CSV-Spalten welchen Feldern entsprechen.
# Kann pro Schule angepasst werden, falls Schild unterschiedliche Exporte macht.

DEFAULT_COLUMN_MAPPING = {
    # CSV-Spaltenname → internes Feld
    "InternID": "school_internal_id",      # Schild-interne ID (PFLICHT für Matching!)
    "Schueler_ID": "school_internal_id",   # Alternative Bezeichnung
    "ID": "school_internal_id",            # Fallback
    
    "Vorname": "first_name",
    "Firstname": "first_name",
    
    "Nachname": "last_name",
    "Name": "last_name",
    "Lastname": "last_name",
    
    "Geburtsdatum": "dob",
    "GebDatum": "dob",
    "Birthdate": "dob",
    
    "Klasse": "class_name",
    "Klassenbezeichnung": "class_name",
    "Class": "class_name",
    
    "Foto": "photo_base64",
    "Photo": "photo_base64",
    "Bild": "photo_base64",
}


# ============================================================================
# DATENKLASSEN FÜR IMPORT-ERGEBNISSE
# ============================================================================

class ChangeType(str, Enum):
    """Art der Änderung beim Diff"""
    INSERT = "insert"      # Neuer Schüler
    UPDATE = "update"      # Daten geändert
    DELETE = "delete"      # Im CSV nicht mehr vorhanden → Soft-Delete
    UNCHANGED = "unchanged" # Keine Änderung
    ERROR = "error"        # Fehler beim Parsen


@dataclass
class StudentChange:
    """
    Repräsentiert eine einzelne Änderung beim Import.
    Wird für die Vorschau und das Audit-Log verwendet.
    """
    change_type: ChangeType
    school_internal_id: str
    
    # Aktuelle Daten (aus DB, None bei INSERT)
    current_data: Optional[Dict[str, Any]] = None
    
    # Neue Daten (aus CSV, None bei DELETE)
    new_data: Optional[Dict[str, Any]] = None
    
    # Bei UPDATE: Welche Felder haben sich geändert?
    changed_fields: List[str] = field(default_factory=list)
    
    # Fehlermeldung bei ERROR
    error_message: Optional[str] = None


@dataclass
class ImportPreview:
    """
    Zusammenfassung des Imports VOR der Anwendung.
    Wird dem Admin zur Prüfung angezeigt.
    """
    total_in_csv: int = 0
    total_in_db: int = 0
    
    inserts: int = 0
    updates: int = 0
    deletes: int = 0
    unchanged: int = 0
    errors: int = 0
    
    changes: List[StudentChange] = field(default_factory=list)
    
    # Für das Frontend: Fehlermeldungen
    error_messages: List[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """
    Ergebnis nach Anwendung des Imports.
    """
    success: bool
    preview: ImportPreview
    
    # IDs der betroffenen Datensätze
    inserted_ids: List[str] = field(default_factory=list)
    updated_ids: List[str] = field(default_factory=list)
    deleted_ids: List[str] = field(default_factory=list)
    
    # Audit-Zusammenfassung
    audit_summary: str = ""


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def detect_encoding(file_bytes: bytes) -> str:
    """
    Erkennt das Encoding einer CSV-Datei.
    Deutsche Schild-Exporte sind oft ISO-8859-1 (Latin-1).
    """
    # Einfache Heuristik: Versuche UTF-8, dann Latin-1
    try:
        file_bytes.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        return 'iso-8859-1'


def detect_delimiter(sample: str) -> str:
    """
    Erkennt das Trennzeichen (Semikolon ist Standard bei deutschen Excel-Exporten).
    """
    # Zähle Vorkommen in der ersten Zeile
    first_line = sample.split('\n')[0]
    semicolons = first_line.count(';')
    commas = first_line.count(',')
    tabs = first_line.count('\t')
    
    if semicolons >= commas and semicolons >= tabs:
        return ';'
    elif tabs >= commas:
        return '\t'
    else:
        return ','


def parse_date(date_str: str) -> Optional[date]:
    """
    Parst verschiedene Datumsformate die in Schild vorkommen können.
    
    Unterstützte Formate:
    - DD.MM.YYYY (deutsch, am häufigsten)
    - YYYY-MM-DD (ISO)
    - DD/MM/YYYY (selten)
    """
    if not date_str or not date_str.strip():
        return None
    
    date_str = date_str.strip()
    
    formats = [
        "%d.%m.%Y",   # 15.03.2005 (deutsch, Standard)
        "%Y-%m-%d",   # 2005-03-15 (ISO)
        "%d/%m/%Y",   # 15/03/2005
        "%d.%m.%y",   # 15.03.05 (kurzes Jahr)
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    return None


def generate_login_code(first_name: str, last_name: str) -> str:
    """
    Generiert den Login-Code für einen Schüler.
    
    Format: Erster Buchstabe Vorname + Nachname + 4 zufällige Zeichen
    Beispiel: "Max Mustermann" → "MMustermann7x3k"
    
    Die zufälligen Zeichen machen den Code eindeutig, auch bei
    Namensvettern wie "Max Müller" und "Maria Müller".
    """
    # Bereinige Namen (Umlaute, Sonderzeichen)
    first_initial = first_name[0].upper() if first_name else "X"
    
    # Nachname: Entferne Leerzeichen, behalte Bindestriche
    clean_last = "".join(c for c in last_name if c.isalnum() or c == '-')
    
    # 4 zufällige alphanumerische Zeichen
    random_suffix = secrets.token_hex(2)  # 4 Hex-Zeichen
    
    return f"{first_initial}{clean_last}{random_suffix}"


def generate_totp_secret() -> str:
    """
    Generiert ein TOTP-Secret für den Offline-QR-Code.
    
    Das Secret ist Base32-codiert (Standard für TOTP).
    Mit diesem Secret kann die PWA alle 30 Sekunden einen
    neuen Code generieren, der vom Backend verifiziert werden kann.
    """
    return pyotp.random_base32()


def hash_photo(photo_data: str) -> str:
    """
    Berechnet SHA256-Hash eines Fotos für Änderungserkennung.
    So müssen beim Import nur Fotos aktualisiert werden, die sich wirklich geändert haben.
    """
    return hashlib.sha256(photo_data.encode()).hexdigest()


def normalize_csv_headers(headers: List[str], mapping: Dict[str, str]) -> Dict[str, int]:
    """
    Mappt CSV-Header auf interne Feldnamen und gibt deren Positionen zurück.
    
    Returns:
        Dict mit internem Feldnamen → Spaltenindex
        z.B. {"school_internal_id": 0, "first_name": 2, ...}
    """
    field_positions = {}
    
    for idx, header in enumerate(headers):
        # Header bereinigen (Leerzeichen, BOM entfernen)
        clean_header = header.strip().replace('\ufeff', '')
        
        # Suche im Mapping
        if clean_header in mapping:
            internal_name = mapping[clean_header]
            # Nur wenn noch nicht gemappt (erste Übereinstimmung gewinnt)
            if internal_name not in field_positions:
                field_positions[internal_name] = idx
    
    return field_positions


# ============================================================================
# HAUPT-IMPORT-KLASSE
# ============================================================================

class CSVImportService:
    """
    Service für den CSV-Import aus Schild.
    
    Verwendung:
        service = CSVImportService(session, school_id=1)
        preview = service.parse_and_diff(csv_bytes)
        
        if preview.errors == 0:
            result = service.apply_changes(preview)
    """
    
    def __init__(
        self,
        session: Session,
        school_id: int,
        column_mapping: Optional[Dict[str, str]] = None
    ):
        """
        Args:
            session: SQLModel Session für DB-Zugriff
            school_id: ID der Schule für die importiert wird
            column_mapping: Optionales Custom-Mapping für CSV-Spalten
        """
        self.session = session
        self.school_id = school_id
        self.mapping = column_mapping or DEFAULT_COLUMN_MAPPING
        
        # Caches für Performance
        self._class_cache: Dict[str, int] = {}  # Klassenname → ID
        self._existing_students: Dict[str, Student] = {}  # school_internal_id → Student
    
    def _load_class_cache(self) -> None:
        """
        Lädt alle Klassen der Schule in einen Cache.
        So müssen wir nicht für jeden Schüler eine DB-Abfrage machen.
        """
        statement = select(Class).where(Class.school_id == self.school_id)
        classes = self.session.exec(statement).all()
        self._class_cache = {c.name: c.id for c in classes}
    
    def _load_existing_students(self) -> None:
        """
        Lädt alle aktiven Schüler der Schule in einen Cache.
        Nur Schüler mit deleted_at=NULL werden berücksichtigt.
        """
        # Hole alle Klassen dieser Schule
        class_ids = list(self._class_cache.values())
        
        if not class_ids:
            self._existing_students = {}
            return
        
        statement = (
            select(Student)
            .where(Student.class_id.in_(class_ids))
            .where(Student.deleted_at.is_(None))
        )
        students = self.session.exec(statement).all()
        self._existing_students = {s.school_internal_id: s for s in students}
    
    def _get_or_create_class(self, class_name: str) -> Optional[int]:
        """
        Holt die Klassen-ID aus dem Cache oder erstellt eine neue Klasse.
        
        Returns:
            Klassen-ID oder None bei Fehler
        """
        if class_name in self._class_cache:
            return self._class_cache[class_name]
        
        # Neue Klasse erstellen
        new_class = Class(
            school_id=self.school_id,
            name=class_name,
            # Standard-Gültigkeit: Ende des aktuellen Schuljahres
            validity_end=self._get_default_validity()
        )
        self.session.add(new_class)
        self.session.flush()  # ID generieren lassen
        
        self._class_cache[class_name] = new_class.id
        return new_class.id
    
    def _get_default_validity(self) -> date:
        """
        Berechnet das Standard-Gültigkeitsdatum basierend auf der Schul-Konfiguration.
        
        Strategien:
        - custom_date: Das fest eingestellte Datum der Schule
        - half_year: 
            - 1. Halbjahr (ca. Aug - Jan): Ende = 1. Freitag nach dem 30.1. (Halbjahreszeugnis)
            - 2. Halbjahr (ca. Feb - Jul): Ende = 31.07. (Schuljahresende)
        """
        school = self.session.get(School, self.school_id)
        if not school:
            # Fallback
            return date(date.today().year + 1, 7, 31)
            
        # 1. Custom Date
        if school.validity_strategy == "custom_date" and school.validity_custom_date:
            return school.validity_custom_date
            
        # 2. Halbjahres-Modus (Standard; greift auch, wenn nichts Sinnvolles
        #    eingestellt ist).
        #
        # Kandidaten sind die Halbjahresenden dieses und des nächsten Jahres:
        # - Ende 1. Halbjahr: erster Freitag nach dem 30.01. (Halbjahreszeugnis)
        # - Ende 2. Halbjahr: 31.07. (Schuljahresende NRW)
        # Gewählt wird das nächste Datum, das noch ausreichend in der Zukunft liegt.

        today = date.today()
        current_year = today.year

        def first_friday_after(d: date) -> date:
            """Erster Freitag NACH dem Datum d (nie d selbst)."""
            weekday = d.weekday()  # Mo=0, So=6
            days_until_friday = (4 - weekday + 7) % 7
            if days_until_friday == 0:
                days_until_friday = 7
            from datetime import timedelta
            return d + timedelta(days=days_until_friday)

        # (A) Ende 1. Halbjahr (Winter)
        jan_30_this = date(current_year, 1, 30)
        end_winter_this = first_friday_after(jan_30_this)
        
        jan_30_next = date(current_year + 1, 1, 30)
        end_winter_next = first_friday_after(jan_30_next)
        
        # (B) Ende 2. Halbjahr (Sommer)
        end_summer_this = date(current_year, 7, 31)
        end_summer_next = date(current_year + 1, 7, 31)
        
        candidates = [end_winter_this, end_winter_next, end_summer_this, end_summer_next]
        candidates.sort()

        # Nächstes Datum, das mindestens 14 Tage in der Zukunft liegt — sonst
        # würde kurz vor Halbjahresende ein Ausweis für wenige Tage ausgestellt.
        from datetime import timedelta
        min_date = today + timedelta(days=14)

        for cand in candidates:
            if cand > min_date:
                return cand

        # Fallback (rechnerisch nicht erreichbar)
        return end_summer_next
    
    def parse_csv(self, file_bytes: bytes) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Parst die CSV-Datei und gibt eine Liste von Schüler-Dicts zurück.
        
        Returns:
            Tuple von (Liste der geparsten Schüler, Liste der Fehler)
        """
        errors = []
        students = []
        
        # Encoding erkennen
        encoding = detect_encoding(file_bytes)
        content = file_bytes.decode(encoding)
        
        # Trennzeichen erkennen
        delimiter = detect_delimiter(content)
        
        # CSV parsen
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        
        try:
            headers = next(reader)
        except StopIteration:
            errors.append("CSV-Datei ist leer")
            return [], errors
        
        # Header mappen
        field_positions = normalize_csv_headers(headers, self.mapping)
        
        # Prüfe Pflichtfelder
        required = ["school_internal_id", "first_name", "last_name", "dob", "class_name"]
        missing = [f for f in required if f not in field_positions]
        if missing:
            errors.append(f"Fehlende Pflichtfelder: {', '.join(missing)}")
            errors.append(f"Gefundene Spalten: {', '.join(headers)}")
            return [], errors
        
        # Zeilen parsen
        for line_num, row in enumerate(reader, start=2):  # Start bei 2 (Header = 1)
            if not any(row):  # Leere Zeile überspringen
                continue
            
            try:
                student_data = {}
                
                for field_name, col_idx in field_positions.items():
                    if col_idx < len(row):
                        student_data[field_name] = row[col_idx].strip()
                    else:
                        student_data[field_name] = ""
                
                # Validierung
                if not student_data.get("school_internal_id"):
                    errors.append(f"Zeile {line_num}: Keine ID vorhanden")
                    continue
                
                # Datum parsen
                dob = parse_date(student_data.get("dob", ""))
                if not dob:
                    errors.append(f"Zeile {line_num}: Ungültiges Geburtsdatum '{student_data.get('dob')}'")
                    continue
                student_data["dob"] = dob
                
                students.append(student_data)
                
            except Exception as e:
                errors.append(f"Zeile {line_num}: Parsing-Fehler - {str(e)}")
        
        return students, errors
    
    def parse_and_diff(self, file_bytes: bytes) -> ImportPreview:
        """
        Parst CSV und berechnet den Diff zur bestehenden Datenbank.
        
        Dies ist der erste Schritt: Es werden KEINE Änderungen angewendet.
        Der Admin sieht eine Vorschau und muss bestätigen.
        
        Returns:
            ImportPreview mit allen geplanten Änderungen
        """
        preview = ImportPreview()
        
        # Caches laden
        self._load_class_cache()
        self._load_existing_students()
        
        preview.total_in_db = len(self._existing_students)
        
        # CSV parsen
        csv_students, parse_errors = self.parse_csv(file_bytes)
        preview.error_messages.extend(parse_errors)
        preview.errors = len(parse_errors)
        preview.total_in_csv = len(csv_students)
        
        # Set für gefundene IDs (um Deletes zu erkennen)
        found_ids = set()
        
        # Jeden Schüler aus CSV prüfen
        for csv_data in csv_students:
            internal_id = csv_data["school_internal_id"]
            found_ids.add(internal_id)
            
            existing = self._existing_students.get(internal_id)
            
            if existing is None:
                # NEUER SCHÜLER
                change = StudentChange(
                    change_type=ChangeType.INSERT,
                    school_internal_id=internal_id,
                    new_data=csv_data
                )
                preview.changes.append(change)
                preview.inserts += 1
            else:
                # BESTEHENDER SCHÜLER - prüfe auf Änderungen
                changed_fields = []
                
                # Vergleiche relevante Felder
                if existing.first_name != csv_data.get("first_name", ""):
                    changed_fields.append("first_name")
                if existing.last_name != csv_data.get("last_name", ""):
                    changed_fields.append("last_name")
                if existing.dob != csv_data.get("dob"):
                    changed_fields.append("dob")
                
                # Klassenänderung prüfen
                new_class_name = csv_data.get("class_name", "")
                current_class_name = None
                for name, cid in self._class_cache.items():
                    if cid == existing.class_id:
                        current_class_name = name
                        break
                
                if current_class_name != new_class_name:
                    changed_fields.append("class_name")
                
                # Foto-Änderung prüfen (falls im CSV vorhanden).
                # Es findet kein Hash-Vergleich gegen die DB statt — ein Foto im
                # CSV wird immer als Änderung markiert.
                if csv_data.get("photo_base64"):
                    changed_fields.append("photo")
                
                if changed_fields:
                    change = StudentChange(
                        change_type=ChangeType.UPDATE,
                        school_internal_id=internal_id,
                        current_data={
                            "first_name": existing.first_name,
                            "last_name": existing.last_name,
                            "dob": str(existing.dob),
                            "class_name": current_class_name
                        },
                        new_data=csv_data,
                        changed_fields=changed_fields
                    )
                    preview.changes.append(change)
                    preview.updates += 1
                else:
                    preview.unchanged += 1
        
        # GELÖSCHTE SCHÜLER: In DB aber nicht im CSV
        for internal_id, student in self._existing_students.items():
            if internal_id not in found_ids:
                change = StudentChange(
                    change_type=ChangeType.DELETE,
                    school_internal_id=internal_id,
                    current_data={
                        "first_name": student.first_name,
                        "last_name": student.last_name,
                        "class_id": student.class_id
                    }
                )
                preview.changes.append(change)
                preview.deletes += 1
        
        return preview
    
    def apply_changes(self, preview: ImportPreview) -> ImportResult:
        """
        Wendet die Änderungen aus der Vorschau auf die Datenbank an.
        
        WICHTIG: Erst nach Admin-Bestätigung aufrufen!
        
        Returns:
            ImportResult mit Details zu den durchgeführten Änderungen
        """
        result = ImportResult(success=True, preview=preview)
        
        try:
            for change in preview.changes:
                if change.change_type == ChangeType.INSERT:
                    self._apply_insert(change, result)
                elif change.change_type == ChangeType.UPDATE:
                    self._apply_update(change, result)
                elif change.change_type == ChangeType.DELETE:
                    self._apply_delete(change, result)
            
            self.session.commit()
            
            result.audit_summary = (
                f"Import abgeschlossen: "
                f"{len(result.inserted_ids)} neu, "
                f"{len(result.updated_ids)} aktualisiert, "
                f"{len(result.deleted_ids)} gelöscht"
            )
            
        except Exception as e:
            self.session.rollback()
            result.success = False
            result.audit_summary = f"Import fehlgeschlagen: {str(e)}"
        
        return result
    
    def _apply_insert(self, change: StudentChange, result: ImportResult) -> None:
        """Fügt einen neuen Schüler ein."""
        data = change.new_data
        
        # Klasse holen/erstellen
        class_id = self._get_or_create_class(data.get("class_name", "Unbekannt"))
        
        # login_pin generieren (6-stellig, ohne führende Null)
        from services.utils import generate_login_pin
        
        # Neuen Schüler anlegen
        student = Student(
            school_internal_id=change.school_internal_id,
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            dob=data.get("dob"),
            class_id=class_id,
            generated_login_code=generate_login_code(
                data.get("first_name", ""),
                data.get("last_name", "")
            ),
            totp_secret=generate_totp_secret(),
            login_pin=generate_login_pin(),  # 6-stellige PIN für gedruckten QR-Code
            is_active=True
        )
        
        self.session.add(student)
        self.session.flush()  # ID generieren
        
        # Foto hinzufügen falls vorhanden
        if data.get("photo_base64"):
            photo = Photo(
                student_id=student.id,
                blob_data=data["photo_base64"],
                hash=hash_photo(data["photo_base64"])
            )
            self.session.add(photo)
        
        result.inserted_ids.append(change.school_internal_id)
    
    def _apply_update(self, change: StudentChange, result: ImportResult) -> None:
        """Aktualisiert einen bestehenden Schüler."""
        student = self._existing_students.get(change.school_internal_id)
        if not student:
            return
        
        data = change.new_data
        
        # Felder aktualisieren
        if "first_name" in change.changed_fields:
            student.first_name = data.get("first_name", student.first_name)
        
        if "last_name" in change.changed_fields:
            student.last_name = data.get("last_name", student.last_name)
        
        if "dob" in change.changed_fields:
            student.dob = data.get("dob", student.dob)
        
        if "class_name" in change.changed_fields:
            class_id = self._get_or_create_class(data.get("class_name", "Unbekannt"))
            student.class_id = class_id

        # Hinweis: Foto-Updates wendet der CSV-Import nicht an —
        # Fotos werden über die Sync-API (/api/sync/change) aktualisiert.

        self.session.add(student)
        result.updated_ids.append(change.school_internal_id)
    
    def _apply_delete(self, change: StudentChange, result: ImportResult) -> None:
        """Soft-Delete: Setzt deleted_at Timestamp."""
        student = self._existing_students.get(change.school_internal_id)
        if not student:
            return
        
        student.deleted_at = datetime.utcnow()
        self.session.add(student)
        result.deleted_ids.append(change.school_internal_id)
