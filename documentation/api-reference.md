# Hagen-ID — API-Referenz

Vollständige Übersicht aller REST-API-Endpunkte, Authentifizierungsmechanismen und Sicherheitskonzepte.

---

## Authentifizierung

Das System nutzt **drei verschiedene Auth-Mechanismen** je nach Kontext:

### 1. Admin-Auth (HttpOnly Cookie + JWT)

Für das Admin-Dashboard. Login erzeugt einen HttpOnly-Cookie (`admin_token`), der bei allen Requests automatisch mitgesendet wird.

```
POST /auth/admin/login
→ Set-Cookie: admin_token=eyJ...; HttpOnly; Secure; SameSite=Lax
```

| Eigenschaft | Wert |
|-------------|------|
| Cookie-Name | `admin_token` |
| Domain | Konfigurierbar via `COOKIE_DOMAIN` env-Variable (leer für localhost) |
| Secure | Konfigurierbar via `COOKIE_SECURE` env-Variable (Default: `true`) |
| HttpOnly | `true` (kein JS-Zugriff) |
| SameSite | `Lax` |
| Lebensdauer | `JWT_EXPIRE_MINUTES × 60` Sekunden |

Alle Admin-Endpunkte akzeptieren auch `Authorization: Bearer <token>` als Fallback.

### 2. PWA-Auth (Bearer Token + Session-Token)

Für die Schüler-/Lehrer-App. Login liefert ein JWT + Session-Token.

```
POST /auth/student/login
→ { "access_token": "eyJ...", "session_token": "abc123..." }
```

Jeder PWA-Request sendet zwei Header:
```
Authorization: Bearer <jwt>
X-Session-Token: <session_token>
```

**Highlander-Prinzip:** Nur ein Gerät kann gleichzeitig eingeloggt sein. Ein neuer Login invalidiert die alte Session. Der alte Client erhält `401` mit Header `X-Kicked-Out: true`.

### 3. Sync-API-Auth (API-Key)

