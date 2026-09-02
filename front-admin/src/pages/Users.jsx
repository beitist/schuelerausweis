/**
 * Users Page (Superadmin Only)
 *
 * Verwaltung aller Admin-Benutzer im System (NICHT Schüler!).
 * - Liste aller User mit Rolle, Schule und Status
 * - Erstellen/Bearbeiten/Löschen von Usern (Superadmin, Schul-Admin, Lehrer)
 *
 * Wichtig: Superadmins haben KEINE Schule (schulübergreifend). Für alle
 * anderen Rollen ist die Schulzuordnung Pflicht.
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

// Anzeige-Namen der Rollen (Backend-Wert -> deutsche Bezeichnung)
const ROLE_LABELS = {
    superadmin: 'Superadmin',
    school_admin: 'Schul-Admin',
    teacher: 'Lehrer'
}

export default function Users({ user, onLogout, apiUrl }) {
    const [users, setUsers] = useState([])
    const [schools, setSchools] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // Modal State
    const [showModal, setShowModal] = useState(false)
    const [editingUser, setEditingUser] = useState(null)
    const [formData, setFormData] = useState({
        email: '',
        password: '',
        role: 'teacher',
        school_id: '',
        is_active: true
    })
    const [saving, setSaving] = useState(false)

    // Daten laden
    useEffect(() => {
        fetchUsers()
        fetchSchools()
    }, [])

    const fetchUsers = async () => {
        setLoading(true)
        setError('')
        try {
            const res = await fetch(`${apiUrl}/auth/admin/users`, {
                credentials: 'include'
            })

            if (!res.ok) {
                if (res.status === 403) {
                    throw new Error('Nur Superadmins können Benutzer verwalten')
                }
                throw new Error('Fehler beim Laden der Benutzer')
            }

            const data = await res.json()
            setUsers(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    // Schulen für das Dropdown laden (nur Superadmins haben Zugriff auf /schools)
    const fetchSchools = async () => {
        try {
            const res = await fetch(`${apiUrl}/schools`, {
                credentials: 'include'
            })
            if (res.ok) {
                setSchools(await res.json())
            }
        } catch (err) {
            // Nicht kritisch — das Formular funktioniert auch ohne Schulliste
            console.error('Fehler beim Laden der Schulen:', err)
        }
    }

    // Modal öffnen für Erstellen
    const handleCreate = () => {
        setEditingUser(null)
        setFormData({
            email: '',
            password: '',
            role: 'teacher',
            school_id: '',
            is_active: true
        })
        setError('')
        setShowModal(true)
    }

    // Modal öffnen für Bearbeiten
    const handleEdit = (u) => {
        setEditingUser(u)
        setFormData({
            email: u.email,
            password: '',  // Leer = unverändert
            role: (u.role || 'teacher').toLowerCase(),
            school_id: u.school_id != null ? String(u.school_id) : '',
            is_active: u.is_active
        })
        setError('')
        setShowModal(true)
    }

    // Speichern (Erstellen oder Bearbeiten)
    const handleSave = async (e) => {
        e.preventDefault()
        setSaving(true)
        setError('')

        const isSuperadmin = formData.role === 'superadmin'

        try {
            let url, method, payload

            if (editingUser) {
                // Bearbeiten (Partial-Update via PATCH)
                url = `${apiUrl}/auth/admin/users/${editingUser.id}`
                method = 'PATCH'
                payload = {
                    email: formData.email,
                    role: formData.role,
                    school_id: isSuperadmin ? null : (formData.school_id ? Number(formData.school_id) : null),
                    is_active: formData.is_active
                }
                // Passwort nur senden, wenn ein neues gesetzt wurde
                if (formData.password) {
                    payload.password = formData.password
                }
            } else {
                // Erstellen
                url = `${apiUrl}/auth/admin/create`
                method = 'POST'
                payload = {
                    email: formData.email,
                    password: formData.password,
                    role: formData.role,
                    school_id: isSuperadmin ? null : (formData.school_id ? Number(formData.school_id) : null)
                }
            }

            const res = await fetch(url, {
                method,
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })

            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Fehler beim Speichern'))
            }

            setShowModal(false)

            // Wer sein EIGENES Passwort ändert, entwertet damit sofort das laufende
            // Token — jeder weitere Request wäre 401. Also sauber abmelden statt fetchUsers(),
            // mit klarem Hinweis auf der Login-Seite (sonst wirkt es wie ein Fehlschlag).
            const editingSelf = editingUser && user?.email &&
                editingUser.email?.toLowerCase() === user.email.toLowerCase()
            if (editingSelf && formData.password) {
                try {
                    sessionStorage.setItem('loginNotice',
                        'Dein Passwort wurde geändert. Bitte melde dich mit dem neuen Passwort neu an.')
                } catch (e) { /* sessionStorage evtl. blockiert — egal */ }
                onLogout()
                return
            }

            fetchUsers()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    // Löschen
    const handleDelete = async (u) => {
        if (!confirm(`Benutzer "${u.email}" wirklich löschen?`)) {
            return
        }

        try {
            const res = await fetch(`${apiUrl}/auth/admin/users/${u.id}`, {
                method: 'DELETE',
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Fehler beim Löschen'))
            }

            fetchUsers()
        } catch (err) {
            alert(err.message)
        }
    }

    // Schulname für die Tabelle ermitteln
    const schoolName = (u) => {
        if (u.role?.toLowerCase() === 'superadmin') return '— (alle)'
        if (u.school_name) return u.school_name
        if (u.school_id == null) return '—'
        const s = schools.find((s) => s.id === u.school_id)
        return s ? s.name : `#${u.school_id}`
    }

    const isSuperadminForm = formData.role === 'superadmin'

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <div className="flex justify-between items-center">
                    <div>
                        <h1>Benutzerverwaltung</h1>
                        <p>Admin-Benutzer (Superadmins, Schul-Admins, Lehrer) verwalten</p>
                    </div>
                    <button className="btn btn-primary" onClick={handleCreate}>
                        + Neuer Benutzer
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
                ) : users.length === 0 ? (
                    <div className="text-center" style={{ padding: '3rem' }}>
                        <p style={{ opacity: 0.7 }}>Keine Benutzer vorhanden</p>
                    </div>
                ) : (
                    <div className="table-container">
                        <table className="table">
                            <thead>
                                <tr>
                                    <th>E-Mail</th>
                                    <th>Rolle</th>
                                    <th>Schule</th>
                                    <th>Status</th>
                                    <th>Aktionen</th>
                                </tr>
                            </thead>
                            <tbody>
                                {users.map((u) => (
                                    <tr key={u.id}>
                                        <td><strong>{u.email}</strong></td>
                                        <td>{ROLE_LABELS[u.role?.toLowerCase()] || u.role}</td>
                                        <td>{schoolName(u)}</td>
                                        <td>
                                            {u.is_active ? (
                                                <span style={{ color: 'var(--color-success, #16a34a)' }}>● Aktiv</span>
                                            ) : (
                                                <span style={{ opacity: 0.5 }}>○ Inaktiv</span>
                                            )}
                                        </td>
                                        <td>
                                            <div className="flex gap-sm">
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => handleEdit(u)}
                                                >
                                                    Bearbeiten
                                                </button>
                                                <button
                                                    className="btn btn-danger btn-sm"
                                                    onClick={() => handleDelete(u)}
                                                    disabled={u.id === user?.id}
                                                    title={u.id === user?.id ? 'Sie können sich nicht selbst löschen' : ''}
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
                        <h2>{editingUser ? 'Benutzer bearbeiten' : 'Neuer Benutzer'}</h2>

                        <form onSubmit={handleSave}>
                            <div className="form-group">
                                <label className="form-label">E-Mail *</label>
                                <input
                                    type="email"
                                    className="form-input"
                                    value={formData.email}
                                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                                    placeholder="z.B. admin@schule.de"
                                    required
                                />
                            </div>

                            <div className="form-group">
                                <label className="form-label">
                                    Passwort {editingUser ? '' : '*'}
                                </label>
                                <input
                                    type="password"
                                    className="form-input"
                                    value={formData.password}
                                    onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                                    placeholder={editingUser ? 'Leer lassen = unverändert' : 'Passwort setzen'}
                                    autoComplete="new-password"
                                    required={!editingUser}
                                />
                                {editingUser && (
                                    <small style={{ opacity: 0.6 }}>
                                        Nur ausfüllen, wenn das Passwort geändert werden soll.
                                    </small>
                                )}
                            </div>

                            <div className="form-group">
                                <label className="form-label">Rolle *</label>
                                <select
                                    className="form-input"
                                    value={formData.role}
                                    onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                                    required
                                >
                                    <option value="teacher">Lehrer</option>
                                    <option value="school_admin">Schul-Admin</option>
                                    <option value="superadmin">Superadmin</option>
                                </select>
                            </div>

                            {/* Schule nur für nicht-Superadmins (Superadmins sind schulübergreifend) */}
                            {!isSuperadminForm && (
                                <div className="form-group">
                                    <label className="form-label">Schule *</label>
                                    <select
                                        className="form-input"
                                        value={formData.school_id}
                                        onChange={(e) => setFormData({ ...formData, school_id: e.target.value })}
                                        required
                                    >
                                        <option value="">— Schule wählen —</option>
                                        {schools.map((s) => (
                                            <option key={s.id} value={String(s.id)}>
                                                {s.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                            )}
                            {isSuperadminForm && (
                                <div className="form-group">
                                    <small style={{ opacity: 0.6 }}>
                                        Superadmins haben Zugriff auf alle Schulen und benötigen keine Schulzuordnung.
                                    </small>
                                </div>
                            )}

                            {/* Aktiv-Status nur beim Bearbeiten */}
                            {editingUser && (
                                <div className="form-group">
                                    <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                        <input
                                            type="checkbox"
                                            checked={formData.is_active}
                                            onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                                        />
                                        Aktiv (Login erlaubt)
                                    </label>
                                </div>
                            )}

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
