/**
 * Login Page für Schüler
 *
 * Passwordless: Nachname + Geburtsdatum + 6-stellige PIN
 *
 * school_id wird aus schoolInfo-Prop übernommen (via URL-Slug aufgelöst).
 * Wenn nicht vorhanden → Backend sucht global (Auto-Erkennung).
 */

import { useState } from 'react'
import InstallButton from '../components/InstallButton'
import { registerDeviceKey } from '../lib/deviceKey'

export default function Login({ onLogin, apiUrl, schoolInfo }) {
    const [lastName, setLastName] = useState('')
    const [dob, setDob] = useState('')
    const [loginPin, setLoginPin] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)

        try {
            const body = {
                last_name: lastName,
                dob: dob,
                login_pin: loginPin
            }
            // school_id nur mitsenden wenn bekannt
            if (schoolInfo?.id) {
                body.school_id = schoolInfo.id
            }

            const res = await fetch(`${apiUrl}/auth/student/login`, {
                method: 'POST',
                credentials: 'include',   // Server setzt HttpOnly-Auth-Cookies
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            })

            const data = await res.json()

            if (!res.ok) {
                setError(data.detail || 'Login fehlgeschlagen')
                return
            }

            // Geräte-Signierschlüssel ERZEUGEN (auf dem Gerät, non-extractable) und
            // den Public Key registrieren (per Cookie authentifiziert). Frischer
            // Schlüssel pro Login. Best-effort: schlägt es fehl (z.B. wackliges Netz),
            // wird es beim Öffnen der Card automatisch nachgeholt.
            await registerDeviceKey(apiUrl, { regenerate: true })

            onLogin(data.totp_secret)

        } catch (err) {
            setError('Verbindung fehlgeschlagen. Bitte prüfe deine Internetverbindung.')
        } finally {
            setLoading(false)
        }
    }

    // Schulfarbe für Branding
    const schoolColor = schoolInfo?.primary_color || '#1a73e8'

    return (
        <div className="login-page">
            <div className="login-card">
                <div className="login-logo">
                    {schoolInfo ? (
                        <>
                            {schoolInfo.logo_url && (
                                <img
                                    src={schoolInfo.logo_url}
                                    alt=""
                                    style={{ height: '48px', marginBottom: '8px' }}
                                    onError={(e) => { e.target.style.display = 'none' }}
                                />
                            )}
                            <h1 style={{ color: schoolColor }}>{schoolInfo.name}</h1>
                            <p>Digitaler Ausweis</p>
                        </>
                    ) : (
                        <>
                            <h1>Käthe-ID</h1>
                            <p>Dein digitaler Schülerausweis</p>
                        </>
                    )}
                </div>

                {error && <div className="login-error">{error}</div>}

                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label className="form-label">Nachname</label>
                        <input
                            type="text"
                            className="form-input"
                            value={lastName}
                            onChange={(e) => setLastName(e.target.value)}
                            placeholder="Mustermann"
                            required
                            autoComplete="family-name"
                            autoCapitalize="words"
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label">Geburtsdatum</label>
                        <input
                            type="date"
                            className="form-input"
                            value={dob}
                            onChange={(e) => setDob(e.target.value)}
                            required
                        />
                    </div>

                    <div className="form-group">
                        <label className="form-label">PIN <span style={{ opacity: 0.6, fontWeight: 'normal' }}>(6-stellig)</span></label>
                        <input
                            type="text"
                            className="form-input"
                            value={loginPin}
                            onChange={(e) => setLoginPin(e.target.value)}
                            placeholder="123456"
                            required
                            autoComplete="off"
                            pattern="[0-9]{6}"
                            inputMode="numeric"
                            maxLength={6}
                        />
                        <small style={{ opacity: 0.6, fontSize: '0.75rem' }}>
                            Steht auf deinem Ausdruck vom Sekretariat
                        </small>
                    </div>

                    <button
                        type="submit"
                        className="btn btn-primary"
                        disabled={loading}
                        style={schoolInfo ? { backgroundColor: schoolColor } : {}}
                    >
                        {loading ? 'Prüfe...' : 'Ausweis laden'}
                    </button>
                </form>
            </div>

            <div className="login-install-hint">
                <InstallButton />
            </div>
        </div>
    )
}
