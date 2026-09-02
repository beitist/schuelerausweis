/**
 * Teachers Page
 *
 * Eigener Bereich für Lehrerkollegium / Dienstausweise.
 * Lehrer sind Student-Datensätze in der Klasse "Lehrerkollegium" und können
 * hier manuell oder über die Sync-API verwaltet werden.
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

function ExpiryBadge({ date }) {
    if (!date) return <span style={{ opacity: 0.5 }}>-</span>

    const expiryDate = new Date(date)
    const today = new Date()
    today.setHours(0, 0, 0, 0)

    const daysRemaining = Math.ceil((expiryDate - today) / (1000 * 60 * 60 * 24))
    const formatted = expiryDate.toLocaleDateString('de-DE')

    if (daysRemaining < 0) {
        return <span className="badge badge-error">{formatted}</span>
    } else if (daysRemaining <= 30) {
        return <span className="badge badge-warning" title={`Noch ${daysRemaining} Tage`}>{formatted}</span>
    } else {
        return <span className="badge badge-success">{formatted}</span>
    }
}

export default function Teachers({ user, onLogout, apiUrl }) {
    const [teachers, setTeachers] = useState([])
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState('')
    const [includeInactive, setIncludeInactive] = useState(false)
    const [teacherClassId, setTeacherClassId] = useState(null)

    // Lehrerkollegium class_id beim Start ermitteln
    useEffect(() => {
        fetchTeacherClassId()
    }, [])

    useEffect(() => {
        if (teacherClassId) fetchTeachers()
    }, [teacherClassId, search, includeInactive])

    const fetchTeacherClassId = async () => {
        try {
            const res = await fetch(`${apiUrl}/admin/classes?school_id=${user.school_id}`, {
                credentials: 'include'
            })
            if (!res.ok) throw new Error('Fehler beim Laden der Klassen')
            const classes = await res.json()
            const lk = classes.find(c => c.name === 'Lehrerkollegium')
            if (lk) {
                setTeacherClassId(lk.id)
            } else {
                setLoading(false)
            }
        } catch (err) {
            console.error(err)
            setLoading(false)
        }
    }

    const fetchTeachers = async () => {
        setLoading(true)
        try {
            let url = `${apiUrl}/admin/students?school_id=${user.school_id}&class_id=${teacherClassId}&exclude_teachers=false&limit=200`
            if (search) url += `&search=${encodeURIComponent(search)}`
            if (includeInactive) url += `&include_inactive=true`

            const res = await fetch(url, { credentials: 'include' })
            if (!res.ok) throw new Error('Fehler beim Laden')
            setTeachers(await res.json())
        } catch (err) {
            console.error(err)
        } finally {
            setLoading(false)
        }
    }

    // Aktionen
    const handleDeactivate = async (id, name) => {
        if (!confirm(`"${name}" wirklich deaktivieren?`)) return
        try {
            await fetch(`${apiUrl}/admin/students/${id}/deactivate`, {
                method: 'PATCH', credentials: 'include'
            })
            fetchTeachers()
        } catch { alert('Fehler beim Deaktivieren') }
    }

    const handleActivate = async (id) => {
        try {
            await fetch(`${apiUrl}/admin/students/${id}/activate`, {
                method: 'PATCH', credentials: 'include'
            })
            fetchTeachers()
        } catch { alert('Fehler beim Aktivieren') }
    }

    const handleDelete = async (id, name) => {
        if (!confirm(`"${name}" wirklich löschen?\n\nWird nach 4 Wochen endgültig entfernt.`)) return
        try {
            const res = await fetch(`${apiUrl}/admin/students/${id}`, {
                method: 'DELETE', credentials: 'include'
            })
            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler'))
            }
            alert('Zum Löschen vorgemerkt (wird in 4 Wochen entfernt)')
            fetchTeachers()
        } catch (err) { alert('Fehler: ' + err.message) }
    }

    // Detail-Modal
    const [showDetailModal, setShowDetailModal] = useState(false)
    const [selectedTeacher, setSelectedTeacher] = useState(null)
    const [teacherDetails, setTeacherDetails] = useState(null)
    const [loadingDetails, setLoadingDetails] = useState(false)
    const [photoFile, setPhotoFile] = useState(null)
    const [uploadingPhoto, setUploadingPhoto] = useState(false)

    // CSV-Export (Name, E-Mail, PIN) fürs Lehrerkollegium — Datenquelle für Serienmail.
    // fetch + Blob statt <a href>, weil der Download das HttpOnly-Auth-Cookie braucht.
    const [exporting, setExporting] = useState(false)
    const handleCsvExport = async () => {
        if (!teacherClassId) return
        setExporting(true)
        try {
            const res = await fetch(`${apiUrl}/admin/classes/${teacherClassId}/export.csv`, {
                credentials: 'include'
            })
            if (!res.ok) throw new Error('Export fehlgeschlagen')
            const blob = await res.blob()
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'pins-lehrerkollegium.csv'
            document.body.appendChild(a)
            a.click()
            a.remove()
            URL.revokeObjectURL(url)
        } catch (err) {
            alert('CSV-Export fehlgeschlagen: ' + err.message)
        } finally {
            setExporting(false)
        }
    }

    const handleShowDetails = async (teacher) => {
        setSelectedTeacher(teacher)
        setShowDetailModal(true)
        setLoadingDetails(true)
        setTeacherDetails(null)
        setPhotoFile(null)

        try {
            const res = await fetch(`${apiUrl}/admin/students/${teacher.id}`, {
                credentials: 'include'
            })
            if (res.ok) setTeacherDetails(await res.json())
        } catch (err) {
            console.error(err)
        } finally {
            setLoadingDetails(false)
        }
    }

    const handlePhotoUpload = async (e) => {
        e.preventDefault()
        if (!photoFile || !selectedTeacher) return

        setUploadingPhoto(true)
        try {
            const formData = new FormData()
            formData.append('file', photoFile)

            const res = await fetch(`${apiUrl}/admin/students/${selectedTeacher.id}/photo`, {
                method: 'POST', credentials: 'include', body: formData
            })
            if (!res.ok) throw new Error('Upload fehlgeschlagen')
            alert('Foto erfolgreich hochgeladen!')
            handleShowDetails(selectedTeacher)
        } catch (err) {
            alert('Fehler beim Upload: ' + err.message)
        } finally {
            setUploadingPhoto(false)
        }
    }

    // Create-Modal
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [createData, setCreateData] = useState({
        first_name: '',
        last_name: '',
        dob: '',
        school_internal_id: '',
        email: ''
    })
    const [creating, setCreating] = useState(false)

    const handleCreate = async (e) => {
        e.preventDefault()
        setCreating(true)
        try {
            const payload = {
                ...createData,
                is_teacher: true  // Backend setzt automatisch Klasse + Gültigkeit
            }
            const res = await fetch(`${apiUrl}/admin/students?school_id=${user.school_id}`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Erstellen'))
            }
            alert('Lehrer erfolgreich angelegt!')
            setShowCreateModal(false)
            setCreateData({ first_name: '', last_name: '', dob: '', school_internal_id: '', email: '' })
            // class_id könnte sich beim ersten Lehrer ändern (Klasse wird erstellt)
            if (!teacherClassId) await fetchTeacherClassId()
            else fetchTeachers()
        } catch (err) {
            alert('Fehler: ' + err.message)
        } finally {
            setCreating(false)
        }
    }

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header flex justify-between items-center">
                <div>
                    <h1>Kollegium</h1>
                    <p>Dienstausweise für Lehrkräfte verwalten</p>
                </div>
                <div className="flex gap-md items-center">
                    <button
                        className="btn btn-secondary"
                        onClick={handleCsvExport}
                        disabled={!teacherClassId || exporting}
                        title="Nachname, Vorname, E-Mail, PIN — als Datenquelle für Word/Outlook-Serienmail"
                    >
                        {exporting ? 'Exportiere…' : '⬇ CSV für Serienmail'}
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={() => setShowCreateModal(true)}
                    >
                        + Lehrer hinzufügen
                    </button>
                </div>
            </div>

            {/* Filter */}
            <div className="card mb-md">
                <div className="flex gap-md items-center">
                    <input
                        type="text"
                        className="form-input"
                        placeholder="Suche nach Name..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        style={{ maxWidth: '300px' }}
                    />
                    <label className="flex items-center gap-md">
                        <input
                            type="checkbox"
                            checked={includeInactive}
                            onChange={(e) => setIncludeInactive(e.target.checked)}
                        />
                        Inaktive anzeigen
                    </label>
                </div>
            </div>

            {/* Tabelle */}
            <div className="card">
                {loading ? (
                    <div className="text-center">
                        <div className="spinner" style={{ margin: '2rem auto' }}></div>
                    </div>
                ) : !teacherClassId && teachers.length === 0 ? (
                    <div className="text-center" style={{ padding: '3rem 1rem' }}>
                        <p style={{ fontSize: '2rem', marginBottom: '1rem' }}>👔</p>
                        <p>Noch keine Lehrkräfte angelegt.</p>
                        <p className="text-sm" style={{ opacity: 0.7, marginTop: '0.5rem' }}>
                            Klicke oben auf "Lehrer hinzufügen" um den ersten Dienstausweis zu erstellen.
                        </p>
                    </div>
                ) : (
                    <div className="table-container">
                        <table className="table">
                            <thead>
                                <tr>
                                    <th style={{ width: '50px' }}></th>
                                    <th>Name</th>
                                    <th>E-Mail</th>
                                    <th>Gültig bis</th>
                                    <th>Status</th>
                                    <th>Aktionen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {teachers.length === 0 ? (
                                    <tr>
                                        <td colSpan="6" className="text-center">
                                            Keine Lehrkräfte gefunden
                                        </td>
                                    </tr>
                                ) : (
                                    teachers.map((t) => (
                                        <tr key={t.id}>
                                            <td>
                                                <div style={{
                                                    width: '36px',
                                                    height: '36px',
                                                    borderRadius: '50%',
                                                    background: '#f0f0f0',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    justifyContent: 'center',
                                                    overflow: 'hidden',
                                                    border: '2px solid var(--color-primary-light)'
                                                }}>
                                                    <img
                                                        /* Stabile URL (KEIN Date.now()-Cache-Buster): der Server cached 1h
                                                           (Cache-Control) — ein Cache-Buster würde bei jedem Render ALLE
                                                           Fotos neu laden. loading="lazy": nur sichtbare Zeilen werden geholt. */
                                                        src={`${apiUrl}/admin/students/${t.id}/photo`}
                                                        alt=""
                                                        loading="lazy"
                                                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                                        onError={(e) => {
                                                            e.target.style.display = 'none'
                                                            if (e.target.parentNode) e.target.parentNode.textContent = '👤'
                                                        }}
                                                    />
                                                </div>
                                            </td>
                                            <td>
                                                <strong>{t.first_name} {t.last_name}</strong>
                                            </td>
                                            <td style={{ fontSize: '0.875rem' }}>
                                                {t.email || <span style={{ opacity: 0.4 }}>-</span>}
                                            </td>
                                            <td>
                                                <ExpiryBadge date={t.validity_end} />
                                            </td>
                                            <td>
                                                {t.is_active ? (
                                                    <span className="badge badge-success">Aktiv</span>
                                                ) : (
                                                    <span className="badge badge-error">Inaktiv</span>
                                                )}
                                            </td>
                                            <td>
                                                <div className="flex gap-sm">
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => handleShowDetails(t)}
                                                    >
                                                        Details
                                                    </button>
                                                    {t.is_active ? (
                                                        <button
                                                            className="btn btn-secondary btn-sm"
                                                            onClick={() => handleDeactivate(t.id, `${t.first_name} ${t.last_name}`)}
                                                        >
                                                            Deaktivieren
                                                        </button>
                                                    ) : (
                                                        <>
                                                            <button
                                                                className="btn btn-primary btn-sm"
                                                                onClick={() => handleActivate(t.id)}
                                                            >
                                                                Aktivieren
                                                            </button>
                                                            <button
                                                                className="btn btn-sm"
                                                                style={{ background: '#ef4444', color: 'white' }}
                                                                onClick={() => handleDelete(t.id, `${t.first_name} ${t.last_name}`)}
                                                            >
                                                                Löschen
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Create Teacher Modal */}
            {showCreateModal && (
                <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                        <h2>Lehrer hinzufügen</h2>
                        <p className="text-sm mb-md" style={{ opacity: 0.7 }}>
                            Gültigkeit wird automatisch bis zum Renteneintritt (67. Geburtstag + Schuljahresende) berechnet.
                        </p>
                        <form onSubmit={handleCreate}>
                            <div className="form-group">
                                <label className="form-label">Vorname</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={createData.first_name}
                                    onChange={(e) => setCreateData({ ...createData, first_name: e.target.value })}
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Nachname</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={createData.last_name}
                                    onChange={(e) => setCreateData({ ...createData, last_name: e.target.value })}
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Geburtsdatum</label>
                                <input
                                    type="date"
                                    className="form-input"
                                    value={createData.dob}
                                    onChange={(e) => setCreateData({ ...createData, dob: e.target.value })}
                                    required
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">
                                    E-Mail <span style={{ opacity: 0.6, fontWeight: 'normal' }}>(optional)</span>
                                </label>
                                <input
                                    type="email"
                                    className="form-input"
                                    value={createData.email}
                                    onChange={(e) => setCreateData({ ...createData, email: e.target.value })}
                                    placeholder="vorname.nachname@schule.de"
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label">
                                    Personal-Nr. <span style={{ opacity: 0.6, fontWeight: 'normal' }}>(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={createData.school_internal_id}
                                    onChange={(e) => setCreateData({ ...createData, school_internal_id: e.target.value })}
                                    placeholder="Wird automatisch generiert"
                                />
                            </div>

                            <div className="flex justify-end gap-sm mt-md">
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => setShowCreateModal(false)}
                                >
                                    Abbrechen
                                </button>
                                <button
                                    type="submit"
                                    className="btn btn-primary"
                                    disabled={creating}
                                >
                                    {creating ? 'Erstellen...' : 'Erstellen'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Teacher Details Modal */}
            {showDetailModal && (
                <div className="modal-overlay" onClick={() => setShowDetailModal(false)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '600px' }}>
                        <div className="flex justify-between items-start mb-md">
                            <h2>Lehrer-Details</h2>
                            <button
                                className="btn btn-sm btn-secondary"
                                onClick={() => setShowDetailModal(false)}
                            >
                                ✕
                            </button>
                        </div>

                        {loadingDetails ? (
                            <div className="text-center p-md">
                                <div className="spinner"></div>
                            </div>
                        ) : teacherDetails ? (
                            <div className="grid gap-md">
                                {/* Kopfdaten */}
                                <div className="flex gap-md items-start">
                                    <div style={{
                                        width: '100px',
                                        height: '130px',
                                        background: '#f0f0f0',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        borderRadius: '8px',
                                        overflow: 'hidden',
                                        border: '2px solid var(--color-primary-light)'
                                    }}>
                                        {teacherDetails.has_photo ? (
                                            <img
                                                src={`${apiUrl}/admin/students/${teacherDetails.id}/photo?t=${Date.now()}`}
                                                alt="Foto"
                                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                            />
                                        ) : (
                                            <span style={{ fontSize: '2rem' }}>👤</span>
                                        )}
                                    </div>
                                    <div>
                                        <h3>{teacherDetails.first_name} {teacherDetails.last_name}</h3>
                                        <div className="grid gap-sm mt-sm text-sm">
                                            <div><strong>Geburtsdatum:</strong> {teacherDetails.dob}</div>
                                            <div><strong>E-Mail:</strong> {teacherDetails.email || '-'}</div>
                                            <div><strong>Status:</strong> {teacherDetails.is_active ? 'Aktiv' : 'Inaktiv'}</div>
                                        </div>
                                    </div>
                                </div>

                                <hr style={{ margin: '1rem 0', border: 0, borderTop: '1px solid #eee' }} />

                                {/* Login-PIN */}
                                <div>
                                    <h4>Login-PIN</h4>
                                    <p className="text-sm opacity-70 mb-sm">
                                        PIN für den Zugang zur digitalen Ausweiskarte.
                                    </p>

                                    <div style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 'var(--space-md)',
                                        marginBottom: 'var(--space-md)'
                                    }}>
                                        <span style={{
                                            fontFamily: 'var(--font-mono)',
                                            fontSize: '1.75rem',
                                            fontWeight: 700,
                                            letterSpacing: '0.15em',
                                            background: '#f0f4ff',
                                            padding: '0.5rem 1rem',
                                            borderRadius: 'var(--radius-md)',
                                            border: '2px solid var(--color-primary-light)',
                                            color: 'var(--color-primary-dark)',
                                            minWidth: '160px',
                                            textAlign: 'center'
                                        }}>
                                            {teacherDetails.login_pin || '—'}
                                        </span>

                                        {teacherDetails.login_pin && (
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => {
                                                    navigator.clipboard.writeText(teacherDetails.login_pin)
                                                    alert('PIN kopiert!')
                                                }}
                                                title="PIN kopieren"
                                            >
                                                Kopieren
                                            </button>
                                        )}
                                    </div>

                                    <div className="flex gap-sm items-center" style={{ flexWrap: 'wrap' }}>
                                        <button
                                            className="btn btn-primary btn-sm"
                                            onClick={async () => {
                                                try {
                                                    const res = await fetch(
                                                        `${apiUrl}/admin/students/${teacherDetails.id}/pin`,
                                                        {
                                                            method: 'PATCH',
                                                            credentials: 'include',
                                                            headers: { 'Content-Type': 'application/json' },
                                                            body: JSON.stringify({})
                                                        }
                                                    )
                                                    if (!res.ok) throw new Error('Fehler')
                                                    const result = await res.json()
                                                    setTeacherDetails({ ...teacherDetails, login_pin: result.login_pin })
                                                } catch { alert('Fehler beim Generieren der PIN') }
                                            }}
                                        >
                                            Neue PIN generieren
                                        </button>
                                    </div>
                                </div>

                                <hr style={{ margin: '1rem 0', border: 0, borderTop: '1px solid #eee' }} />

                                {/* Foto Upload */}
                                <div>
                                    <h4>Foto hochladen / ändern</h4>
                                    <form onSubmit={handlePhotoUpload} className="flex gap-sm items-end">
                                        <input
                                            type="file"
                                            accept="image/*"
                                            onChange={(e) => setPhotoFile(e.target.files[0])}
                                            className="form-input"
                                        />
                                        <button
                                            type="submit"
                                            className="btn btn-primary"
                                            disabled={!photoFile || uploadingPhoto}
                                        >
                                            {uploadingPhoto ? 'Lädt...' : 'Hochladen'}
                                        </button>
                                    </form>
                                </div>
                            </div>
                        ) : (
                            <p className="text-error">Fehler beim Laden der Details.</p>
                        )}
                    </div>
                </div>
            )}
        </Layout>
    )
}
