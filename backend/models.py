"""
Hagen-ID Datenbank-Modelle (SQLModel)

Dieses Modul definiert alle Datenbankmodelle für das digitale Schülerausweis-System.

Hierarchie:
    School -> Level (Bildungsgang) -> Class -> Student -> Photo
    
Zusätzliche Tabellen:
    - User: Admins/Lehrer für das Backend
    - PushSubscription: Web Push Benachrichtigungen
    - AuditLog: Änderungsprotokoll für Compliance
"""

import uuid
from datetime import date, datetime
from typing import Optional, List
from enum import Enum
import hashlib

from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import event, select as _sa_select, inspect as _sa_inspect, Column
from services.secrets_at_rest import EncryptedStr, EncryptedDate, blind_index


# ============================================================================
# ENUMS
# ============================================================================

class UserRole(str, Enum):
    """
    Benutzerrollen für das Admin-System.
    - superadmin: Vollzugriff auf alle Schulen (System-Admin)
    - school_admin: Vollzugriff auf eine Schule (Schulleitung/Sekretariat)
    - teacher: Eingeschränkter Zugriff (Lehrer, kann z.B. keine Imports machen)
    """
    SUPERADMIN = "superadmin"
    SCHOOL_ADMIN = "school_admin"
    TEACHER = "teacher"

class ValidityStrategy(str, Enum):
    """
    Strategie für die Berechnung des Gültigkeitsdatums.
    """
    HALF_YEAR = "half_year"      # Automatisch: Ende des aktuellen/nächsten Halbjahres
    CUSTOM_DATE = "custom_date"  # Manuell: Festes Datum für alle neuen Ausweise


# ============================================================================
# CORE TABLES
# ============================================================================

class School(SQLModel, table=True):
    """
    Repräsentiert eine Schule im System.

    Mandantenfähigkeit: Jede Schule hat einen optionalen path_slug,
    der für pfad-basiertes Routing verwendet wird (z.B. domain.de/kkbk).
    """
    __tablename__ = "schools"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Allgemeine Infos
    name: str = Field(index=True, description="Offizieller Name, z.B. 'Käthe-Kollwitz-Berufskolleg'")
    path_slug: Optional[str] = Field(default=None, unique=True, index=True, description="URL-Pfad, z.B. 'kkbk' → domain.de/kkbk")
    
    # Branding
    logo_url: Optional[str] = Field(default=None, description="URL zum Schullogo (für Ausweis-Header)")
    # Lokal zwischengespeichertes Logo (Base64-Data-URI). Wird beim Setzen der
    # logo_url einmalig vom Backend geladen, damit es z.B. für die serverseitige
    # PDF-Erzeugung (Schulbescheinigung) offline verfügbar ist.
    logo_data: Optional[str] = Field(default=None, description="Base64-Data-URI des geladenen Logos (für PDF-Erzeugung)")
    primary_color: Optional[str] = Field(default="#1a73e8", description="Primärfarbe als Hex, z.B. '#ff0000'")
    
    # === SETTINGS ===
    # Gültigkeits-Strategie
    validity_strategy: ValidityStrategy = Field(
        default=ValidityStrategy.HALF_YEAR,
        description="Strategie für Laufzeit-Berechnung (Halbjahr oder Festes Datum)"
    )
    validity_custom_date: Optional[date] = Field(
        default=None,
        description="Festes Enddatum, falls strategy='custom_date' gewählt ist"
    )
    
    # Feature-Toggles als JSON-String: {"schulbescheinigung": true, "push_notifications": false}
    # Erlaubt flexible Aktivierung/Deaktivierung von Features pro Schule
    features_enabled: Optional[str] = Field(default=None, description="JSON mit aktivierten Features")
    
    # URLs
    pwa_base_url: Optional[str] = Field(default=None, description="Basis-URL der PWA, z.B. 'https://ausweis.example.com' (für QR-Codes auf gedruckten Karten)")

    # Kontakt
    contact_email: Optional[str] = Field(default=None, description="Kontakt-E-Mail für Rückfragen/Support")

    # Briefkopf-Daten (für PDF-Bescheinigungen; Schuladmin-Settings)
    address: Optional[str] = Field(default=None, description="Postanschrift, mehrzeilig (Straße\\nPLZ Ort)")
    school_number: Optional[str] = Field(default=None, description="Amtliche Schulnummer")
    phone: Optional[str] = Field(default=None, description="Telefonnummer fürs Briefkopf/PDF")

    # Editierbare Bescheinigungs-Texte (Schuladmin-Template-Editor). NULL = eingebauter
    # Standardtext. Platzhalter: {Name} {Vorname} {GebDatum} {Bildungsgang} {Klasse}
    # (+ {Schule} {Schuljahr}); {Bildungsgang} löst vorerst leer auf (SchILD-Feld folgt).
    cert_template_student: Optional[str] = Field(default=None, description="Template Schulbescheinigung (NULL = Standard)")
    cert_template_teacher: Optional[str] = Field(default=None, description="Template Dienstbescheinigung (NULL = Standard)")

    # API-Key für externe Sync-Tools (z.B. Windows Desktop Client)
    api_key_hash: Optional[str] = Field(
        default=None,
        max_length=64,
        description="SHA256-Hash des API-Keys. Key wird nur einmal angezeigt!"
    )
    api_key_created_at: Optional[datetime] = Field(
        default=None,
        description="Zeitpunkt der API-Key-Erstellung"
    )

    # Optionale IP-Allowlist für die Sync-API. Default AUS (Fallback für Tests /
    # dynamische IPs). Ist sie AN, akzeptiert die Sync-API nur noch Requests aus den
    # in `sync_ip_allowlist` hinterlegten Bereichen — der Key allein reicht dann nicht.
    sync_ip_allowlist_enabled: bool = Field(
        default=False,
        description="Wenn true: Sync-API nur von IPs/CIDRs in sync_ip_allowlist"
    )

    # Beziehungen (1:n)
    levels: List["Level"] = Relationship(back_populates="school")
    classes: List["Class"] = Relationship(back_populates="school")
    users: List["User"] = Relationship(back_populates="school")


