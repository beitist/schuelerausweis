/**
 * Normalisiert einen FastAPI-Fehler-Response zu einem lesbaren String.
 *
 * WARUM: FastAPI/Pydantic liefert `detail` je nach Fehler unterschiedlich:
 *   - 4xx aus HTTPException  → detail: string            ("E-Mail bereits registriert")
 *   - 422 Validierungsfehler → detail: [{loc, msg, ...}] (Array von Objekten!)
 * Wird so ein Array/Objekt direkt in den React-State gesetzt und gerendert,
 * crasht React (#31: "Objects are not valid as a React child").
 * Diese Funktion gibt IMMER einen String zurück.
 */
export function apiError(data, fallback = 'Ein Fehler ist aufgetreten') {
    const d = data?.detail
    if (d == null) return fallback

    if (typeof d === 'string') return d

    if (Array.isArray(d)) {
        const msgs = d.map((e) => {
            if (typeof e === 'string') return e
            const field = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : null
            const msg = e?.msg || 'ungültig'
            return field ? `${field}: ${msg}` : msg
        })
        return msgs.join('; ') || fallback
    }

    if (typeof d === 'object') return d.msg || JSON.stringify(d)

    return String(d)
}
