/**
 * Students Page
 * 
 * Schülerliste mit Suche und Aktionen.
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

/**
 * ExpiryBadge - Zeigt Gültigkeitsstatus farblich an
 * 
 * Farben:
 * - Rot: Abgelaufen
 * - Gelb: Läuft in 30 Tagen ab
 * - Grün: Gültig
 */
function ExpiryBadge({ date }) {
    if (!date) return <span style={{ opacity: 0.5 }}>-</span>

    const expiryDate = new Date(date)
    const today = new Date()
    today.setHours(0, 0, 0, 0)

    const daysRemaining = Math.ceil((expiryDate - today) / (1000 * 60 * 60 * 24))

    // Formatieren: TT.MM.JJJJ
    const formatted = expiryDate.toLocaleDateString('de-DE')

    if (daysRemaining < 0) {
        // Abgelaufen
        return <span className="badge badge-error">{formatted}</span>
    } else if (daysRemaining <= 30) {
        // Bald ablaufend
        return (
            <span className="badge badge-warning" title={`Noch ${daysRemaining} Tage`}>
                {formatted}
            </span>
        )
    } else {
        // Gültig
        return <span className="badge badge-success">{formatted}</span>
    }
}

export default function Students({ user, onLogout, apiUrl, token }) {
    const [students, setStudents] = useState([])
    const [loading, setLoading] = useState(true)
    const [search, setSearch] = useState('')
    const [includeInactive, setIncludeInactive] = useState(false)

    useEffect(() => {
        fetchStudents()
    }, [search, includeInactive])

    const fetchStudents = async () => {
        setLoading(true)
        try {
            let url = `${apiUrl}/admin/students?school_id=${user.school_id}&limit=100&exclude_teachers=true`
            if (search) url += `&search=${encodeURIComponent(search)}`
            if (includeInactive) url += `&include_inactive=true`

            const res = await fetch(url, {
                credentials: 'include'
            })

            if (!res.ok) throw new Error('Fehler beim Laden')

            const data = await res.json()
            setStudents(data)
        } catch (err) {
            console.error(err)
        } finally {
            setLoading(false)
        }
    }

    const handleDeactivate = async (studentId, studentName) => {
        if (!confirm(`"${studentName}" wirklich deaktivieren?`)) return

        try {
            await fetch(`${apiUrl}/admin/students/${studentId}/deactivate`, {
                method: 'PATCH',
                credentials: 'include'
            })
            fetchStudents()
        } catch (err) {
            alert('Fehler beim Deaktivieren')
        }
    }

    const handleActivate = async (studentId) => {
        try {
            await fetch(`${apiUrl}/admin/students/${studentId}/activate`, {
                method: 'PATCH',
                credentials: 'include'
            })
            fetchStudents()
        } catch (err) {
            alert('Fehler beim Aktivieren')
        }
    }

    const handleDelete = async (studentId, studentName) => {
        if (!confirm(`"${studentName}" wirklich löschen?\n\nDie Person wird zum Löschen vorgemerkt und nach 4 Wochen endgültig entfernt.`)) return

        try {
            const res = await fetch(`${apiUrl}/admin/students/${studentId}`, {
                method: 'DELETE',
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Löschen'))
            }

            alert('Zum Löschen vorgemerkt (wird in 4 Wochen entfernt)')
            fetchStudents()
        } catch (err) {
            alert('Fehler: ' + err.message)
        }
    }

    // Modal State
    const [showStudentModal, setShowStudentModal] = useState(false)
    const [selectedStudent, setSelectedStudent] = useState(null)
    const [studentDetails, setStudentDetails] = useState(null)
    const [loadingDetails, setLoadingDetails] = useState(false)
    const [photoFile, setPhotoFile] = useState(null)
    const [uploadingPhoto, setUploadingPhoto] = useState(false)

    // Details laden
    const handleShowDetails = async (student) => {
        setSelectedStudent(student)
        setShowStudentModal(true)
        setLoadingDetails(true)
        setStudentDetails(null)
        setPhotoFile(null)

        try {
            const res = await fetch(`${apiUrl}/admin/students/${student.id}`, {
                credentials: 'include'
            })
            if (res.ok) {
                const data = await res.json()
                setStudentDetails(data)
            }
        } catch (err) {
            console.error(err)
        } finally {
            setLoadingDetails(false)
        }
    }

    const handlePhotoUpload = async (e) => {
        e.preventDefault()
        if (!photoFile || !selectedStudent) return

        setUploadingPhoto(true)
        try {
            const formData = new FormData()
            formData.append('file', photoFile)

            const res = await fetch(`${apiUrl}/admin/students/${selectedStudent.id}/photo`, {
                method: 'POST',
                credentials: 'include',
                body: formData
            })

            if (!res.ok) throw new Error('Upload fehlgeschlagen')

            alert('Foto erfolgreich hochgeladen!')
            // Details neu laden um Foto-Status zu aktualisieren
            handleShowDetails(selectedStudent)
        } catch (err) {
            alert('Fehler beim Upload: ' + err.message)
        } finally {
            setUploadingPhoto(false)
        }
    }


    // Create Modal State
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [createData, setCreateData] = useState({
        first_name: '',
        last_name: '',
        dob: '',
        class_name: '',
        school_internal_id: ''  // Schülernummer (optional, wird sonst auto-generiert)
    })
    const [creating, setCreating] = useState(false)

    const handleCreateUser = async (e) => {
        e.preventDefault()
        setCreating(true)

        try {
            const res = await fetch(`${apiUrl}/admin/students?school_id=${user.school_id}`, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(createData)
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Erstellen'))
            }

            alert('Benutzer erfolgreich erstellt!')
            setShowCreateModal(false)
            setCreateData({
                first_name: '',
                last_name: '',
                dob: '',
                class_name: '',
                school_internal_id: ''
            })
            fetchStudents() // Liste aktualisieren
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
                    <h1>Schülerverwaltung</h1>
                    <p>Schüler suchen, anzeigen und verwalten</p>
                </div>
                <button
                    className="btn btn-primary"
                    onClick={() => setShowCreateModal(true)}
                >
                    + Person hinzufügen
                </button>
            </div>

            {/* Filter */}
            <div className="card mb-md">
                <div className="flex gap-md items-center">
                    <input
                        type="text"
                        className="form-input"
                        placeholder="Suche nach Name oder ID..."
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
                ) : (
                    <div className="table-container">
                        <table className="table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Name</th>
                                    <th>Klasse</th>
                                    <th>Gültig bis</th>
                                    <th>Status</th>
                                    <th>Aktionen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {students.length === 0 ? (
                                    <tr>
                                        <td colSpan="6" className="text-center">
                                            Keine Schüler gefunden
                                        </td>
                                    </tr>
                                ) : (
                                    students.map((s) => (
                                        <tr key={s.id}>
                                            <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>
                                                {s.school_internal_id}
                                            </td>
                                            <td>
                                                <strong>{s.first_name} {s.last_name}</strong>
                                            </td>
                                            <td>{s.class_name || '-'}</td>
                                            <td>
                                                <ExpiryBadge date={s.validity_end} />
                                            </td>
                                            <td>
                                                {s.is_active ? (
                                                    <span className="badge badge-success">Aktiv</span>
                                                ) : (
                                                    <span className="badge badge-error">Inaktiv</span>
                                                )}
                                            </td>
                                            <td>
                                                <div className="flex gap-sm">
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => handleShowDetails(s)}
                                                    >
                                                        Details
                                                    </button>
                                                    {s.is_active ? (
                                                        <button
                                                            className="btn btn-secondary btn-sm"
                                                            onClick={() => handleDeactivate(s.id, `${s.first_name} ${s.last_name}`)}
                                                        >
                                                            Deaktivieren
                                                        </button>
                                                    ) : (
                                                        <>
                                                            <button
                                                                className="btn btn-primary btn-sm"
                                                                onClick={() => handleActivate(s.id)}
                                                            >
                                                                Aktivieren
                                                            </button>
                                                            <button
                                                                className="btn btn-sm"
                                                                style={{ background: '#ef4444', color: 'white' }}
                                                                onClick={() => handleDelete(s.id, `${s.first_name} ${s.last_name}`)}
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

            {/* Create Student/Teacher Modal */}
            {showCreateModal && (
                <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                        <h2>Person hinzufügen</h2>
                        <form onSubmit={handleCreateUser}>
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
                                    Schülernummer / ID <span style={{ opacity: 0.6, fontWeight: 'normal' }}>(optional)</span>
                                </label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={createData.school_internal_id}
                                    onChange={(e) => setCreateData({ ...createData, school_internal_id: e.target.value })}
                                    placeholder="z.B. 123456"
                                />
                                <small className="text-secondary">
                                    Wird automatisch generiert, wenn leer gelassen.
                                </small>
                            </div>

                            <div className="form-group">
                                <label className="form-label">Klasse</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={createData.class_name}
                                    onChange={(e) => setCreateData({ ...createData, class_name: e.target.value })}
                                    placeholder="z.B. 10A"
                                    required
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

            {/* Student Details Modal (Existing) */}
            {showStudentModal && (
                <div className="modal-overlay" onClick={() => setShowStudentModal(false)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '600px' }}>
                        <div className="flex justify-between items-start mb-md">
                            <h2>Schüler-Details</h2>
                            <button
                                className="btn btn-sm btn-secondary"
                                onClick={() => setShowStudentModal(false)}
                            >
                                ✕
                            </button>
                        </div>

                        {loadingDetails ? (
                            <div className="text-center p-md">
                                <div className="spinner"></div>
                            </div>
                        ) : studentDetails ? (
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
                                        border: '1px solid #ddd'
                                    }}>
                                        {studentDetails.has_photo ? (
                                            <img
                                                src={`${apiUrl}/admin/students/${studentDetails.id}/photo?t=${Date.now()}`}
                                                alt="Passfoto"
                                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                            />
                                        ) : (
                                            <span style={{ fontSize: '2rem' }}>👤</span>
                                        )}
                                    </div>
                                    <div>
                                        <h3>{studentDetails.first_name} {studentDetails.last_name}</h3>
                                        <p className="text-sm opacity-70">ID: {studentDetails.school_internal_id}</p>
                                        <div className="grid grid-cols-2 gap-sm mt-sm text-sm">
                                            <div><strong>Klasse:</strong> {studentDetails.class_name || '-'}</div>
                                            <div><strong>Geburtsdatum:</strong> {studentDetails.dob}</div>
                                            <div><strong>Status:</strong> {studentDetails.is_active ? 'Aktiv' : 'Inaktiv'}</div>
                                        </div>
                                    </div>
                                </div>

                                <hr style={{ margin: '1rem 0', border: 0, borderTop: '1px solid #eee' }} />

                                {/* Login-PIN Sektion */}
                                <div>
                                    <h4>🔑 Login-PIN</h4>
                                    <p className="text-sm opacity-70 mb-sm">
                                        Diese PIN wird auf dem gedruckten QR-Code verwendet.
                                    </p>

                                    {/* PIN-Anzeige – groß, monospace, gut lesbar */}
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
                                            {studentDetails.login_pin || '—'}
                                        </span>

                                        {/* Kopieren */}
                                        {studentDetails.login_pin && (
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => {
                                                    navigator.clipboard.writeText(studentDetails.login_pin)
                                                    alert('PIN kopiert!')
                                                }}
                                                title="PIN in Zwischenablage kopieren"
                                            >
                                                📋 Kopieren
                                            </button>
                                        )}
                                    </div>

                                    {/* PIN-Aktionen: Neu generieren + Manuell setzen */}
                                    <div className="flex gap-sm items-center" style={{ flexWrap: 'wrap' }}>
                                        <button
                                            className="btn btn-primary btn-sm"
                                            onClick={async () => {
                                                // Neue zufällige PIN generieren
                                                try {
                                                    const res = await fetch(
                                                        `${apiUrl}/admin/students/${studentDetails.id}/pin`,
                                                        {
                                                            method: 'PATCH',
                                                            credentials: 'include',
                                                            headers: { 'Content-Type': 'application/json' },
                                                            body: JSON.stringify({})
                                                        }
                                                    )
                                                    if (!res.ok) throw new Error('Fehler')
                                                    const result = await res.json()
                                                    // Details aktualisieren
                                                    setStudentDetails({ ...studentDetails, login_pin: result.login_pin })
                                                } catch (err) {
                                                    alert('Fehler beim Generieren der PIN')
                                                }
                                            }}
                                        >
                                            🔄 Neue PIN generieren
                                        </button>

                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => {
                                                const newPin = prompt(
                                                    'Neue PIN eingeben (6 Ziffern, keine führende 0):',
                                                    studentDetails.login_pin || ''
                                                )
                                                if (!newPin) return
                                                // Validierung
                                                if (!/^[1-9]\d{5}$/.test(newPin)) {
                                                    alert('PIN muss genau 6 Ziffern haben und darf nicht mit 0 beginnen.')
                                                    return
                                                }
                                                // An API senden
                                                fetch(`${apiUrl}/admin/students/${studentDetails.id}/pin`, {
                                                    method: 'PATCH',
                                                    credentials: 'include',
                                                    headers: { 'Content-Type': 'application/json' },
                                                    body: JSON.stringify({ pin: newPin })
                                                })
                                                    .then(res => {
                                                        if (!res.ok) throw new Error('Fehler')
                                                        return res.json()
                                                    })
                                                    .then(result => {
                                                        setStudentDetails({ ...studentDetails, login_pin: result.login_pin })
                                                    })
                                                    .catch(() => alert('Fehler beim Setzen der PIN'))
                                            }}
                                        >
                                            ✏️ PIN manuell setzen
                                        </button>
                                    </div>
                                </div>

                                <hr style={{ margin: '1rem 0', border: 0, borderTop: '1px solid #eee' }} />

                                {/* Foto Upload */}
                                <div>
                                    <h4>Foto ändern / hochladen</h4>
                                    <p className="text-sm opacity-70 mb-sm">
                                        Hier kann das Foto manuell aktualisiert werden (z.B. bei Fehlern im Import).
                                    </p>
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