class Level(SQLModel, table=True):
    """
    Bildungsgang / Abteilung innerhalb einer Schule.
    
    Beispiele: "Sozialpädagogik", "Technik", "Wirtschaft", "Gestaltung"
    
    Nutzen: Ermöglicht gezielte Push-Nachrichten an alle Schüler eines
    Bildungsgangs (z.B. "Alle Erzieher-Klassen").
    """
    __tablename__ = "levels"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="schools.id", index=True)
    
    name: str = Field(description="Name des Bildungsgangs, z.B. 'Sozialpädagogik'")
    
    # Beziehungen
    school: Optional[School] = Relationship(back_populates="levels")
    classes: List["Class"] = Relationship(back_populates="level")


class Class(SQLModel, table=True):
    """
    Eine Schulklasse.
    
    Hinweis zur Gültigkeit (validity_end):
    - Standard: 31.07. des aktuellen Schuljahres (NRW Schuljahresende)
    - Kann nur VERKÜRZT werden (z.B. bei Abbruch)
    - Beim Roll-up (neues Schuljahr) wird automatisch um 1 Jahr verlängert
    
    Sonderfall "Lehrerkollegium":
    - Virtuelle Klasse für Dienstausweise
    - Gültigkeit: 5 Jahre oder unbegrenzt
    - Frontend zeigt anderen Style (z.B. goldener Rahmen)
    """
    __tablename__ = "classes"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="schools.id", index=True)
    level_id: Optional[int] = Field(default=None, foreign_key="levels.id", index=True,
                                     description="Optional: Zuordnung zu Bildungsgang")
    
    name: str = Field(index=True, description="Klassenname, z.B. 'E1A', 'FOS12B', 'Lehrerkollegium'")
    
    # Standard-Gültigkeit für Schüler dieser Klasse
    validity_end: Optional[date] = Field(
        default=None,
        description="Ausweis-Gültigkeit, Standard: 31.07. des Schuljahres"
    )
    
    # Beziehungen
    school: Optional[School] = Relationship(back_populates="classes")
    level: Optional[Level] = Relationship(back_populates="classes")
    students: List["Student"] = Relationship(back_populates="student_class")


