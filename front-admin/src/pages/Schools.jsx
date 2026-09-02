/**
 * Schools Page (Superadmin Only)
 * 
 * Verwaltung aller Schulen im System.
 * - Liste aller Schulen mit Statistiken
 * - Erstellen/Bearbeiten/Löschen von Schulen
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

export default function Schools({ user, onLogout, apiUrl, token }) {
    const [schools, setSchools] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // Modal State
    const [showModal, setShowModal] = useState(false)
    const [editingSchool, setEditingSchool] = useState(null)
    const [formData, setFormData] = useState({
        name: '',
        path_slug: '',
        pwa_base_url: '',
        logo_url: '',
        primary_color: '#1a73e8',
        contact_email: ''
    })
    const [saving, setSaving] = useState(false)

    // Schulen laden
    useEffect(() => {
        fetchSchools()
    }, [])

    const fetchSchools = async () => {
        setLoading(true)
        setError('')
        try {
            const res = await fetch(`${apiUrl}/schools`, {
                credentials: 'include'
            })

            if (!res.ok) {
                if (res.status === 403) {
                    throw new Error('Nur Superadmins können Schulen verwalten')
                }
                throw new Error('Fehler beim Laden der Schulen')
            }

            const data = await res.json()
            setSchools(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    // Modal öffnen für Erstellen
    const handleCreate = () => {
        setEditingSchool(null)
        setFormData({
            name: '',
            path_slug: '',
            pwa_base_url: '',
            logo_url: '',
            primary_color: '#1a73e8',
            contact_email: ''
        })
        setShowModal(true)
    }

    // Modal öffnen für Bearbeiten
    const handleEdit = async (school) => {
        // Details laden
        try {
            const res = await fetch(`${apiUrl}/schools/${school.id}`, {
                credentials: 'include'
            })
            if (res.ok) {
                const data = await res.json()
                setEditingSchool(data)
                setFormData({
                    name: data.name,
                    path_slug: data.path_slug || '',
                    pwa_base_url: data.pwa_base_url || '',
                    logo_url: data.logo_url || '',
                    primary_color: data.primary_color || '#1a73e8',
                    contact_email: data.contact_email || ''
                })
                setShowModal(true)
            }
        } catch (err) {
            alert('Fehler beim Laden der Schule')
        }
    }

    // Speichern (Erstellen oder Bearbeiten)
    const handleSave = async (e) => {
        e.preventDefault()
        setSaving(true)
        setError('')

        try {
            const url = editingSchool
                ? `${apiUrl}/schools/${editingSchool.id}`
                : `${apiUrl}/schools`

            const res = await fetch(url, {
                method: editingSchool ? 'PUT' : 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Speichern'))
            }

            setShowModal(false)
            fetchSchools()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    // Löschen
    const handleDelete = async (school) => {
        if (!confirm(`Schule "${school.name}" wirklich löschen?\n\nDies ist nur möglich, wenn keine Schüler, Klassen oder Admins mehr existieren.`)) {
            return
        }

        try {
            const res = await fetch(`${apiUrl}/schools/${school.id}`, {
                method: 'DELETE',
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Löschen'))
            }

            fetchSchools()
        } catch (err) {
            alert(err.message)
        }
    }

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <div className="flex justify-between items-center">
                    <div>
                        <h1>Schulenverwaltung</h1>
                        <p>Alle Schulen im System verwalten</p>
                    </div>
                    <button className="btn btn-primary" onClick={handleCreate}>
                        + Neue Schule
                    </button>
                </div>
            </div>

            {error && <div className="alert alert-error mb-md">{error}</div>}

            {/* Tabelle */}
            <div className="card">
                {loading ? (
                    <div className="text-center">
                        <div className="spinner" style={{ margin: '2rem auto' }}></div>
                    </div>
                ) : schools.length === 0 ? (
                    <div className="text-center" style={{ padding: '3rem' }}>
                        <p style={{ opacity: 0.7 }}>Keine Schulen vorhanden</p>
                    </div>
                ) : (
                    <div className="table-container">
                        <table className="table">
                            <thead>
                                <tr>
                                    <th>Name</th>
                                    <th>URL-Pfad</th>
                                    <th>Farbe</th>
                                    <th>Klassen</th>
                                    <th>Schüler</th>
                                    <th>Aktionen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {schools.map((school) => (
                                    <tr key={school.id}>
                                        <td><strong>{school.name}</strong></td>
                                        <td>
                                            {school.path_slug ? (
                                                <code style={{
                                                    background: 'var(--color-gray-100)',
                                                    padding: '2px 6px',
                                                    borderRadius: '4px'
                                                }}>
                                                    /{school.path_slug}
                                                </code>
                                            ) : (
                                                <span style={{ opacity: 0.4 }}>—</span>
                                            )}
                                        </td>
                                        <td>
                                            <div style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: '0.5rem'
                                            }}>
                                                <div style={{
                                                    width: '20px',
                                                    height: '20px',
                                                    borderRadius: '4px',
                                                    background: school.primary_color || '#1a73e8'
                                                }}></div>
                                                <span style={{ fontSize: '0.8rem', opacity: 0.7 }}>
                                                    {school.primary_color}
                                                </span>
                                            </div>
                                        </td>
                                        <td>{school.class_count}</td>
                                        <td>{school.student_count}</td>
                                        <td>
                                            <div className="flex gap-sm">
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => handleEdit(school)}
                                                >
                                                    Bearbeiten
                                                </button>
                                                <button
                                                    className="btn btn-danger btn-sm"
                                                    onClick={() => handleDelete(school)}
                                                >
                                                    Löschen
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Modal für Erstellen/Bearbeiten */}
            {showModal && (
                <div className="modal-overlay" onClick={() => setShowModal(false)}>
                    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                        <h2>{editingSchool ? 'Schule bearbeiten' : 'Neue Schule'}</h2>

                        <form onSubmit={handleSave}>
                            <div className="form-group">
                                <label className="form-label">Name *</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={formData.name}
                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                    placeholder="z.B. Käthe-Kollwitz-Berufskolleg"
                                    required
                                />
                            </div>

                            <div className="form-group">
                                <label className="form-label">URL-Pfad</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={formData.path_slug}
                                    onChange={(e) => setFormData({ ...formData, path_slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '') })}
                                    placeholder="z.B. kkbk"
                                />
                                <small style={{ opacity: 0.6 }}>
                                    Erreichbar unter domain.de/{formData.path_slug || 'kkbk'} — Nur Kleinbuchstaben, Zahlen und Bindestriche. Leer lassen wenn nur eine Schule.
                                </small>
                            </div>

                            <div className="form-group">
                                <label className="form-label">PWA-URL (Schüler-App)</label>
                                <input
                                    type="url"
                                    className="form-input"
                                    value={formData.pwa_base_url}
                                    onChange={(e) => setFormData({ ...formData, pwa_base_url: e.target.value })}
                                    placeholder="https://ausweis.example.de"
                                />
                                <small style={{ opacity: 0.6 }}>
                                    Basis-URL der Schüler-App. Wird in QR-Codes auf gedruckten Karten verwendet.
                                </small>
                            </div>

                            <div className="form-group">
                                <label className="form-label">Logo URL</label>
                                <input
                                    type="url"
                                    className="form-input"
                                    value={formData.logo_url}
                                    onChange={(e) => setFormData({ ...formData, logo_url: e.target.value })}
                                    placeholder="https://..."
                                />
                            </div>

                            <div className="form-group">
                                <label className="form-label">Primärfarbe</label>
                                <div className="flex gap-md items-center">
                                    <input
                                        type="color"
                                        value={formData.primary_color}
                                        onChange={(e) => setFormData({ ...formData, primary_color: e.target.value })}
                                        style={{ width: '60px', height: '40px', padding: 0, border: 'none' }}
                                    />
                                    <input
                                        type="text"
                                        className="form-input"
                                        value={formData.primary_color}
                                        onChange={(e) => setFormData({ ...formData, primary_color: e.target.value })}
                                        placeholder="#1a73e8"
                                        style={{ maxWidth: '120px' }}
                                    />
                                </div>
                            </div>

                            {error && <div className="alert alert-error">{error}</div>}

                            <div className="flex gap-md justify-end" style={{ marginTop: '1.5rem' }}>
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => setShowModal(false)}
                                >
                                    Abbrechen
                                </button>
                                <button
                                    type="submit"
                                    className="btn btn-primary"
                                    disabled={saving}
                                >
                                    {saving ? 'Speichern...' : 'Speichern'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </Layout>
    )
}
