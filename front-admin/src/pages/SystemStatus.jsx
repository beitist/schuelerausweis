/**
 * System-Status (Superadmin Only)
 *
 * Live-Betriebsübersicht: DB-Erreichbarkeit + Latenz, Uptime, Kennzahlen,
 * letzter nächtlicher Job-Lauf und Sicherheits-Anomalien der letzten 24h.
 *
 * Bewusst KEINE E-Mail-Alerts — die Anomalien stehen hier.
 * Ein echter „Server-tot"-Alarm braucht einen externen Pinger auf /health.
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

const ACTION_LABELS = {
    sync_ip_blocked: '🌐 Sync-IP blockiert',
    admin_login_failed: '🚫 Admin-Login fehlgeschlagen',
    student_login_failed: '🚫 Schüler-Login fehlgeschlagen',
    trace_enabled: '🎯 Trace aktiviert',
    trace_revoked: '🎯 Trace beendet',
}

const REFRESH_MS = 30000  // Auto-Refresh alle 30s

export default function SystemStatus({ user, onLogout, apiUrl }) {
    const [status, setStatus] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [lastFetch, setLastFetch] = useState(null)

    // Login-Rate-Limit live einstellbar
    const [rl, setRl] = useState(null)
    const [rlForm, setRlForm] = useState({ attempts: '', window_minutes: '' })
    const [rlSaving, setRlSaving] = useState(false)
    const [rlMsg, setRlMsg] = useState('')

    useEffect(() => {
        fetchStatus()
        fetchRl()
        const id = setInterval(fetchStatus, REFRESH_MS)
        return () => clearInterval(id)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const fetchStatus = async () => {
        try {
            const res = await fetch(`${apiUrl}/admin/system/status`, { credentials: 'include' })
            if (!res.ok) {
                if (res.status === 403) throw new Error('Nur Superadmins sehen den Systemstatus')
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Status konnte nicht geladen werden'))
            }
            setStatus(await res.json())
            setError('')
            setLastFetch(new Date())
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const fetchRl = async () => {
        try {
            const res = await fetch(`${apiUrl}/admin/settings/rate-limit`, { credentials: 'include' })
            if (!res.ok) return
            const d = await res.json()
            setRl(d)
            setRlForm({ attempts: String(d.attempts), window_minutes: String(d.window_minutes) })
        } catch { /* Rate-Limit-Karte ist optional — Fehler still ignorieren */ }
    }

    const saveRl = async () => {
        setRlSaving(true); setRlMsg('')
        try {
            const res = await fetch(`${apiUrl}/admin/settings/rate-limit`, {
                method: 'PUT',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    attempts: parseInt(rlForm.attempts, 10),
                    window_minutes: parseInt(rlForm.window_minutes, 10),
                }),
            })
            const d = await res.json().catch(() => ({}))
            if (!res.ok) throw new Error(apiError(d, 'Speichern fehlgeschlagen'))
            setRl(d)
            setRlForm({ attempts: String(d.attempts), window_minutes: String(d.window_minutes) })
            setRlMsg('Gespeichert · sofort aktiv')
        } catch (err) {
            setRlMsg(err.message)
        } finally {
            setRlSaving(false)
        }
    }

    const fmtUptime = (s) => {
        if (s == null) return '—'
        const d = Math.floor(s / 86400)
        const h = Math.floor((s % 86400) / 3600)
        const m = Math.floor((s % 3600) / 60)
        if (d > 0) return `${d}d ${h}h ${m}m`
        if (h > 0) return `${h}h ${m}m`
        return `${m}m`
    }

    const fmtTime = (iso) => {
        if (!iso) return '—'
        try {
            return new Date(iso).toLocaleString('de-DE', {
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            })
        } catch { return iso }
    }

    const sec = status?.security || {}
    const counts = status?.counts || {}

    // Ampel: rot bei DB-Problem, gelb bei frischen Anomalien, sonst grün
    const hasAnomalies = (sec.sync_ip_blocked_24h > 0)
    const dbBad = status && !status.db_ok
    const dot = dbBad ? '#ef4444' : (hasAnomalies ? '#f59e0b' : '#16a34a')
    const dotLabel = dbBad ? 'Problem' : (hasAnomalies ? 'Auffälligkeiten' : 'Alles ok')

    // Kleine Kennzahl-Karte
    const Stat = ({ label, value, warn }) => (
        <div className="card" style={{ padding: 'var(--space-md)', margin: 0 }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--color-text-light)' }}>{label}</div>
            <div style={{ fontSize: '1.6rem', fontWeight: 700, color: warn ? '#f59e0b' : 'inherit' }}>
                {value}
            </div>
        </div>
    )

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <div className="flex justify-between items-center">
                    <div>
                        <h1>System-Status</h1>
                        <p>
                            Live-Betriebsübersicht
                            {lastFetch && <> · zuletzt {lastFetch.toLocaleTimeString('de-DE')}</>}
                        </p>
                    </div>
                    <div className="flex gap-sm items-center">
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
                            <span style={{ width: 12, height: 12, borderRadius: '50%', background: dot }} />
                            {status ? dotLabel : '…'}
                        </span>
                        <button className="btn btn-secondary btn-sm" onClick={fetchStatus}>↻ Aktualisieren</button>
                    </div>
                </div>
            </div>

            {error && <div className="alert alert-error mb-md">{error}</div>}

            {loading && !status ? (
                <div className="text-center"><div className="spinner" style={{ margin: '2rem auto' }}></div></div>
            ) : status ? (
                <>
                    {/* System-Zeile */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)' }}>
                        <Stat label="Datenbank" value={status.db_ok ? `OK · ${status.db_latency_ms} ms` : 'FEHLER'} warn={!status.db_ok} />
                        <Stat label="Uptime" value={fmtUptime(status.uptime_seconds)} />
                        <Stat label="Modus" value={status.debug ? 'DEBUG' : 'Produktion'} warn={status.debug} />
                        <Stat label="Aktive Traces" value={sec.active_traces ?? 0} warn={sec.active_traces > 0} />
                    </div>

                    {/* Kennzahlen */}
                    <h3 style={{ marginBottom: 'var(--space-sm)' }}>Bestand</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)' }}>
                        <Stat label="Schulen" value={counts.schools ?? '—'} />
                        <Stat label="Schüler (aktiv)" value={`${counts.students_active ?? '—'} / ${counts.students_total ?? '—'}`} />
                        <Stat label="Klassen" value={counts.classes ?? '—'} />
                        <Stat label="Admin-Benutzer" value={counts.admin_users ?? '—'} />
                    </div>

                    {/* Sicherheit 24h + Sync */}
                    <h3 style={{ marginBottom: 'var(--space-sm)' }}>Sicherheit (letzte 24h)</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)' }}>
                        <Stat label="Admin-Logins fehlgeschl." value={sec.failed_admin_logins_24h ?? 0} warn={sec.failed_admin_logins_24h > 0} />
                        <Stat label="Schüler-Logins fehlgeschl." value={sec.failed_student_logins_24h ?? 0} warn={sec.failed_student_logins_24h > 0} />
                        <Stat label="Sync-IP blockiert" value={sec.sync_ip_blocked_24h ?? 0} warn={sec.sync_ip_blocked_24h > 0} />
                        <Stat label="Schulen mit Allowlist" value={`${sec.schools_with_allowlist ?? 0} / ${sec.schools_with_api_key ?? 0}`} />
                    </div>

                    {/* Login-Schutz (Rate-Limit) — live einstellbar */}
                    {rl && (
                        <div className="card mb-lg">
                            <h3 style={{ marginBottom: '0.5rem' }}>Login-Schutz (Rate-Limit)</h3>
                            <p style={{ fontSize: '0.9rem', color: 'var(--color-text-light)', marginTop: 0 }}>
                                Nach so vielen Fehlversuchen je Zeitfenster wird ein Konto/eine IP gesperrt (global).
                                {' '}Aktuell aktiv: <strong>{rl.is_db_override ? 'hier gesetzt' : 'Env/Default'}</strong>.
                            </p>
                            <div style={{ display: 'flex', gap: 'var(--space-md)', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                                <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.85rem' }}>
                                    Max. Fehlversuche
                                    <input type="number" min={rl.attempts_min} max={rl.attempts_max}
                                        value={rlForm.attempts}
                                        onChange={e => setRlForm(f => ({ ...f, attempts: e.target.value }))}
                                        style={{ width: 120 }} />
                                    <small style={{ color: 'var(--color-text-light)' }}>erlaubt {rl.attempts_min}–{rl.attempts_max}</small>
                                </label>
                                <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.85rem' }}>
                                    Zeitfenster (Minuten)
                                    <input type="number" min={rl.window_min} max={rl.window_max}
                                        value={rlForm.window_minutes}
                                        onChange={e => setRlForm(f => ({ ...f, window_minutes: e.target.value }))}
                                        style={{ width: 120 }} />
                                    <small style={{ color: 'var(--color-text-light)' }}>erlaubt {rl.window_min}–{rl.window_max}</small>
                                </label>
                                <button className="btn btn-primary" disabled={rlSaving} onClick={saveRl}>
                                    {rlSaving ? 'Speichern…' : 'Speichern'}
                                </button>
                                {rlMsg && <span style={{ fontSize: '0.85rem' }}>{rlMsg}</span>}
                            </div>
                        </div>
                    )}

                    {/* Nächtlicher Job */}
                    <div className="card mb-lg">
                        <h3 style={{ marginBottom: '0.5rem' }}>Nächtlicher Job (DSGVO-Cleanup + Retention)</h3>
                        {status.last_cleanup ? (
                            <div style={{ fontSize: '0.9rem' }}>
                                Letzter Lauf: <strong>{fmtTime(status.last_cleanup.at)}</strong>
                                {status.last_cleanup.details && (
                                    <div style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--color-text-light)', marginTop: '0.25rem' }}>
                                        {status.last_cleanup.details}
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p style={{ opacity: 0.7, fontSize: '0.9rem' }}>
                                Noch kein Lauf protokolliert (läuft täglich 3:00 Uhr — der erste Eintrag erscheint danach).
                            </p>
                        )}
                    </div>

                    {/* Jüngste Anomalien */}
                    <h3 style={{ marginBottom: 'var(--space-sm)' }}>Jüngste sicherheitsrelevante Ereignisse</h3>
                    <div className="card">
                        {status.recent_anomalies.length === 0 ? (
                            <p style={{ opacity: 0.7, padding: '1rem' }}>Keine Ereignisse.</p>
                        ) : (
                            <div className="table-container">
                                <table className="table">
                                    <thead>
                                        <tr>
                                            <th style={{ whiteSpace: 'nowrap' }}>Zeitpunkt</th>
                                            <th>Ereignis</th>
                                            <th>Schule</th>
                                            <th>Betroffene ID</th>
                                            <th>IP</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {status.recent_anomalies.map((a, i) => (
                                            <tr key={i}>
                                                <td style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{fmtTime(a.timestamp)}</td>
                                                <td>{ACTION_LABELS[a.action] || a.action}</td>
                                                <td>{a.school_id ?? '—'}</td>
                                                <td style={{ fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all' }}>{a.subject_id || '—'}</td>
                                                <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{a.ip || '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            ) : null}
        </Layout>
    )
}
