/**
 * Hagen-ID Admin Dashboard
 * 
 * Hauptkomponente mit Routing und Auth-State.
 */

import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'

// Pages
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Students from './pages/Students'
import Teachers from './pages/Teachers'
import Import from './pages/Import'
import Schools from './pages/Schools'
import Users from './pages/Users'
import Settings from './pages/Settings'
import PrintPins from './pages/PrintPins'
import AuditLogs from './pages/AuditLogs'
import SystemStatus from './pages/SystemStatus'

// API URL: Aus Environment oder Produktion-Default
// Lokal: VITE_API_URL=http://localhost:8000 in .env setzen
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * Auth Context für globalen Zustand
 */
export const AuthContext = {
    token: null,
    user: null,
    setAuth: () => { },
    logout: () => { }
}

function App() {
    // Auth State - Initial null, wird durch ersten Fetch geprüft.
    // Da die Auth in HttpOnly-Cookies liegt (JS-unlesbar), lässt sich der
    // Login-Status nur über einen Aufruf von /auth/me feststellen.
    const [user, setUser] = useState(null)
    const [loading, setLoading] = useState(true)

    // Beim Start: Prüfen ob Cookie da ist (via API Call)
    useEffect(() => {
        fetchUser()
    }, [])

    const fetchUser = async () => {
        try {
            // Credentials: 'include' ist wichtig damit Cookies mitgesendet werden!
            const res = await fetch(`${API_URL}/auth/me`, {
                credentials: 'include'
            })

            if (res.ok) {
                const data = await res.json()
                setUser(data)
            } else {
                // Nicht eingeloggt
                setUser(null)
            }
        } catch (err) {
            console.error('Fehler beim Laden der User-Daten:', err)
            setUser(null)
        } finally {
            setLoading(false)
        }
    }

    const login = (userData) => {
        setUser(userData)
    }

    const logout = async () => {
        try {
            // Logout Endpoint aufrufen um Cookie zu löschen
            await fetch(`${API_URL}/auth/admin/logout`, {
                method: 'POST',
                credentials: 'include'
            })
        } catch (e) {
            console.error(e)
        }
        setUser(null)
    }

    // Loading-State
    if (loading) {
        return (
            <div className="loading-screen">
                <div className="spinner"></div>
                <p>Lade...</p>
            </div>
        )
    }

    return (
        <BrowserRouter>
            <div className="app">
                <Routes>
                    {/* Login */}
                    <Route
                        path="/login"
                        element={user ? <Navigate to="/" /> : <Login onLogin={login} apiUrl={API_URL} />}
                    />

                    {/* Protected Routes */}
                    <Route
                        path="/"
                        element={user ? <Dashboard user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/students"
                        element={user ? <Students user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/teachers"
                        element={user ? <Teachers user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/import"
                        element={user ? <Import user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/schools"
                        element={user ? <Schools user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/users"
                        element={user ? <Users user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/settings"
                        element={user ? <Settings user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/print"
                        element={user ? <PrintPins user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/audit-logs"
                        element={user ? <AuditLogs user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />
                    <Route
                        path="/system"
                        element={user ? <SystemStatus user={user} onLogout={logout} apiUrl={API_URL} /> : <Navigate to="/login" />}
                    />

                    {/* Fallback */}
                    <Route path="*" element={<Navigate to="/" />} />
                </Routes>
            </div>
        </BrowserRouter>
    )
}

export default App