class Student(SQLModel, table=True):
    """
    Ein Schüler (oder Lehrer im Fall der virtuellen 'Lehrerkollegium'-Klasse).
    
    Wichtige Felder:
    - school_internal_id: Die ID aus Schild/Schulverwaltung. KRITISCH für den CSV-Sync!
    - generated_login_code: Der berechnete Login-Code (Vorname[0] + Nachname + Hash)
    - totp_secret: Geheimnis für den Offline-QR-Code (TOTP = Time-based One-Time Password)
    
    Soft-Delete:
    - deleted_at wird gesetzt, wenn der Schüler nicht mehr im CSV auftaucht
    - Ein Cronjob löscht nach 4 Wochen endgültig
    """
    __tablename__ = "students"
    
    # UUID als Primary Key für externe Referenzen (URLs, Tokens)
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        description="Interne UUID, nicht die Schild-ID!"
    )
    
    # Die ID aus der Schulverwaltung (Schild) - WICHTIG für CSV-Matching
    school_internal_id: str = Field(
        index=True,
        description="ID aus Schild/Verwaltung, z.B. '12345'. Muss pro Schule eindeutig sein."
    )
    
    # Personendaten: PII at-rest verschlüsselt. Ein DB-Dump enthält damit
    # keine Klartext-Namen/Geburtsdaten. Suche/Login laufen NICHT auf diesen Spalten,
    # sondern über die Blind-Index-Spalten unten (last_name_bidx/dob_bidx).
    first_name: str = Field(sa_column=Column(EncryptedStr, nullable=False))
    last_name: str = Field(sa_column=Column(EncryptedStr, nullable=False))
    dob: date = Field(sa_column=Column(EncryptedDate, nullable=False))

    # Blind-Indexe (HMAC des normalisierten Werts) — für den exakten Login-/Such-Match auf
    # den verschlüsselten Feldern. Werden per ORM-Event automatisch aus name/dob gepflegt.
    last_name_bidx: Optional[str] = Field(default=None, index=True,
                                          description="HMAC(lower(last_name)) — Login/Suche")
    dob_bidx: Optional[str] = Field(default=None, index=True,
                                    description="HMAC(dob.isoformat()) — Login-Match")
    
    # Auth
    generated_login_code: Optional[str] = Field(
        default=None,
        description="Berechneter Login-Code, z.B. 'MMustermann7x3k'"
    )
    # At-rest verschlüsselt (EncryptedStr) — ein DB-Dump enthält keinen nutzbaren Seed.
    totp_secret: Optional[str] = Field(
        default=None,
        sa_column=Column(EncryptedStr, nullable=True),
        description="Base32-codiertes TOTP-Secret für den Offline-QR-Code (verschlüsselt)"
    )
    
    # Klassenzuordnung
    class_id: Optional[int] = Field(default=None, foreign_key="classes.id", index=True)

    # Denormalisierte Schul-ID (aus der Klasse abgeleitet) — macht RLS-Policies auf
    # students ohne Join möglich. NICHT manuell setzen: ein ORM-Event (unten in dieser Datei)
    # leitet sie bei jedem Insert / Klassenwechsel automatisch aus class.school_id ab.
    school_id: Optional[int] = Field(default=None, foreign_key="schools.id", index=True,
                                     description="Denormalisiert aus class.school_id (für RLS)")
    
    # Status
    is_active: bool = Field(default=True, description="False = deaktiviert (z.B. Schulverweis)")
    deleted_at: Optional[datetime] = Field(
        default=None,
        description="Soft-Delete Timestamp. Wird nach 4 Wochen endgültig gelöscht."
    )
    
    # Highlander Session Management
    # Speichert den SHA256-Hash des aktuellen Session-Tokens.
    # Wenn NULL -> Schüler ist nirgends eingeloggt.
    # Bei jedem Login wird ein neuer Hash gesetzt, was alle alten Sessions invalidiert.
    active_session_token_hash: Optional[str] = Field(
        default=None,
        index=True,
        description="SHA256-Hash des aktiven Session-Tokens. Nur EIN Gerät kann eingeloggt sein."
    )
    
    # Geräte-Schlüssel für die Ausweis-Authentifizierung (QR-Signatur)
    id_public_key: Optional[str] = Field(
        default=None,
        description="Base64-codierter ECDSA-P-256 Public Key für die Ausweis-Authentifizierung"
    )
    id_key_generated_at: Optional[datetime] = Field(
        default=None,
        description="Zeitpunkt der Key-Generierung (bei Login)"
    )
    
    # 6-stellige PIN für Login (statt school_internal_id).
    # At-rest verschlüsselt (EncryptedStr). Match beim Login läuft in Python
    # (Fernet ist nicht deterministisch → kein SQL-`==`), siehe services/auth.py.
    login_pin: Optional[str] = Field(
        default=None,
        sa_column=Column(EncryptedStr, nullable=True),
        description="6-stellige PIN für Login (verschlüsselt). Wird automatisch generiert."
    )
    
    # Optionale E-Mail-Adresse (z.B. dienstliche Adresse beim Kollegium)
    email: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optionale E-Mail-Adresse für PIN-Reset"
    )

    # Dauerhafter Token für die Schulbescheinigungs-Verifizierung.
    # Anders als der rotierende Ausweis-QR ist dieser Token stabil und wird
    # in den QR-Code der gedruckten Schulbescheinigung eingebettet. Beim Scannen
    # löst er eine Live-Abfrage des aktuellen Status (is_active) aus.
    # Widerruf erfolgt automatisch über den Live-Status (deaktiviert/gelöscht).
    cert_verify_token: Optional[str] = Field(
        default=None,
        index=True,
        description="Dauerhafter, widerrufbarer Token für die Schulbescheinigungs-Verifizierung"
    )

    # Manuelle Laufzeit-Überschreibung (z.B. für Lehrer)
    validity_end_override: Optional[date] = Field(
        default=None,
        description="Falls gesetzt, gilt dieses Datum statt dem der Klasse (z.B. für Lehrer-Dienstausweise)."
    )
    
    # Beziehungen
    student_class: Optional[Class] = Relationship(back_populates="students")
    photo: Optional["Photo"] = Relationship(back_populates="student")
    push_subscriptions: List["PushSubscription"] = Relationship(back_populates="student")


