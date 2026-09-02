/**
 * Import Page
 * 
 * CSV-Upload mit Drag & Drop und Vorschau.
 */

import { useState, useRef } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

export default function Import({ user, onLogout, apiUrl, token }) {
    const [file, setFile] = useState(null)
    const [preview, setPreview] = useState(null)
    const [loading, setLoading] = useState(false)
    const [importing, setImporting] = useState(false)
    const [result, setResult] = useState(null)
    const [error, setError] = useState('')
    const [dragover, setDragover] = useState(false)

    const fileInputRef = useRef()

    // Datei auswählen
    const handleFileSelect = (e) => {
        const selectedFile = e.target.files[0]
        if (selectedFile && selectedFile.name.endsWith('.csv')) {
            setFile(selectedFile)
            setPreview(null)
            setResult(null)
            setError('')
        } else {
            setError('Bitte nur CSV-Dateien hochladen')
        }
    }

    // Drag & Drop Handler
    const handleDrop = (e) => {
        e.preventDefault()
        setDragover(false)
        const droppedFile = e.dataTransfer.files[0]
        if (droppedFile && droppedFile.name.endsWith('.csv')) {
            setFile(droppedFile)
            setPreview(null)
            setResult(null)
            setError('')
        } else {
            setError('Bitte nur CSV-Dateien hochladen')
        }
    }

    // Vorschau laden
    const handlePreview = async () => {
        if (!file) return

        setLoading(true)
        setError('')

        try {
            const formData = new FormData()
            formData.append('file', file)

            const res = await fetch(`${apiUrl}/admin/import/preview?school_id=${user.school_id}`, {
                method: 'POST',
                credentials: 'include',
                body: formData
            })

            const data = await res.json()

            if (!res.ok) {
                setError(apiError(data, 'Fehler beim Parsen der CSV'))
                return
            }

            setPreview(data)

        } catch (err) {
            setError('Verbindung zum Server fehlgeschlagen')
        } finally {
            setLoading(false)
        }
    }

    // Import durchführen
    const handleImport = async () => {
        if (!file || !preview) return

        if (!confirm(`Wirklich ${preview.inserts} neue Schüler anlegen und ${preview.deletes} als gelöscht markieren?`)) {
            return
        }

        setImporting(true)
        setError('')

        try {
            const formData = new FormData()
            formData.append('file', file)

            const res = await fetch(`${apiUrl}/admin/import/apply?school_id=${user.school_id}`, {
                method: 'POST',
                credentials: 'include',
                body: formData
            })

            const data = await res.json()

            if (!res.ok) {
                setError(apiError(data, 'Import fehlgeschlagen'))
                return
            }

            setResult(data)
            setPreview(null)
            setFile(null)

        } catch (err) {
            setError('Verbindung zum Server fehlgeschlagen')
        } finally {
            setImporting(false)
        }
    }

    // Reset
    const handleReset = () => {
        setFile(null)
        setPreview(null)
        setResult(null)
        setError('')
        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
    }

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <h1>CSV-Import</h1>
                <p>Schülerdaten aus Schild importieren</p>
            </div>

            {error && <div className="login-error mb-md">{error}</div>}

            {result && (
                <div className="card mb-md" style={{ background: 'rgba(52, 168, 83, 0.1)', borderColor: 'var(--color-success)' }}>
                    <h3 style={{ color: 'var(--color-success)', marginBottom: 'var(--space-sm)' }}>
                        ✓ Import erfolgreich!
                    </h3>
                    <p>{result.message}</p>
                    <p className="mt-md">
                        <strong>{result.inserted_count}</strong> neu,
                        <strong> {result.updated_count}</strong> aktualisiert,
                        <strong> {result.deleted_count}</strong> gelöscht
                    </p>
                    <button className="btn btn-secondary mt-md" onClick={handleReset}>
                        Weiteren Import starten
                    </button>
                </div>
            )}

            {!result && (
                <>
                    {/* Upload-Zone */}
                    <div
                        className={`file-upload ${dragover ? 'dragover' : ''}`}
                        onClick={() => fileInputRef.current?.click()}
                        onDragOver={(e) => { e.preventDefault(); setDragover(true) }}
                        onDragLeave={() => setDragover(false)}
                        onDrop={handleDrop}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept=".csv"
                            onChange={handleFileSelect}
                            style={{ display: 'none' }}
                        />
                        <div className="file-upload-icon">📁</div>
                        {file ? (
                            <div>
                                <p><strong>{file.name}</strong></p>
                                <p className="file-upload-text">
                                    {(file.size / 1024).toFixed(1)} KB
                                </p>
                            </div>
                        ) : (
                            <p className="file-upload-text">
                                <strong>Klicken</strong> oder CSV-Datei hierher ziehen
                            </p>
                        )}
                    </div>

                    {/* Aktionen */}
                    {file && !preview && (
                        <div className="card mt-md">
                            <div className="flex gap-md">
                                <button
                                    className="btn btn-primary"
                                    onClick={handlePreview}
                                    disabled={loading}
                                >
                                    {loading ? 'Analysiere...' : 'Vorschau anzeigen'}
                                </button>
                                <button className="btn btn-secondary" onClick={handleReset}>
                                    Abbrechen
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Vorschau */}
                    {preview && (
                        <div className="card mt-md">
                            <div className="card-header">
                                <h3 className="card-title">Import-Vorschau</h3>
                            </div>

                            {/* Statistik */}
                            <div className="stats-grid" style={{ marginBottom: 'var(--space-lg)' }}>
                                <div className="stat-card">
                                    <div className="stat-value" style={{ color: 'var(--color-success)' }}>
                                        {preview.inserts}
                                    </div>
                                    <div className="stat-label">Neue Schüler</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value" style={{ color: 'var(--color-warning)' }}>
                                        {preview.updates}
                                    </div>
                                    <div className="stat-label">Aktualisierungen</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value" style={{ color: 'var(--color-error)' }}>
                                        {preview.deletes}
                                    </div>
                                    <div className="stat-label">Zu löschen</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value">{preview.unchanged}</div>
                                    <div className="stat-label">Unverändert</div>
                                </div>
                            </div>

                            {/* Fehler */}
                            {preview.errors > 0 && (
                                <div className="login-error mb-md">
                                    {preview.errors} Fehler gefunden:
                                    <ul style={{ marginTop: 'var(--space-sm)', paddingLeft: 'var(--space-lg)' }}>
                                        {preview.error_messages.slice(0, 5).map((msg, i) => (
                                            <li key={i}>{msg}</li>
                                        ))}
                                        {preview.error_messages.length > 5 && (
                                            <li>... und {preview.error_messages.length - 5} weitere</li>
                                        )}
                                    </ul>
                                </div>
                            )}

                            {/* Änderungen (Beispiele) */}
                            {preview.sample_changes.length > 0 && (
                                <div className="mb-md">
                                    <h4 style={{ marginBottom: 'var(--space-sm)' }}>Beispiel-Änderungen:</h4>
                                    <div className="table-container">
                                        <table className="table">
                                            <thead>
                                                <tr>
                                                    <th>Typ</th>
                                                    <th>ID</th>
                                                    <th>Name</th>
                                                    <th>Klasse</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {preview.sample_changes.slice(0, 10).map((change, i) => (
                                                    <tr key={i}>
                                                        <td>
                                                            <span className={`badge ${change.type === 'insert' ? 'badge-success' :
                                                                change.type === 'update' ? 'badge-warning' :
                                                                    'badge-error'
                                                                }`}>
                                                                {change.type === 'insert' ? 'Neu' :
                                                                    change.type === 'update' ? 'Update' : 'Löschen'}
                                                            </span>
                                                        </td>
                                                        <td>{change.id}</td>
                                                        <td>
                                                            {change.new?.first_name || change.current?.first_name} {' '}
                                                            {change.new?.last_name || change.current?.last_name}
                                                        </td>
                                                        <td>{change.new?.class_name || '-'}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}

                            {/* Aktionen */}
                            <div className="flex gap-md">
                                <button
                                    className="btn btn-primary"
                                    onClick={handleImport}
                                    disabled={importing || preview.errors > preview.total_in_csv * 0.1}
                                >
                                    {importing ? 'Importiere...' : '✓ Import durchführen'}
                                </button>
                                <button className="btn btn-secondary" onClick={handleReset}>
                                    Abbrechen
                                </button>
                            </div>
                        </div>
                    )}
                </>
            )}
        </Layout>
    )
}
