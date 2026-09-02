/**
 * Audit-Log-Viewer (Superadmin Only)
 *
 * Zeigt das systemweite Ereignis-Protokoll mit Filtern:
 * - Aktion (Ausweis verifiziert, Schulbescheinigung geladen/verifiziert,
 *   fehlgeschlagene Logins auf allen Ebenen, Import, API-Keys, Sync …)
 * - Schule, betroffene ID (Schüler-UUID / E-Mail), Zeitraum
 *
 * Datenschutz: Das Log ist pseudonym (IDs statt Klarnamen). Ausweis-Loads
 * werden NICHT protokolliert; nur die zeitlich begrenzte "Trace"-Funktion
 * macht sie für einen Verdachtsfall gezielt sichtbar.
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

// Aktions-Slugs (Backend) -> deutsche Bezeichnung + Icon für die Anzeige.
// Slugs ohne Eintrag werden roh angezeigt (zukunftssicher).
const ACTION_META = {
    id_verified:          { label: 'Ausweis verifiziert',            icon: '✅' },
    id_card_loaded:       { label: 'Ausweis geladen (Trace)',        icon: '📲' },
    cert_downloaded:      { label: 'Schulbescheinigung geladen',     icon: '📄' },
    cert_verified:        { label: 'Schulbescheinigung verifiziert', icon: '🔎' },
    cert_verify_failed:   { label: 'Bescheinigung: unbek. Token',    icon: '⚠️' },
    admin_login_failed:   { label: 'Admin-Login fehlgeschlagen',     icon: '🚫' },
    student_login_failed: { label: 'Schüler-Login fehlgeschlagen',   icon: '🚫' },
    sync_ip_blocked:      { label: 'Sync-IP blockiert',             icon: '🌐' },
    bulk_import:          { label: 'CSV-Import',                     icon: '📁' },
    manual_delete:        { label: 'Manuell gelöscht',              icon: '🗑️' },
    api_key_generated:    { label: 'API-Key erstellt',              icon: '🔑' },
    api_key_revoked:      { label: 'API-Key widerrufen',            icon: '🔒' },
    sync_manifest_read:   { label: 'Sync: Manifest gelesen',        icon: '🔄' },
    sync_photo_upload:    { label: 'Sync: Foto-Upload',             icon: '🔄' },
    sync_student_upsert:  { label: 'Sync: Schüler-Upsert',          icon: '🔄' },
    sync_student_delete:  { label: 'Sync: Schüler-Löschung',        icon: '🔄' },
    trace_enabled:        { label: 'Trace aktiviert',              icon: '🎯' },
    trace_revoked:        { label: 'Trace beendet',                icon: '🎯' },
}

// Reihenfolge im Filter-Dropdown (die für den Superadmin wichtigsten oben)
const ACTION_ORDER = [
    'id_verified', 'cert_downloaded', 'cert_verified', 'cert_verify_failed',
    'admin_login_failed', 'student_login_failed', 'sync_ip_blocked',
    'id_card_loaded',
    'bulk_import', 'manual_delete',
    'api_key_generated', 'api_key_revoked',
    'trace_enabled', 'trace_revoked',
    'sync_manifest_read', 'sync_photo_upload', 'sync_student_upsert', 'sync_student_delete',
]

const PAGE_SIZE = 100

// Leere-Filter-Vorlage (auch für den Reset-Button)
const EMPTY_FILTERS = { action: '', school_id: '', subject_id: '', since: '', until: '' }

export default function AuditLogs({ user, onLogout, apiUrl }) {
    const [items, setItems] = useState([])
    const [total, setTotal] = useState(0)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [schools, setSchools] = useState([])

    // Bearbeitbare Filter (Formular) + aktiv angewandte Filter (für den Fetch)
    const [filters, setFilters] = useState(EMPTY_FILTERS)
    const [applied, setApplied] = useState(EMPTY_FILTERS)
    const [offset, setOffset] = useState(0)

    // --- Trace-Verwaltung ---
    const [showTrace, setShowTrace] = useState(false)
    const [traces, setTraces] = useState([])
    const [traceForm, setTraceForm] = useState({ school_id: '', subject_id: '', reason: '', hours: 48 })
    const [traceBusy, setTraceBusy] = useState(false)
    const [traceError, setTraceError] = useState('')

    // Schulen einmalig laden (für Dropdown + Namensauflösung)
    useEffect(() => {
        fetch(`${apiUrl}/schools`, { credentials: 'include' })
            .then((r) => (r.ok ? r.json() : []))
            .then(setSchools)
            .catch(() => {})
        fetchTraces()
    }, [])

    const fetchTraces = async () => {
        try {
            const res = await fetch(`${apiUrl}/admin/traces`, { credentials: 'include' })
            if (res.ok) setTraces(await res.json())
        } catch { /* nicht kritisch */ }
    }

    const createTrace = async (e) => {
        e.preventDefault()
        setTraceBusy(true)
        setTraceError('')
        try {
            if (!traceForm.school_id) throw new Error('Bitte eine Schule wählen')
            const res = await fetch(`${apiUrl}/admin/traces`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    school_id: Number(traceForm.school_id),
                    subject_id: traceForm.subject_id.trim() || null,
                    reason: traceForm.reason.trim() || null,
                    hours: Number(traceForm.hours) || 48,
                }),
            })
            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Trace konnte nicht aktiviert werden'))
            }
            setTraceForm({ school_id: '', subject_id: '', reason: '', hours: 48 })
            fetchTraces()
        } catch (err) {
            setTraceError(err.message)
        } finally {
            setTraceBusy(false)
        }
    }

    const revokeTrace = async (t) => {
        const scope = t.subject_id ? `ID ${t.subject_id}` : 'die ganze Schule'
        if (!confirm(`Trace für ${scope} jetzt beenden?`)) return
        try {
            const res = await fetch(`${apiUrl}/admin/traces/${t.id}`, {
                method: 'DELETE', credentials: 'include'
            })
            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Trace konnte nicht beendet werden'))
            }
            fetchTraces()
        } catch (err) {
            alert(err.message)
        }
    }

    // Bei jeder Änderung von angewandten Filtern oder Offset neu laden
    useEffect(() => {
        fetchLogs()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [applied, offset])

    const fetchLogs = async () => {
        setLoading(true)
        setError('')
        try {
            const params = new URLSearchParams()
            if (applied.action) params.set('action', applied.action)
            if (applied.school_id) params.set('school_id', applied.school_id)
            if (applied.subject_id) params.set('subject_id', applied.subject_id.trim())
            if (applied.since) params.set('since', applied.since)
            if (applied.until) params.set('until', applied.until)
            params.set('limit', String(PAGE_SIZE))
            params.set('offset', String(offset))

            const res = await fetch(`${apiUrl}/admin/audit-logs?${params.toString()}`, {
                credentials: 'include'
            })
            if (!res.ok) {
                if (res.status === 403) throw new Error('Nur Superadmins können das Audit-Log einsehen')
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Fehler beim Laden der Logs'))
            }
            const data = await res.json()
            setItems(data.items || [])
            setTotal(data.total || 0)
        } catch (err) {
            setError(err.message)
            setItems([])
            setTotal(0)
        } finally {
            setLoading(false)
        }
    }

    const handleApply = (e) => {
        e.preventDefault()
        setOffset(0)
        setApplied(filters)
    }

    const handleReset = () => {
        setFilters(EMPTY_FILTERS)
        setOffset(0)
        setApplied(EMPTY_FILTERS)
    }

    // --- Anzeige-Helfer ---
    const schoolName = (id) => {
        if (id == null) return '—'
        const s = schools.find((s) => s.id === id)
        return s ? s.name : `#${id}`
    }

    const actionLabel = (slug) => {
        const m = ACTION_META[slug]
        return m ? `${m.icon} ${m.label}` : slug
    }

    // JSON-Details in lesbare "key: value" Paare zerlegen
    const renderDetails = (raw) => {
        if (!raw) return null
        let obj
        try { obj = JSON.parse(raw) } catch { return raw }
        return Object.entries(obj)
            .map(([k, v]) => `${k}: ${v}`)
            .join('  ·  ')
    }

    const fmtTime = (iso) => {
        try {
            return new Date(iso).toLocaleString('de-DE', {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            })
        } catch { return iso }
    }

    const page = Math.floor(offset / PAGE_SIZE) + 1
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
    const activeTraceCount = traces.filter((t) => t.active).length

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <div className="flex justify-between items-center">
                    <div>
                        <h1>Audit-Log</h1>
                        <p>Systemweites Ereignis-Protokoll (pseudonym, 180 Tage Aufbewahrung)</p>
                    </div>
                    <button className="btn btn-secondary" onClick={() => setShowTrace((v) => !v)}>
                        🎯 Trace-Verwaltung {activeTraceCount > 0 ? `(${activeTraceCount} aktiv)` : ''}
                    </button>
                </div>
            </div>

            {/* Trace-Verwaltung: gezielte, zeitlich begrenzte Detail-Nachverfolgung */}
            {showTrace && (
                <div className="card mb-md" style={{ borderLeft: '4px solid #f59e0b' }}>
                    <h2 style={{ marginBottom: 'var(--space-sm)' }}>Detail-Nachverfolgung (Trace)</h2>
                    <p style={{ fontSize: '0.85rem', color: 'var(--color-text-light)', marginBottom: 'var(--space-md)' }}>
                        Im Normalbetrieb loggen wir sparsam. Für einen Verdachtsfall kannst du hier
                        pro Schule (optional auf eine ID eingegrenzt) zusätzlich <strong>Ausweis-Loads
                        und IP-Adressen</strong> mitschreiben lassen. Der Trace schaltet sich nach Ablauf
                        automatisch ab.
                    </p>

                    <form onSubmit={createTrace}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--space-md)' }}>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                                <label className="form-label">Schule *</label>
                                <select
                                    className="form-input"
                                    value={traceForm.school_id}
                                    onChange={(e) => setTraceForm({ ...traceForm, school_id: e.target.value })}
                                    required
                                >
                                    <option value="">— Schule wählen —</option>
                                    {schools.map((s) => (
                                        <option key={s.id} value={String(s.id)}>{s.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                                <label className="form-label">Betroffene ID (optional)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    placeholder="leer = ganze Schule"
                                    value={traceForm.subject_id}
                                    onChange={(e) => setTraceForm({ ...traceForm, subject_id: e.target.value })}
                                />
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                                <label className="form-label">Dauer (Stunden)</label>
                                <input
                                    type="number"
                                    className="form-input"
                                    min="1"
                                    max="168"
                                    value={traceForm.hours}
                                    onChange={(e) => setTraceForm({ ...traceForm, hours: e.target.value })}
                                />
                            </div>
                            <div className="form-group" style={{ marginBottom: 0 }}>
                                <label className="form-label">Grund (optional)</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    placeholder="z.B. Missbrauchsverdacht"
                                    value={traceForm.reason}
                                    onChange={(e) => setTraceForm({ ...traceForm, reason: e.target.value })}
                                />
                            </div>
                        </div>

                        {traceError && <div className="alert alert-error" style={{ marginTop: 'var(--space-md)' }}>{traceError}</div>}

                        <div style={{ marginTop: 'var(--space-md)' }}>
                            <button type="submit" className="btn btn-primary" disabled={traceBusy}>
                                {traceBusy ? 'Aktiviere…' : 'Trace aktivieren'}
                            </button>
                        </div>
                    </form>

                    {/* Bestehende Traces */}
                    {traces.length > 0 && (
                        <div className="table-container" style={{ marginTop: 'var(--space-lg)' }}>
                            <table className="table">
                                <thead>
                                    <tr>
                                        <th>Status</th>
                                        <th>Schule</th>
                                        <th>Betroffene ID</th>
                                        <th>Grund</th>
                                        <th>Läuft ab</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {traces.map((t) => (
                                        <tr key={t.id}>
                                            <td>
                                                {t.active
                                                    ? <span style={{ color: '#f59e0b', fontWeight: 600 }}>● aktiv</span>
                                                    : <span style={{ opacity: 0.5 }}>○ beendet</span>}
                                            </td>
                                            <td>{t.school_name || `#${t.school_id}`}</td>
                                            <td style={{ fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all' }}>
                                                {t.subject_id || '— ganze Schule —'}
                                            </td>
                                            <td style={{ fontSize: '0.85rem' }}>{t.reason || '—'}</td>
                                            <td style={{ whiteSpace: 'nowrap', fontSize: '0.85rem' }}>{fmtTime(t.expires_at)}</td>
                                            <td>
                                                {t.active && (
                                                    <button className="btn btn-danger btn-sm" onClick={() => revokeTrace(t)}>
                                                        Beenden
                                                    </button>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Filterleiste */}
            <div className="card mb-md">
                <form onSubmit={handleApply}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--space-md)' }}>
                        <div className="form-group" style={{ marginBottom: 0 }}>
                            <label className="form-label">Aktion</label>
                            <select
                                className="form-input"
                                value={filters.action}
                                onChange={(e) => setFilters({ ...filters, action: e.target.value })}
                            >
                                <option value="">— Alle —</option>
                                {ACTION_ORDER.map((slug) => (
                                    <option key={slug} value={slug}>{ACTION_META[slug].label}</option>
                                ))}
                            </select>
                        </div>

                        <div className="form-group" style={{ marginBottom: 0 }}>
                            <label className="form-label">Schule</label>
                            <select
                                className="form-input"
                                value={filters.school_id}
                                onChange={(e) => setFilters({ ...filters, school_id: e.target.value })}
                            >
                                <option value="">— Alle —</option>
                                {schools.map((s) => (
                                    <option key={s.id} value={String(s.id)}>{s.name}</option>
                                ))}
                            </select>
                        </div>

                        <div className="form-group" style={{ marginBottom: 0 }}>
                            <label className="form-label">Betroffene ID</label>
                            <input
                                type="text"
                                className="form-input"
                                placeholder="Schüler-UUID / E-Mail"
                                value={filters.subject_id}
                                onChange={(e) => setFilters({ ...filters, subject_id: e.target.value })}
                            />
                        </div>

                        <div className="form-group" style={{ marginBottom: 0 }}>
                            <label className="form-label">Von</label>
                            <input
                                type="datetime-local"
                                className="form-input"
                                value={filters.since}
                                onChange={(e) => setFilters({ ...filters, since: e.target.value })}
                            />
                        </div>

                        <div className="form-group" style={{ marginBottom: 0 }}>
                            <label className="form-label">Bis</label>
                            <input
                                type="datetime-local"
                                className="form-input"
                                value={filters.until}
                                onChange={(e) => setFilters({ ...filters, until: e.target.value })}
                            />
                        </div>
                    </div>

                    <div className="flex gap-sm" style={{ marginTop: 'var(--space-md)' }}>
                        <button type="submit" className="btn btn-primary">Filtern</button>
                        <button type="button" className="btn btn-secondary" onClick={handleReset}>Zurücksetzen</button>
                        <button type="button" className="btn btn-secondary" onClick={fetchLogs}>↻ Aktualisieren</button>
                    </div>
                </form>
            </div>

            {error && <div className="alert alert-error mb-md">{error}</div>}

            {/* Ergebnisliste */}
            <div className="card">
                <div className="flex justify-between items-center mb-md">
                    <div style={{ fontSize: '0.875rem', color: 'var(--color-text-light)' }}>
                        {loading ? 'Lade…' : `${total} Ereignisse`}
                    </div>
                    {total > PAGE_SIZE && (
                        <div className="flex gap-sm items-center">
                            <button
                                className="btn btn-secondary btn-sm"
                                disabled={offset === 0 || loading}
                                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                            >
                                ← Zurück
                            </button>
                            <span style={{ fontSize: '0.875rem' }}>Seite {page} / {pageCount}</span>
                            <button
                                className="btn btn-secondary btn-sm"
                                disabled={offset + PAGE_SIZE >= total || loading}
                                onClick={() => setOffset(offset + PAGE_SIZE)}
                            >
                                Weiter →
                            </button>
                        </div>
                    )}
                </div>

                {loading ? (
                    <div className="text-center"><div className="spinner" style={{ margin: '2rem auto' }}></div></div>
                ) : items.length === 0 ? (
                    <div className="text-center" style={{ padding: '3rem' }}>
                        <p style={{ opacity: 0.7 }}>Keine Ereignisse für diese Filter</p>
                    </div>
                ) : (
                    <div className="table-container">
                        <table className="table">
                            <thead>
                                <tr>
                                    <th style={{ whiteSpace: 'nowrap' }}>Zeitpunkt</th>
                                    <th>Aktion</th>
                                    <th>Schule</th>
                                    <th>Betroffene ID</th>
                                    <th>IP</th>
                                    <th>Details</th>
                                </tr>
                            </thead>
                            <tbody>
                                {items.map((it) => (
                                    <tr key={it.id}>
                                        <td style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                                            {fmtTime(it.timestamp)}
                                        </td>
                                        <td>{actionLabel(it.action)}</td>
                                        <td>{schoolName(it.school_id)}</td>
                                        <td style={{ fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all' }}>
                                            {it.subject_id || '—'}
                                        </td>
                                        <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                                            {it.ip_address || '—'}
                                        </td>
                                        <td style={{ fontSize: '0.8rem', color: 'var(--color-text-light)' }}>
                                            {renderDetails(it.details) || '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </Layout>
    )
}