class Photo(SQLModel, table=True):
    """
    Schülerfoto, separat gespeichert.
    
    Warum separiert?
    - Performance: Beim CSV-Sync müssen wir nur den Hash vergleichen
    - Nur bei Hash-Änderung wird das (große) Bild aktualisiert
    
    Storage:
    - blob_data: Base64-codiertes Bild ODER Pfad zu externem Storage
    - hash: SHA256 des Original-Bildes für Änderungserkennung
    """
    __tablename__ = "photos"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: uuid.UUID = Field(foreign_key="students.id", unique=True, index=True)

    # Denormalisiert aus student.school_id (per ORM-Event) — für RLS auf photos.
    school_id: Optional[int] = Field(default=None, foreign_key="schools.id", index=True,
                                     description="Denormalisiert aus student.school_id (für RLS)")
    
    # Bilddaten
    # Foto-Blob at-rest verschlüsselt (Biometrie Minderjähriger). Der `hash` unten
    # bleibt ein Hash des Klartext-Bildes (für den Sync-Diff) und ist NICHT das Bild.
    blob_data: Optional[str] = Field(
        default=None,
        sa_column=Column(EncryptedStr, nullable=True),
        description="Base64-codiertes Bild (verschlüsselt)"
    )
    hash: Optional[str] = Field(
        default=None,
        description="SHA256 des Originalbildes für Änderungserkennung beim Import"
    )
    
    # Beziehung
    student: Optional[Student] = Relationship(back_populates="photo")


