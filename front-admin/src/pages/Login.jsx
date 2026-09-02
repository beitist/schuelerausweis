/**
 * Login Page
 * 
 * Admin-Login mit E-Mail und Passwort.
 */

import { useState, useEffect } from 'react'
import { apiError } from '../lib/apiError'

export default function Login({ onLogin, apiUrl }) {
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [notice, setNotice] = useState('')

    // Einmalige Hinweis-Meldung (z.B. nach eigenem Passwort-Wechsel → Auto-Logout).
    useEffect(() => {
        try {
            const n = sessionStorage.getItem('loginNotice')
            if (n) { setNotice(n); sessionStorage.removeItem('loginNotice') }
        } catch (e) { /* sessionStorage evtl. blockiert — egal */ }
    }, [])

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)

        try {
            const res = await fetch(`${apiUrl}/auth/admin/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',  // CRITICAL: Erlaubt Browser Cookies zu speichern!
                body: JSON.stringify({ email, password })
            })

            const data = await res.json()

            if (!res.ok) {
                // 401 = falsche Credentials, 422 = ungültiges Format (z.B. keine echte E-Mail).
                // apiError() macht daraus IMMER einen String (sonst React-Crash #31).
                setError(apiError(data, res.status === 401 ? 'E-Mail oder Passwort falsch' : 'Login fehlgeschlagen'))
                return
            }

            // Das Cookie setzt das Backend; über /auth/me (mit credentials: 'include')
            // wird geprüft, ob die Session steht, und der User geladen.
            const userRes = await fetch(`${apiUrl}/auth/me`, {
                credentials: 'include'
            })

            if (!userRes.ok) {
                throw new Error('Login session konnte nicht erstellt werden')
            }

            const userData = await userRes.json()

            onLogin(userData)

        } catch (err) {
            setError('Verbindung zum Server fehlgeschlagen')
            // Explizit Logout triggern um Cleanup zu machen
            try {
                await fetch(`${apiUrl}/auth/admin/logout`, { method: 'POST' })
            } catch (e) { }
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="login-page">
            <div className="login-card">
                <div className="login-logo">
                    <h1>🎓 Hagen-ID</h1>
                    <p>Admin Dashboard</p>
                </div>

                {notice && <div className="login-notice" style={{ padding: '0.75rem 1rem', marginBottom: '1rem', borderRadius: '8px', background: 'rgba(37,99,235,0.1)', color: '#1d4ed8', fontSize: '0.9rem' }}>{notice}</div>}
                {error && <div className="login-error">{error}</div>}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label className="form-label">E-Mail</label>
                        <input
                            type="email"
                            className="form-input"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="admin@schule.de"
                            required
                            autoFocus
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label">Passwort</label>
                        <input
                            type="password"
                            className="form-input"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="••••••••"
                            required
                        />
                    </div>

                    <button
                        type="submit"
                        className="btn btn-primary"
                        style={{ width: '100%' }}
                        disabled={loading}
                    >
                        {loading ? 'Wird geladen...' : 'Anmelden'}
                    </button>
                </form>
            </div>
        </div>
    )
}
