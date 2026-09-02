# Hagen-ID — Sync-API (Schnittstelle für externe Tools)

Referenz für den Bau eines Sync-Tools, das SchILD-Daten mit Hagen-ID abgleicht.
Die Referenz-Implementierung ist [SchildSpider](https://github.com/beitist/schild-spider) —
das Management-Tool fürs Sekretariat.

> **Kurzfassung des Zyklus:** `GET /manifest` (Server-Ist) → lokal gegen SchILD diffen →
> Deltas via `POST /new`, `POST /change`, `POST /suspend` schicken. Der Diff basiert auf
> zwei Hashes (`data_hash`, `photo_hash`), die das Tool **bit-genau nachbauen** muss.

---

## 1. Überblick

| | |
|---|---|
| **Base-URL** | `https://api.example.com` (Prod, eigene API-Domain) · lokal `http://localhost:8000` |
| **Prefix** | Alle Endpunkte unter `/api/sync` |
| **Auth** | Header `X-API-Key: hgn_sk_...` (schulgebunden) |
| **Content-Type** | `application/json` (UTF-8) |
| **Zeit/Datum** | Datum als `YYYY-MM-DD` **oder** `DD.MM.YYYY` (Eingang); Ausgang immer `YYYY-MM-DD` |

**Endpunkte:**

| Methode | Pfad | Zweck | Max/Request |
|---|---|---|---|
| `GET`  | `/api/sync/manifest` | Aktueller Server-Datenbestand (Hashes für Diff) | — |
| `POST` | `/api/sync/new`      | Neue Personen anlegen (Bulk) | 200 |
| `POST` | `/api/sync/change`   | Bestehende aktualisieren (Bulk, partiell) | 200 |
| `POST` | `/api/sync/suspend`  | Deaktivieren (Bulk) | 500 |

---

## 2. Authentifizierung

Jeder Request braucht den Header:

```
X-API-Key: hgn_sk_<...>
```

- Der Key wird serverseitig SHA256-gehasht und über einen **indizierten Lookup** auf die
  Schule aufgelöst. **Die Schule wird aus dem Key abgeleitet** — sie wird nie vom Client
  übergeben. Damit ist die Mandantentrennung auf diesem Kanal strukturell erzwungen: ein
  Key kann ausschließlich Daten *seiner* Schule sehen/ändern.
- Key erzeugen/rotieren: im Admin-Dashboard der Schule (Superadmin). Format beginnt immer
  mit `hgn_sk_`.

**Fehlercodes der Auth:**

| Status | Bedeutung |
|---|---|
| `401` | Header fehlt, Format ≠ `hgn_sk_...`, oder Key unbekannt/widerrufen |
| `403` | IP-Allowlist ist für die Schule **aktiv** und die Client-IP liegt in keinem erlaubten Bereich |

**IP-Allowlist (optional):** Per Default **AUS** → keine IP-Einschränkung. Ist sie an der
Schule aktiv, muss die Egress-IP des Sync-Servers in einem hinterlegten CIDR liegen (der Key
wird dann zum zweiten Faktor). Aktivierung + CIDRs pflegt der Admin im Dashboard.

---

## 3. Der Sync-Zyklus (Diff-Algorithmus)

```
1. GET /api/sync/manifest
     → Liste aller Personen der Schule mit { school_internal_id, data_hash,
       photo_hash, is_active, ... }

2. Aus SchILD die Soll-Liste bauen. Für jede Person:
     data_hash_local  = compute_data_hash(...)     # siehe §5
     photo_hash_local = sha256(photo_base64)        # siehe §5

3. Mengen bilden (Schlüssel = school_internal_id):
     NEU        = in SchILD, nicht im Manifest                → POST /new
     GEÄNDERT   = in beiden, data_hash unterschiedlich        → POST /change (Stammdaten)
     FOTO_NEU   = in beiden, photo_hash unterschiedlich        → POST /change (photo_base64)
     ENTFALLEN  = im Manifest (aktiv), nicht mehr in SchILD    → POST /suspend

4. Deltas in Batches senden (Limits s. o.). Antworten pro Person auswerten.
```

Der Sinn der Hashes: nur echte Änderungen übertragen (spart Last, v. a. bei Fotos).

---

## 4. Endpunkte im Detail

### 4.1 `GET /api/sync/manifest`

Gibt den kompletten aktuellen Datenbestand der Schule zurück (alle **nicht endgültig
gelöschten** Personen; suspendierte, die via `/suspend` `deleted_at` gesetzt bekamen,
erscheinen **nicht** mehr — siehe §7 „Reaktivierung").

**Response `200`:**

```json
{
  "school_id": 1,
  "school_name": "Käthe-Kollwitz-Gesamtschule",
  "generated_at": "2026-08-03T09:12:44.123456Z",
  "total_count": 2,
  "students": [
    {
      "school_internal_id": "10231",
      "first_name": "Max",
      "last_name": "Mustermann",
      "dob": "2010-03-15",
      "email": null,
      "class_name": "5a",
      "is_active": true,
      "has_photo": true,
      "photo_hash": "e3b0c442...b855",
      "data_hash": "a1b2c3...f9"
    },
    {
      "school_internal_id": "L-0007",
      "first_name": "Erika",
      "last_name": "Muster",
      "dob": "1980-05-01",
      "email": "muster@schule.de",
      "class_name": "Lehrerkollegium",
      "is_active": true,
      "has_photo": false,
      "photo_hash": null,
      "data_hash": "9f8e7d..."
    }
  ]
}
```

| Feld | Typ | Hinweis |
|---|---|---|
| `is_active` | bool | `false` = im Dashboard deaktiviert, aber noch nicht via Sync suspendiert |
| `has_photo` | bool | Es existiert ein Foto-Blob |
| `photo_hash` | str/null | `sha256` über den beim Anlegen gesendeten Base64-String (§5) |
| `data_hash` | str | `sha256` über die Stammdaten (§5) |

### 4.2 `POST /api/sync/new`

Legt neue Personen an. Server generiert automatisch **Login-Code, TOTP-Secret und die
6-stellige Login-PIN** — diese werden **nicht** in der Antwort zurückgegeben (PINs holt man
über die Druck-/Admin-Funktion im Dashboard).

**Request:**

```json
{
  "students": [
    {
      "school_internal_id": "10231",
      "first_name": "Max",
      "last_name": "Mustermann",
      "dob": "2010-03-15",
      "email": null,
      "class_name": "5a",
      "photo_base64": null
    }
  ]
}
```

| Feld | Pflicht | Hinweis |
|---|---|---|
| `school_internal_id` | ✅ | Stabiler Schlüssel aus SchILD, pro Schule eindeutig |
| `first_name`, `last_name` | ✅ | |
| `dob` | ✅ | `YYYY-MM-DD` oder `DD.MM.YYYY` |
| `class_name` | ✅ | Klasse wird bei Bedarf automatisch angelegt. `"Lehrerkollegium"` ⇒ Lehrer (§6) |
| `email` | — | |
| `photo_base64` | — | Base64-String des Bildes (siehe §5.2) |

**Response `200`** (pro Person ein Ergebnis):

```json
{ "success": true, "processed": 1,
  "results": [ { "school_internal_id": "10231", "status": "created", "message": null } ] }
```

`status` ∈ `created` · `already_exists` (ID existiert bereits, aktiv) · `error` (z. B. ungültiges Datum, message erklärt).

### 4.3 `POST /api/sync/change`

Aktualisiert bestehende Personen — **partiell**: nur gesetzte Felder werden geändert
(`null`/fehlend = unverändert). Lookup per `school_internal_id`.

**Request:**

```json
{
  "students": [
    { "school_internal_id": "10231", "class_name": "6a" },
    { "school_internal_id": "10250", "last_name": "Neu-Name", "photo_base64": "/9j/4AAQ..." }
  ]
}
```

Felder wie bei `/new`, alle optional außer `school_internal_id`. `class_name` darf auf
`"Lehrerkollegium"` wechseln (Voll-Sync). Ist `photo_base64` gesetzt, wird das Foto ersetzt.

**Response `200`:** `status` ∈ `updated` · `not_found` (kein aktiver Datensatz mit der ID) · `error`.

### 4.4 `POST /api/sync/suspend`

Deaktiviert Personen (`is_active=false`, `deleted_at=now()`). Die DSGVO-Garbage-Collection
löscht endgültig nach ~4 Wochen.

**Request:**

```json
{ "school_internal_ids": ["10231", "L-0099"] }
```

**Response `200`:** `status` ∈ `suspended` · `not_found`.

> ⚠️ **Voll-Sync-Warnung:** `/suspend` deaktiviert genau die gesendeten IDs — das Tool ist
> alleiniger Entscheider. Ein unvollständiger SchILD-Pull, der die „ENTFALLEN"-Menge zu groß
> macht, deaktiviert entsprechend viele echte Personen (inkl. Lehrer). Baue einen
> **Plausibilitäts-Riegel** ein (z. B. abbrechen/rückfragen, wenn eine Sync-Runde > X % der
> aktiven Datensätze suspendieren würde).

**HTTP `400`** wenn Batch-Limit überschritten (`/new`,`/change` > 200; `/suspend` > 500).

---

## 5. Die Hashes (bit-genau nachbauen!)

Damit der Diff funktioniert, müssen die lokal berechneten Hashes **exakt** denen des Servers
entsprechen. Beide sind `SHA256` als **Hex-String** (lowercase).

### 5.1 `data_hash` — Stammdaten

Kanonische Zeichenkette, Felder mit `|` verbunden, dann SHA256:

```
canonical = "|".join([
    first_name.strip().lower(),
    last_name.strip().lower(),
    dob_iso,                                  # IMMER "YYYY-MM-DD" (nicht DD.MM.YYYY!)
    class_name.strip().lower() if class_name else "",
    (email or "").strip().lower()
])
data_hash = sha256( canonical.encode("utf-8") ).hexdigest()
```

**Fallstricke:**
- **`dob` muss ISO `YYYY-MM-DD`** sein, *bevor* gehasht wird. Liest dein Tool `15.03.2010`
  aus SchILD, vorher nach `2010-03-15` konvertieren — sonst matcht der Hash nie und du
  produzierst Dauer-`/change`.
- Reihenfolge ist fix: **first, last, dob, class, email**.
- `email` leer ⇒ leerer String `""` (nicht das Wort „null").
- Alles außer `dob` wird `.strip().lower()`-normalisiert.

**C#-Beispiel:**

```csharp
static string DataHash(string first, string last, string dobIso, string cls, string email) {
    string canonical = string.Join("|", new[] {
        first.Trim().ToLowerInvariant(),
        last.Trim().ToLowerInvariant(),
        dobIso,                                   // "2010-03-15"
        string.IsNullOrEmpty(cls) ? "" : cls.Trim().ToLowerInvariant(),
        (email ?? "").Trim().ToLowerInvariant()
    });
    using var sha = System.Security.Cryptography.SHA256.Create();
    var b = sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(canonical));
    return Convert.ToHexString(b).ToLowerInvariant();
}
```

### 5.2 `photo_hash` — Foto

```
photo_hash = sha256( photo_base64.encode("utf-8") ).hexdigest()
```

- Gehasht wird **der Base64-String selbst**, nicht die rohen Bildbytes. Hashe **exakt den
  String, den du in `photo_base64` sendest** — gleiche Kodierung, gleiches (Nicht-)Vorhanden-
  sein eines `data:image/...;base64,`-Präfixes. Wichtig ist nur: send und hash denselben String.
- Für den Diff: `photo_hash_local != manifest.photo_hash` ⇒ Foto per `/change` neu senden.

---

## 6. Lehrer-Konvention

- Lehrer sind **`Student`-Datensätze in der Klasse `"Lehrerkollegium"`** (exakter,
  case-sensitiver Name nach `.strip()`). Es gibt **kein** separates Feld.
- Über `/new`, `/change`, `/suspend` genauso zu behandeln wie Schüler — nur `class_name =
  "Lehrerkollegium"` setzen.
- Der Server vergibt der Klasse „Lehrerkollegium" beim **ersten** Anlegen die
  **Lehrer-Gültigkeit** (heute + 5 Jahre, 31.07.) statt Schuljahresende.
- Lehrer erscheinen im `/manifest` mit `class_name: "Lehrerkollegium"`.

**Bekannte Schwächen (bewusst dokumentiert):**
- **Magic-String:** Tippfehler/andere Groß-/Kleinschreibung ⇒ still als Schüler behandelt.
  Sende den String **exakt** `Lehrerkollegium`.
- **Geteilte Gültigkeit:** Alle Lehrer teilen die eine Klasse; deren `validity_end` steht bei
  Erst-Anlage fest und erneuert sich nicht automatisch.

> *(Mögliche spätere Härtung: explizites `is_teacher: true` im Payload statt Magic-String.)*

---

## 7. Edge-Cases & Caveats

- **`school_internal_id` ist nicht DB-eindeutig** (nur Konvention + Index). Folgen:
  - **Batch-Duplikate:** Zwei Einträge mit gleicher (neuer) ID im *selben* `/new`-Batch werden
    **beide** angelegt. → Dein Tool muss Batches **deduplizieren**.
  - **Reaktivierung:** Eine via `/suspend` deaktivierte Person hat `deleted_at` gesetzt und
    erscheint **nicht** mehr im Manifest. Kehrt sie innerhalb der ~4-Wochen-GC-Frist zurück,
    sieht dein Tool sie als „NEU" ⇒ `/new` legt einen **zweiten** Datensatz (frische PIN) an;
    der alte lingert bis zur GC. `/change` würde sie **nicht** finden (`not_found`). → Lege
    eine Reaktivierungs-Policy fest (z. B. innerhalb der Frist nicht neu anlegen, sondern
    manuell reaktivieren).
- **`is_active=false` im Manifest** bedeutet „im Dashboard deaktiviert, aber nicht via Sync
  suspendiert" — noch abgleichbar. Via Sync suspendierte fallen ganz aus dem Manifest.
- **PINs** kommen nicht über die Sync-API zurück — Ausgabe/Druck läuft übers Admin-Dashboard.
- **Idempotenz:** `/new` auf bereits aktive ID ⇒ `already_exists` (kein Duplikat). `/change`
  ist partiell und wiederholbar. `/suspend` auf bereits Suspendierte ⇒ `not_found`.

---

## 8. Fehler- & Statuswerte (Übersicht)

| HTTP | Wann |
|---|---|
| `200` | Verarbeitet (Ergebnis **pro Person** in `results[].status`) |
| `400` | Batch-Limit überschritten |
| `401` | API-Key fehlt/ungültig |
| `403` | IP-Allowlist aktiv, IP nicht erlaubt |

Per-Person-`status`: `created` · `already_exists` · `updated` · `suspended` · `not_found` · `error`
(bei `error` erklärt `message`, z. B. ungültiges Datum).

---

## 9. Referenz-Ablauf (Pseudocode)

```python
manifest = GET("/api/sync/manifest")                      # {school_internal_id: {data_hash, photo_hash, is_active}}
server = { s.school_internal_id: s for s in manifest.students }
soll   = read_from_schild()                               # inkl. Lehrer mit class_name="Lehrerkollegium"

new, change, photo, suspend = [], [], [], []
for p in dedupe(soll):
    dh = data_hash(p.first, p.last, iso(p.dob), p.cls, p.email)
    if p.id not in server:
        new.append(p)
    else:
        if dh != server[p.id].data_hash:          change.append(p)
        if p.photo and sha256(p.b64) != server[p.id].photo_hash: photo.append(p)

for id in server:                                          # nur aktive
    if server[id].is_active and id not in soll_ids:        suspend.append(id)

assert len(suspend) < 0.2 * count_active(server)           # Plausibilitäts-Riegel!

POST_batched("/api/sync/new",     new,     size=200)
POST_batched("/api/sync/change",  change+photo, size=200)
POST_batched("/api/sync/suspend", suspend, size=500)
```