# ============================================================================
# ADMIN & SECURITY TABLES
# ============================================================================

class User(SQLModel, table=True):
    """
    Admin-Benutzer (Lehrer, Schuladmins, Superadmins).
    
    NICHT zu verwechseln mit Students!
    Dies sind die Benutzer des Admin-Dashboards.
    """
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    email: str = Field(unique=True, index=True)
    password_hash: str = Field(description="bcrypt-Hash des Passworts")
    
    # Zuordnung
    school_id: Optional[int] = Field(
        default=None,
        foreign_key="schools.id",
        description="NULL bei Superadmins (haben Zugriff auf alle Schulen)"
    )
    role: UserRole = Field(default=UserRole.TEACHER)

    # Status
    is_active: bool = Field(default=True)

    # Zeitpunkt der letzten Passwortänderung. Admin-Tokens, die davor ausgestellt
    # wurden, werden abgelehnt (Session-Revocation-Ersatz für die sonst stateless Admin-JWTs).
    # NULL bei Altbestand → keine Ablehnung (rückwärtskompatibel), bis das Passwort einmal wechselt.
    password_changed_at: Optional[datetime] = Field(
        default=None,
        description="Letzte Passwortänderung; entwertet ältere Tokens."
    )

    # Beziehung
    school: Optional[School] = Relationship(back_populates="users")