Für externe Tools, z.B. [SchildSpider](https://github.com/beitist/schild-spider).

```
X-API-Key: hgn_sk_abc123def456...
```

| Eigenschaft | Wert |
|-------------|------|
| Format | `hgn_sk_` + 64 Hex-Zeichen |
| Speicherung | SHA256-Hash in DB (`schools.api_key_hash`) |
| Rotation | Neuer Key invalidiert alten sofort |
| Anzeige | Nur einmalig beim Generieren sichtbar |

---

## Rate Limiting

| Endpoint | Limit | Reaktion |
|----------|-------|----------|
| `POST /auth/student/login` | 5 Fehlversuche | 15 Min Sperre (pro Konto **und** pro IP) |
| `POST /api/sync/new` | 200 Schüler/Request | `400 Bad Request` |
| `POST /api/sync/change` | 200 Schüler/Request | `400 Bad Request` |
| `POST /api/sync/suspend` | 500 IDs/Request | `400 Bad Request` |
| `POST /admin/import/preview` | 10.000 Zeilen/CSV | `400 Bad Request` |
| Foto-Upload | 10 MB/Datei | `400 Bad Request` |

---

## CORS-Konfiguration

```
Allowed Origins:  Konfigurierbar via ALLOWED_ORIGINS env-Variable
Allowed Methods:  GET, POST, PUT, PATCH, DELETE, OPTIONS
Allowed Headers:  Authorization, Content-Type, X-Session-Token,
                  X-Requested-With, X-API-Key
Credentials:      true (Cookies erlaubt)
Preflight-Cache:  3600 Sekunden
```

---

## Endpunkte

### Health & System

| Methode | Pfad | Auth | Beschreibung |
|---------|------|------|-------------|
| `GET` | `/` | — | Versionsinformation |
| `GET` | `/health` | — | Health-Check mit DB-Status |

---

### Auth (`/auth`)

#### `POST /auth/admin/login`
Admin-Login via E-Mail + Passwort. Setzt HttpOnly-Cookie.

**Request:**
```json
{ "email": "admin@schule.de", "password": "..." }
```
**Response:** `{ "message": "Login erfolgreich", "user_type": "admin" }`

---

#### `POST /auth/admin/logout`
Löscht den Admin-Cookie.

---

#### `POST /auth/student/login`
Schüler-Login via Nachname + Geburtsdatum + 6-stellige PIN.

**Request:**
```json
{
    "last_name": "Mustermann",
    "dob": "2005-03-15",
    "school_id": 1,
    "login_pin": "123456"
}
```

> **Hinweis:** `school_id` ist optional. Wenn nicht angegeben, sucht das Backend den Schüler global (Auto-Erkennung anhand Name + Geburtsdatum + PIN).

```json
{
    "last_name": "Mustermann",
    "dob": "2005-03-15",
    "login_pin": "123456"
}
```
**Response:** Setzt zusätzlich HttpOnly-Cookies (`student_token`, `session_token`).
```json
{
    "access_token": "eyJ...",
    "token_type": "bearer",
    "expires_in": 900,
    "totp_secret": "JBSWY3DPE...",
    "session_token": "abc123..."
}
```

**Sicherheit:**
- 5 Fehlversuche → 15 Min Sperre (pro Konto und pro IP)
- Session-Token für Highlander-Validierung
- TOTP-Secret für Offline-QR-Generierung
- Es wird KEIN privater Schlüssel ausgeliefert: das Signier-Keypair entsteht auf
  dem Gerät (ECDSA P-256, non-extractable); nur der Public Key wird via
  `POST /pwa/register-key` registriert

---

#### `GET /auth/me`
Gibt den aktuell authentifizierten User zurück (Admin oder Schüler).

**Auth:** Cookie oder Bearer Token

---

#### `POST /auth/admin/create`
Neuen Admin-User anlegen.

**Auth:** Admin (Superadmin für alle Rollen, School-Admin nur für Lehrer)

**Request:**
```json
{
    "email": "lehrer@schule.de",
    "password": "...",
    "school_id": 1,
    "role": "teacher"
}
```

---

### PWA — Schüler-App (`/pwa`)

Alle Endpoints erfordern die Schüler-Session — primär via HttpOnly-Cookies
(`student_token` + `session_token`), alternativ `Authorization: Bearer <jwt>` +
`X-Session-Token: <token>`.

#### `GET /pwa/me`
Kartendaten für Ausweis-Anzeige (wird vom Service Worker gecacht).

**Response:**
```json
{
    "id": "uuid",
    "first_name": "Max",
    "last_name": "Mustermann",
    "dob": "15.03.2005",
    "class_name": "E1A",
    "school_name": "Berufskolleg",
    "school_color": "#1a73e8",
    "valid_until": "31.07.2026",
    "is_teacher": false,
    "totp_secret": "...",
    "has_photo": true,
    "photo_url": "/pwa/photo/{id}",
    "photo_b64": "data:image/jpeg;base64,...",
    "schulbescheinigung_enabled": false,
    "session_hash_prefix": "abcdef12"
}
```

---

#### `GET /pwa/photo/{student_id}`
Eigenes Foto als Bild. Nur das eigene Foto abrufbar (403 bei fremder ID).

**Response:** Binäre Bilddaten (JPEG/PNG/WebP, auto-detected)
**Cache:** `private, max-age=86400`

---

#### `POST /pwa/photo`
Eigenes Foto hochladen. **Nur für Lehrkräfte** (Klasse "Lehrerkollegium").

**Request:** Multipart `file` (Bild, max 5 MB)
**Response:** `{ "success": true }`
**Sicherheit:** 403 wenn User kein Lehrer ist.

---

> **Hinweis:** Es gibt bewusst **keinen** serverseitigen QR- oder Verify-Endpunkt.
> Die QR-Erzeugung läuft **vollständig clientseitig** (die PWA signiert offline mit
> ihrem Geräte-Key), die Verifizierung **ausschließlich** über [`GET /check`](#get-checkppayload)
> — dort werden TOTP, Highlander-Session, Timestamp-Frische **und** die
> ECDSA-P-256-Signatur geprüft.

---

#### `POST /pwa/push/subscribe` · `DELETE /pwa/push/unsubscribe`
Push-Benachrichtigungen registrieren/abmelden.

---

### Verifizierung (`/check`)

#### `GET /check?p={payload}`
Öffentliche QR-Code-Verifizierung (für Kontrollpersonal).

**Auth:** Keine (öffentlich zugänglich)

**Payload-Format:** `student_id:totp_code:session_hash:timestamp:signature`

**Prüfschritte:**
1. Payload parsen und Student laden
2. TOTP-Code verifizieren (±1 Intervall Toleranz)
3. Highlander-Check: Session-Hash stimmt mit DB überein?
4. Student aktiv und nicht gelöscht?
5. ECDSA-P-256-Signatur prüfen (über den registrierten Geräte-Public-Key)

**Response:** HTML-Seite mit Ergebnis (grün = gültig, rot = ungültig), Foto und Personendaten.

---

### Admin-Dashboard (`/admin`)

Alle Endpoints erfordern Admin-Auth (Cookie oder Bearer Token).

#### Dashboard

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `GET` | `/admin/dashboard?school_id=1` | Statistiken (Schüler, Klassen, letzter Import) |

---

#### Schülerverwaltung

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `GET` | `/admin/students` | Schüler-Liste (paginiert, filterbar) |
| `GET` | `/admin/students/{id}` | Schüler-Details |
| `POST` | `/admin/students?school_id=1` | Schüler manuell anlegen |
| `PATCH` | `/admin/students/{id}/deactivate` | Deaktivieren |
| `PATCH` | `/admin/students/{id}/activate` | Reaktivieren |
| `DELETE` | `/admin/students/{id}` | Soft-Delete (4 Wochen, dann endgültig) |
| `POST` | `/admin/students/{id}/photo` | Foto hochladen (Admin) |
| `GET` | `/admin/students/{id}/photo` | Foto abrufen |
| `PATCH` | `/admin/students/{id}/pin` | Login-PIN ändern/generieren |

**`GET /admin/students` — Query-Parameter:**

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| `school_id` | (required) | Schul-ID |
| `class_id` | — | Filter nach Klasse |
| `search` | — | Suche nach Name oder ID |
| `include_inactive` | `false` | Auch deaktivierte anzeigen |
| `exclude_teachers` | `true` | Lehrerkollegium ausschließen |
| `limit` | `50` | Max. Ergebnisse (≤ 200) |
| `offset` | `0` | Pagination-Offset |

**`POST /admin/students` — Schüler anlegen:**
```json
{
    "first_name": "Max",
    "last_name": "Mustermann",
    "dob": "2005-03-15",
    "class_name": "E1A",
    "school_internal_id": null,
    "is_teacher": false
}
```
- `is_teacher: true` → Klasse wird automatisch "Lehrerkollegium", Gültigkeit = 31.07. in 5 Jahren
- `school_internal_id: null` → wird automatisch generiert (`MAN-{hex}`)

---

#### Klassen

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `GET` | `/admin/classes?school_id=1` | Klassen-Liste mit Schülerzahl |
| `GET` | `/admin/classes/{id}/print-data` | Druckdaten (Schüler + PINs) |

---

#### CSV-Import

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `POST` | `/admin/import/preview?school_id=1` | CSV parsen + Diff-Vorschau |
| `POST` | `/admin/import/apply?school_id=1` | CSV-Änderungen anwenden |

**Vorschau-Response:**
```json
{
    "total_in_csv": 150,
    "total_in_db": 148,
    "inserts": 2,
    "updates": 5,
    "deletes": 1,
    "unchanged": 145,
    "errors": 0,
    "sample_changes": [...]
}
```

---

### Öffentliche Schulinfo (`/schools/public`)

Ohne Authentifizierung zugänglich. Wird von der PWA beim Laden verwendet.

| Methode | Pfad | Auth | Beschreibung |
|---------|------|------|-------------|
| `GET` | `/schools/public` | — | Alle Schulen (Name, Slug, Farbe, Logo) |
| `GET` | `/schools/public/{slug}` | — | Einzelne Schule per URL-Pfad auflösen |

**`GET /schools/public` — Response:**
```json
[
    {
        "id": 1,
        "name": "Berufskolleg",
        "path_slug": "kkbk",
        "logo_url": "https://...",
        "primary_color": "#1a73e8"
    }
]
```

**`GET /schools/public/{slug}`** — Gibt eine einzelne Schule zurück (404 wenn Slug unbekannt).

---

### Schulverwaltung (`/schools`)

Nur Superadmins, außer Settings (auch School-Admins).

| Methode | Pfad | Auth | Beschreibung |
|---------|------|------|-------------|
| `GET` | `/schools` | Superadmin | Alle Schulen |
| `GET` | `/schools/{id}` | Superadmin | Schul-Details |
| `POST` | `/schools` | Superadmin | Schule anlegen |
| `PUT` | `/schools/{id}` | Superadmin | Schule bearbeiten |
| `DELETE` | `/schools/{id}` | Superadmin | Schule löschen (nur wenn leer) |
| `GET` | `/schools/{id}/settings` | Admin | Einstellungen lesen |
| `PUT` | `/schools/{id}/settings` | Admin | Einstellungen ändern |
| `GET` | `/schools/{id}/api-key` | Admin | API-Key-Status prüfen |
| `POST` | `/schools/{id}/api-key` | Admin | API-Key generieren |
| `DELETE` | `/schools/{id}/api-key` | Admin | API-Key widerrufen |

---

### Sync-API (`/api/sync`)

Für externe Tools, z.B. [SchildSpider](https://github.com/beitist/schild-spider).
Auth via `X-API-Key` Header.
Details und Diff-Algorithmus: [docs/sync-api.md](../docs/sync-api.md).

**Lehrer sind Teil der Voll-Sync:** Sie sind Datensätze mit
`class_name = "Lehrerkollegium"` (siehe docs/sync-api.md §6).

#### `GET /api/sync/manifest`
Aktueller Datenbestand mit Hashes für Diff-Vergleich.

**Response:**
```json
{
    "school_id": 1,
    "school_name": "Berufskolleg",
    "generated_at": "2026-02-20T14:30:00Z",
    "total_count": 150,
    "students": [
        {
            "school_internal_id": "10001",
            "first_name": "Max",
            "last_name": "Mustermann",
            "dob": "2005-03-15",
            "email": "max@schule.de",
            "class_name": "E1A",
            "is_active": true,
            "has_photo": true,
            "photo_hash": "sha256...",
            "data_hash": "sha256..."
        }
    ]
}
```

**`data_hash`** = SHA256 über `lower(vorname)|lower(nachname)|geb|lower(klasse)|lower(email)`

---

#### `POST /api/sync/new`
Neue Schüler anlegen (Bulk, max 200/Request).

**Request:**
```json
{
    "students": [
        {
            "school_internal_id": "10001",
            "first_name": "Max",
            "last_name": "Mustermann",
            "dob": "2005-03-15",
            "email": "max@schule.de",
            "class_name": "E1A",
            "photo_base64": "/9j/4AAQ..."
        }
    ]
}
```

**Response:**
```json
{
    "success": true,
    "processed": 1,
    "results": [
        {
            "school_internal_id": "10001",
            "status": "created",
            "message": null
        }
    ]
}
```

**Status-Werte:** `created`, `already_exists`, `error`

---

#### `POST /api/sync/change`
Bestehende Schüler aktualisieren (Partial Update, max 200/Request).

Nur gesetzte Felder werden aktualisiert. Foto wird bei Bedarf ersetzt.

**Status-Werte:** `updated`, `not_found`, `error`

---

#### `POST /api/sync/suspend`
Schüler deaktivieren (max 500/Request).

**Request:**
```json
{
    "school_internal_ids": ["10001", "10002"]
}
```

Setzt `is_active=false` und `deleted_at=now()`. DSGVO-Garbage-Collection löscht nach 4 Wochen endgültig.

**Status-Werte:** `suspended`, `not_found`

---

## DSGVO & Datenhaltung

| Aspekt | Umsetzung |
|--------|-----------|
| Soft-Delete | `deleted_at` Timestamp, Student kann noch reaktiviert werden |
| Hard-Delete | Nach 4 Wochen automatisch (Daily Cleanup Job, 03:00 UTC) |
| Foto-Speicherung | Base64 in PostgreSQL, kein Dateisystem |
| Session-Isolation | Highlander-Prinzip: max. 1 aktives Gerät pro Person |
| Audit-Log | Alle Bulk-Operationen (Import, Sync) werden protokolliert |
