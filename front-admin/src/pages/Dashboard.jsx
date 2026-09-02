/**
 * Dashboard Page
 * 
 * Übersicht mit Statistiken.
 */

import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import Layout from '../components/Layout'

export default function Dashboard({ user, onLogout, apiUrl, token }) {
    const [stats, setStats] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    useEffect(() => {
        fetchStats()
    }, [])

    const fetchStats = async () => {
        // Superadmin hat keine eigene Schule → die schul-gebundene Abfrage würde mit
        // school_id=null als 422 fehlschlagen. Stattdessen unten einen Hinweis zeigen.
        if (!user?.school_id) {
            setLoading(false)
            return
        }
        try {
            const res = await fetch(`${apiUrl}/admin/dashboard?school_id=${user.school_id}`, {
                credentials: 'include'
            })

            if (!res.ok) throw new Error('Fehler beim Laden')

            const data = await res.json()
            setStats(data)
        } catch (err) {
            setError('Dashboard-Daten konnten nicht geladen werden')
        } finally {
            setLoading(false)
        }
    }

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <h1>Dashboard</h1>
                <p>Willkommen zurück, {user?.email}</p>
            </div>

            {error && <div className="login-error">{error}</div>}

            {!loading && !user?.school_id ? (
                <div className="stat-card" style={{ padding: '1.5rem' }}>
                    <p>Du bist als <strong>Superadmin</strong> angemeldet und hast keine eigene Schule —
                    daher gibt es hier keine schul-gebundene Übersicht. Verwalte Schulen und Konten
                    über die Navigation.</p>
                </div>
            ) : loading ? (
                <div className="loading-screen">
                    <div className="spinner"></div>
                </div>
            ) : stats && (
                <>
                    {/* Statistik-Karten */}
                    <div className="stats-grid">
                        <div className="stat-card">
                            <div className="stat-value">{stats.active_students}</div>
                            <div className="stat-label">Aktive Schüler</div>
                            {stats.total_students !== stats.active_students && (
                                <div className="stat-sub" style={{ fontSize: '0.8rem', opacity: 0.6, marginTop: '0.25rem' }}>
                                    {stats.total_students} gesamt
                                </div>
                            )}
                        </div>

                        <div className="stat-card">
                            <div className="stat-value">{stats.active_teachers}</div>
                            <div className="stat-label">Lehrkräfte</div>
                            {stats.total_teachers !== stats.active_teachers && (
                                <div className="stat-sub" style={{ fontSize: '0.8rem', opacity: 0.6, marginTop: '0.25rem' }}>
                                    {stats.total_teachers} gesamt
                                </div>
                            )}
                        </div>

                        <div className="stat-card">
                            <div className="stat-value">{stats.total_classes}</div>
                            <div className="stat-label">Klassen</div>
                        </div>

                        <div className="stat-card">
                            <div className="stat-value">{stats.total_levels}</div>
                            <div className="stat-label">Bildungsgänge</div>
                        </div>
                    </div>

                    {/* Schnellaktionen */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Schnellaktionen</h3>
                        </div>
                        <div className="flex gap-md" style={{ flexWrap: 'wrap' }}>
                            <Link to="/import" className="btn btn-primary">
                                CSV importieren
                            </Link>
                            <Link to="/students" className="btn btn-secondary">
                                Schüler verwalten
                            </Link>
                            <Link to="/teachers" className="btn btn-secondary">
                                Lehrkräfte verwalten
                            </Link>
                        </div>
                    </div>

                    {/* Letzter Import */}
                    {stats.last_import && (
                        <div className="card mt-md">
                            <div className="card-header">
                                <h3 className="card-title">Letzter Import</h3>
                            </div>
                            <p>{new Date(stats.last_import).toLocaleString('de-DE')}</p>
                        </div>
                    )}
                </>
            )}
        </Layout>
    )
}