class AuditLog(SQLModel, table=True):
    """
    Änderungsprotokoll für Compliance und Debugging.
    
    Wichtige Aktionen, die geloggt werden:
    - bulk_import: CSV-Import mit Anzahl geänderter Datensätze
    - manual_delete: Manuelles Deaktivieren/Löschen eines Schülers
    - login_failed: Fehlgeschlagene Login-Versuche (für Rate-Limiting Analyse)
    """
    __tablename__ = "audit_logs"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    user_id: Optional[int] = Field(
        default=None,
        foreign_key="users.id",
        description="Wer hat die Aktion ausgeführt? NULL bei System-Aktionen."
    )

    # Schul-Kontext der Aktion (für mandantengetrennte Auswertung, z.B. Dashboard).
    # NULL bei älteren Einträgen und schulübergreifenden System-Aktionen.
    school_id: Optional[int] = Field(
        default=None,
        foreign_key="schools.id",
        index=True,
        description="Schule, zu der die Aktion gehört (für schulbezogene Auswertung)"
    )

    action: str = Field(index=True, description="Aktions-Typ, z.B. 'bulk_import', 'manual_delete'")
    target_table: Optional[str] = Field(default=None, description="Betroffene Tabelle, z.B. 'students'")

    # Betroffenes Subjekt als String (Schüler-UUID oder Admin-E-Mail) — indexiert,
    # damit der Log-Viewer effizient nach einer ID filtern/„tracen" kann.
    subject_id: Optional[str] = Field(default=None, index=True, description="Betroffene ID (Schüler-UUID / E-Mail)")
    
    # Details als JSON-String (z.B. {"added": 5, "updated": 3, "deleted": 1})
    details: Optional[str] = Field(default=None, description="JSON mit Details zur Aktion")
    
    # Für Photo-Access Logging (Verifizierer-Zugriffe)
    ip_address: Optional[str] = Field(default=None, description="IP-Adresse des Zugreifenden")
    user_agent: Optional[str] = Field(default=None, description="Browser/Gerät Info")
    
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class LoginAttempt(SQLModel, table=True):
    """
    Fehlgeschlagene Login-Versuche für das Rate-Limiting.

    Bewusst in der DB persistiert (statt in-memory), damit die Zählung
    Prozess-Neustarts übersteht und bei mehreren Uvicorn-Workern nicht in
    getrennte Prozess-Zustände zerfällt.

    `identifier` bündelt einen „Bucket" — bewusst zweigleisig genutzt:
    - pro Konto:  "student-acct:<school>:<nachname>:<dob>"  → IP-Rotation wirkungslos
    - pro IP:     "student-ip:<ip>" / "admin-ip:<ip>"        → gegen breites Durchprobieren
    Alte Zeilen werden per Cronjob aufgeräumt.
    """
    __tablename__ = "login_attempts"

    id: Optional[int] = Field(default=None, primary_key=True)
    identifier: str = Field(index=True, description="Bucket-Kennung (Konto oder IP)")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AccessTrace(SQLModel, table=True):
    """
    Zeitlich begrenzte, gezielte Detail-Nachverfolgung ("Trace") — Superadmin-Werkzeug.

    Datenschutz-by-Design: Im Normalbetrieb protokollieren wir bewusst SPARSAM
    (keine Ausweis-Loads, IP nur bei Verifizierung/Login). Für einen konkreten
    Verdachtsfall (z.B. missbräuchliche Nutzung eines bestimmten Ausweises) kann ein
    Superadmin für EINE Schule — optional eingegrenzt auf EINE betroffene ID — einen
    Trace aktivieren. Solange er läuft, werden zusätzlich Ausweis-Loads und IP-Adressen
    für die betroffene(n) Person(en) mitgeschrieben.

    - `subject_id = None`  → gilt für die ganze Schule
    - `subject_id` gesetzt → nur diese eine ID (Schüler-UUID)
    - `expires_at`         → Auto-Aus (Standard 48h); der Trace erlischt automatisch
    - `revoked_at`         → vorzeitig vom Superadmin beendet

    Aktiv ist ein Trace, wenn revoked_at IS NULL UND expires_at > jetzt.
    Das Aktivieren/Beenden selbst wird ebenfalls im Audit-Log vermerkt.
    """
    __tablename__ = "access_traces"

    id: Optional[int] = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="schools.id", index=True,
                           description="Schule, für die der Trace gilt (Pflicht — pro Schule)")
    subject_id: Optional[str] = Field(default=None, index=True,
                                      description="Optional: einzelne betroffene ID; None = ganze Schule")
    enabled_by: Optional[int] = Field(default=None, foreign_key="users.id",
                                      description="Superadmin, der den Trace aktiviert hat")
    reason: Optional[str] = Field(default=None, description="Grund/Notiz für die Nachvollziehbarkeit")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    expires_at: datetime = Field(index=True, description="Auto-Aus-Zeitpunkt")
    revoked_at: Optional[datetime] = Field(default=None, description="Vorzeitig beendet (falls gesetzt)")


class SyncIpAllowlistEntry(SQLModel, table=True):
    """
    Ein erlaubter IP-Bereich für die Sync-API einer Schule.

    `cidr` speichert einen einzelnen Host (z.B. "203.0.113.7" bzw. "203.0.113.7/32")
    oder einen Bereich (z.B. "203.0.113.0/24") — IPv4 und IPv6. Greift nur, wenn an der
    Schule `sync_ip_allowlist_enabled = true` ist. Verwaltet wird die Liste vom
    Schul-Admin (eigene Schule) — dieselbe Berechtigungsstufe wie die API-Key-Verwaltung.
    """
    __tablename__ = "sync_ip_allowlist"

    id: Optional[int] = Field(default=None, primary_key=True)
    school_id: int = Field(foreign_key="schools.id", index=True)
    cidr: str = Field(description="Einzel-IP oder CIDR-Bereich (v4/v6), normalisiert gespeichert")
    note: Optional[str] = Field(default=None, description="Notiz, z.B. 'Sekretariat-PC'")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id",
                                      description="Admin, der den Eintrag angelegt hat")


