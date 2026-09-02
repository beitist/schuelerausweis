# Lehrer-Sync — Spezifikation für ein Sync-Tool-Plugin

Spezifikation für ein **dediziertes Lehrer-Plugin**, z. B. für
[SchildSpider](https://github.com/beitist/schild-spider). Es nutzt **dieselbe** Sync-API wie der
Schüler-Sync (Auth, Endpunkte, Hashes) — die Mechanik steht in [sync-api.md](sync-api.md).
Dieses Dokument beschreibt nur, was für Lehrer **anders** ist.

> **Die eine goldene Regel:** Lehrer sind `Student`-Datensätze in der Klasse
> `"Lehrerkollegium"`. Das Plugin arbeitet **ausschließlich auf dieser Teilmenge** —
> beim Lesen des Manifests **und** beim Suspend. Sonst kollidiert es mit dem Schüler-Plugin.

---

## 1. Warum ein getrenntes Plugin sauber ist (und die Falle)

Schüler- und Lehrer-Plugin teilen sich **ein** Manifest (`GET /api/sync/manifest` liefert
*beide*). Wenn ein Plugin „alle deaktivieren, die nicht in meiner Liste sind" macht, ohne zu
filtern, **löscht es die Datensätze des anderen**:

- Schüler-Plugin ohne Filter → würde alle **Lehrer** suspendieren (stehen nicht in der
  SchILD-Schülerliste).
- Lehrer-Plugin ohne Filter → würde alle **Schüler** suspendieren.

**Lösung — jedes Plugin filtert das Manifest auf seine Domäne:**

```
TEACHER_CLASS = "Lehrerkollegium"     # EINE Konstante, überall verwenden

# Server-Sicht des Lehrer-Plugins:
server_teachers = [ s for s in manifest.students if s.class_name == TEACHER_CLASS ]

# Server-Sicht des Schüler-Plugins:
server_students = [ s for s in manifest.students if s.class_name != TEACHER_CLASS ]
```

So laufen beide Plugins **unabhängig und in beliebiger Reihenfolge** kollisionsfrei. Weil das
Plugin die Konstante `TEACHER_CLASS` sowohl zum **Senden** (`class_name`) als auch zum
**Filtern** benutzt, entfällt die „Magic-String-Fragilität" aus sync-api.md §6 innerhalb des
Plugins — es gibt nur eine Stelle mit dem Literal.

---

## 2. Datenzuordnung SchILD-Lehrer → Payload

Gleiche Felder wie beim Schüler, mit diesen Lehrer-Spezifika:

| Payload-Feld | Quelle / Regel |
|---|---|
| `school_internal_id` | **Stabiler, eindeutiger Lehrer-Schlüssel.** Siehe ⚠️ unten. |
| `first_name`, `last_name` | Lehrer-Stammdaten aus SchILD |
| `dob` | Geburtsdatum des Lehrers, als `YYYY-MM-DD` (vor dem Hash konvertieren!) |
| `email` | dienstliche E-Mail (optional) |
| `class_name` | **immer exakt** `"Lehrerkollegium"` (= `TEACHER_CLASS`) |
| `photo_base64` | Lichtbild (optional), s. sync-api.md §5.2 |

> ⚠️ **`school_internal_id` ist ein gemeinsamer Namensraum mit den Schülern und NICHT
> DB-eindeutig** (nur Konvention). Ein Lehrer-Schlüssel, der zufällig mit einer Schüler-ID
> kollidiert, führt zu Datensalat. **Empfehlung: Lehrer-IDs eindeutig namespacen**, z. B.
> Präfix `L-` + SchILD-Lehrer-ID/Kürzel (`L-4711`, `L-MUE`) oder die SchILD-Lehrer-GUID.
> Wichtig ist nur: pro Lehrer **stabil** (ändert sich nie) und **kollisionsfrei** zu Schülern.

Der `data_hash` (sync-api.md §5.1) ist identisch zu berechnen — für Lehrer ist
`class_name.lower()` konstant `"lehrerkollegium"`.

---

## 3. Der Lehrer-Sync-Zyklus

```python
TEACHER_CLASS = "Lehrerkollegium"

manifest = GET("/api/sync/manifest")                       # gleiche API wie Schüler
server = { s.school_internal_id: s
           for s in manifest.students
           if s.class_name == TEACHER_CLASS }              # <-- NUR Lehrer!

soll = read_lehrer_from_schild()                           # deine Plugin-Logik
soll = dedupe_by_id(soll)                                  # Batch-Duplikate raus (§5)
soll_ids = { t.school_internal_id for t in soll }

new, change, photo = [], [], []
for t in soll:
    payload = to_payload(t, class_name=TEACHER_CLASS)      # class_name IMMER setzen
    dh = data_hash(t.first, t.last, iso(t.dob), TEACHER_CLASS, t.email)
    if t.id not in server:
        new.append(payload)
    else:
        if dh != server[t.id].data_hash:                          change.append(payload)
        if t.photo and sha256(t.b64) != server[t.id].photo_hash:  photo.append(payload_with_photo)

# ENTFALLENE Lehrer — NUR aus der Lehrer-Teilmenge!
suspend = [ id for id, s in server.items() if s.is_active and id not in soll_ids ]

# --- PLAUSIBILITÄTS-RIEGEL (bei Lehrern besonders wichtig, kleine Population) ---
if len(soll) == 0:
    # 0 Lehrer aus SchILD = fast sicher Pull-Fehler → Suspend-Schritt AUSLASSEN (nicht fragen).
    # new/change trotzdem normal; nur suspend wird übersprungen.
    suspend = []
elif len(suspend) > max(3, 0.3 * len(server)):
    # Ungewöhnlich viele → NICHT hart abbrechen, sondern bestätigen lassen.
    if not confirm(f"{len(suspend)} von {len(server)} Lehrern deaktivieren? [Ja/Nein]"):
        suspend = []        # abgelehnt → diese Runde nicht suspenden (new/change laufen weiter)

POST_batched("/api/sync/new",     new,          size=200)
POST_batched("/api/sync/change",  change+photo, size=200)
POST_batched("/api/sync/suspend", {"school_internal_ids": suspend}, size=500)
```

---

## 4. Lehrer-Gültigkeit (zur Kenntnis)

- Der Server legt die Klasse `Lehrerkollegium` beim **ersten** `/new` automatisch mit der
  **Lehrer-Gültigkeit** an: *heute + 5 Jahre, 31.07.* (statt Schuljahresende).
- **Achtung, geteilt & eingefroren:** Alle Lehrer teilen diese eine Klasse; ihr `validity_end`
  steht ab Erst-Anlage fest und erneuert sich **nicht** automatisch bei später
  hinzukommenden Lehrern. Das Plugin kann daran nichts ändern — es ist ein Server-/
  Dashboard-Thema (ein automatisches Auffrischen ist nicht implementiert).

---

## 5. Plugin-Checkliste

- [ ] **Filtert** das Manifest auf `class_name == "Lehrerkollegium"` (Lesen **und** Suspend-Menge).
- [ ] Setzt `class_name` beim Senden **immer** exakt auf die Konstante `TEACHER_CLASS`.
- [ ] Vergibt **stabile, kollisionsfreie** `school_internal_id` (Namespacing, z. B. `L-…`).
- [ ] `dob` als `YYYY-MM-DD`, `data_hash`/`photo_hash` bit-genau (sync-api.md §5).
- [ ] **Plausibilitäts-Riegel** gegen Massen-Suspend: 0 Lehrer aus SchILD ⇒ Suspend auslassen (nicht fragen); ungewöhnlich viele ⇒ **Bediener bestätigen lassen**, nicht hart abbrechen. Bei **unbeaufsichtigtem** Lauf (kein Mensch da): Suspend überspringen + laute Warnung loggen statt blind zu deaktivieren.
- [ ] Dedupliziert Batches; kennt die Reaktivierungs-Policy (sync-api.md §7).
- [ ] Nutzt den schulgebundenen `X-API-Key` (derselbe wie der Schüler-Sync ist ok;
      pro Schule gibt es genau einen Key).

---

## 6. Mögliche Server-Härtung (nicht umgesetzt)

Um den Magic-String ganz aus der Schnittstelle zu nehmen, könnte `/new` + `/change`
um ein explizites Feld erweitert werden:

```json
{ "school_internal_id": "L-4711", "first_name": "Erika", "last_name": "Muster",
  "dob": "1980-05-01", "is_teacher": true }
```

Der Server würde `is_teacher: true` in `class_name = "Lehrerkollegium"` übersetzen und das
Manifest um ein `is_teacher`-Flag ergänzen (dann müsste das Plugin nicht mehr auf den
Klassennamen filtern).
