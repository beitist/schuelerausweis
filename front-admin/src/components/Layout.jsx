/**
 * Layout Component
 * 
 * Sidebar-Layout für alle authentifizierten Seiten.
 */

import { Link, useLocation } from 'react-router-dom'

export default function Layout({ user, onLogout, children }) {
    const location = useLocation()

    const isActive = (path) => location.pathname === path

    return (
        <div className="page">
            {/* Sidebar */}
            <aside className="sidebar">
                <div className="sidebar-logo">
                    🎓 Hagen-ID
                </div>

                <nav className="sidebar-nav">
                    <Link
                        to="/"
                        className={`sidebar-link ${isActive('/') ? 'active' : ''}`}
                    >
                        📊 Dashboard
                    </Link>

                    {/* Schülerverwaltung - NICHT für Superadmin (der verwaltet nur Schulen) */}
                    {user?.role?.toLowerCase() !== 'superadmin' && (
                        <Link
                            to="/students"
                            className={`sidebar-link ${isActive('/students') ? 'active' : ''}`}
                        >
                            👥 Schüler
                        </Link>
                    )}

                    {/* Kollegium - eigener Bereich für Lehrer/Dienstausweise */}
                    {user?.role?.toLowerCase() !== 'superadmin' && (
                        <Link
                            to="/teachers"
                            className={`sidebar-link ${isActive('/teachers') ? 'active' : ''}`}
                        >
                            👔 Kollegium
                        </Link>
                    )}

                    <Link
                        to="/import"
                        className={`sidebar-link ${isActive('/import') ? 'active' : ''}`}
                    >
                        📁 CSV-Import
                    </Link>

                    {/* QR-Druck: PINs pro Klasse drucken – NICHT für Superadmin */}
                    {user?.role?.toLowerCase() !== 'superadmin' && (
                        <Link
                            to="/print"
                            className={`sidebar-link ${isActive('/print') ? 'active' : ''}`}
                        >
                            🖨️ QR-Codes drucken
                        </Link>
                    )}

                    {/* Nur für Superadmins: Schulenverwaltung */}
                    {user?.role?.toLowerCase() === 'superadmin' && (
                        <Link
                            to="/schools"
                            className={`sidebar-link ${isActive('/schools') ? 'active' : ''}`}
                        >
                            🏫 Schulen
                        </Link>
                    )}

                    {/* Nur für Superadmins: Benutzerverwaltung (Admins/Superadmins) */}
                    {user?.role?.toLowerCase() === 'superadmin' && (
                        <Link
                            to="/users"
                            className={`sidebar-link ${isActive('/users') ? 'active' : ''}`}
                        >
                            🔑 Benutzer
                        </Link>
                    )}

                    {/* Nur für Superadmins: Audit-Log (systemweites Ereignis-Protokoll) */}
                    {user?.role?.toLowerCase() === 'superadmin' && (
                        <Link
                            to="/audit-logs"
                            className={`sidebar-link ${isActive('/audit-logs') ? 'active' : ''}`}
                        >
                            📜 Audit-Log
                        </Link>
                    )}

                    {/* Nur für Superadmins: System-Status (Live-Betriebsübersicht) */}
                    {user?.role?.toLowerCase() === 'superadmin' && (
                        <Link
                            to="/system"
                            className={`sidebar-link ${isActive('/system') ? 'active' : ''}`}
                        >
                            🩺 System-Status
                        </Link>
                    )}

                    {/* Settings - für alle Admins */}
                    <Link
                        to="/settings"
                        className={`sidebar-link ${isActive('/settings') ? 'active' : ''}`}
                    >
                        ⚙️ Einstellungen
                    </Link>
                </nav>

                <div className="sidebar-footer">
                    <div style={{ marginBottom: 'var(--space-sm)', fontSize: '0.875rem' }}>
                        <div style={{ color: 'var(--color-text-light)' }}>Angemeldet als</div>
                        <div>{user?.email}</div>
                    </div>
                    <button className="btn btn-secondary" onClick={onLogout} style={{ width: '100%' }}>
                        Abmelden
                    </button>
                </div>
            </aside>

            {/* Main Content */}
            <main className="main-content">
                {children}
            </main>
        </div>
    )
}