class PushSubscription(SQLModel, table=True):
    """
    Web Push Subscription für einen Schüler.
    
    Enthält die Browser-spezifischen Schlüssel für die Verschlüsselung
    der Push-Nachrichten (Web Push Protocol).
    
    Ein Schüler kann mehrere Subscriptions haben (verschiedene Geräte/Browser).
    """
    __tablename__ = "push_subscriptions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: uuid.UUID = Field(foreign_key="students.id", index=True)

    # Denormalisiert aus student.school_id (per ORM-Event) — für RLS auf push_subscriptions.
    school_id: Optional[int] = Field(default=None, foreign_key="schools.id", index=True,
                                     description="Denormalisiert aus student.school_id (für RLS)")
    
    # Web Push Protocol Felder
    endpoint: str = Field(description="Push-Service URL des Browsers")
    p256dh: str = Field(description="Public Key für Verschlüsselung")
    auth: str = Field(description="Auth Secret für Verschlüsselung")
    
    # Metadaten
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Beziehung
    student: Optional[Student] = Relationship(back_populates="push_subscriptions")


# ============================================================================
# DENORMALISIERTE school_id AUTOMATISCH PFLEGEN (Mechanismus statt Disziplin)
# ============================================================================
# `students.school_id` / `photos.school_id` / `push_subscriptions.school_id` sind
# denormalisiert (für einfache, schnelle RLS-Policies ohne Join). Statt das an ~8
# Schreibpfaden von Hand zu setzen (und einen zu vergessen), leiten ORM-Events die
# Spalte zentral beim Flush ab — greift für JEDES session.add(...)/Update
# (CSV-Import, Sync, Admin, PWA — alle nutzen die ORM).

def _student_school_id(connection, class_id):
    """Schul-ID zur Klasse (über die Flush-Connection, DB-agnostisch)."""
    if class_id is None:
        return None
    row = connection.execute(
        _sa_select(Class.school_id).where(Class.id == class_id)
    ).first()
    return row[0] if row else None


@event.listens_for(Student, "before_insert")
def _student_fill_school_id_insert(mapper, connection, target):
    target.school_id = _student_school_id(connection, target.class_id)


@event.listens_for(Student, "before_update")
def _student_fill_school_id_update(mapper, connection, target):
    # Nur bei Klassenwechsel neu ableiten — nicht bei jedem is_active/Session-Update
    # (sonst eine Extra-Query pro Login).
    if _sa_inspect(target).attrs.class_id.history.has_changes():
        target.school_id = _student_school_id(connection, target.class_id)


def _student_school_id_by_uuid(connection, student_id):
    if student_id is None:
        return None
    row = connection.execute(
        _sa_select(Student.school_id).where(Student.id == student_id)
    ).first()
    return row[0] if row else None


@event.listens_for(Photo, "before_insert")
def _photo_fill_school_id(mapper, connection, target):
    target.school_id = _student_school_id_by_uuid(connection, target.student_id)


# --- Blind-Indexe für Login/Suche automatisch aus name/dob pflegen ---
def _set_student_bidx(target):
    target.last_name_bidx = (blind_index(target.last_name.strip().lower())
                             if target.last_name else None)
    target.dob_bidx = blind_index(target.dob.isoformat()) if target.dob else None


@event.listens_for(Student, "before_insert")
def _student_bidx_insert(mapper, connection, target):
    _set_student_bidx(target)


@event.listens_for(Student, "before_update")
def _student_bidx_update(mapper, connection, target):
    insp = _sa_inspect(target)
    if insp.attrs.last_name.history.has_changes() or insp.attrs.dob.history.has_changes():
        _set_student_bidx(target)


@event.listens_for(PushSubscription, "before_insert")
def _push_fill_school_id(mapper, connection, target):
    target.school_id = _student_school_id_by_uuid(connection, target.student_id)


# ============================================================================
# App-weite Einstellungen (Key/Value) — GLOBAL, vom Superadmin verwaltet.
# Aktuell: Live-Override der Login-Rate-Limits, statt nur per Env.
# Keine school_id → kein Mandant (kein RLS; Superadmin = Superuser verwaltet es).
# ============================================================================
class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True, max_length=64)
    value: str = Field()
    updated_at: datetime = Field(default_factory=datetime.utcnow)
