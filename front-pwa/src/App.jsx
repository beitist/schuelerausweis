/**
 * Hagen-ID PWA - Schüler-App
 *
 * Digitaler Schülerausweis mit dynamischem QR-Code.
 * Funktioniert offline!
 *
 * ROUTING: Pfad-basiert — domain.de/kkbk → Schule "kkbk"
 * HIGHLANDER: Nur EIN Gerät kann gleichzeitig eingeloggt sein.
 */

import { useState, useEffect, useCallback } from 'react'
import Login from './pages/Login'
import Card from './pages/Card'
import { clearDeviceKey, importTotpSecret, hasTotpKey, clearTotpKey } from './lib/deviceKey'

// API URL: Aus Environment oder Produktion-Default
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * Slug aus URL-Pfad extrahieren.
 * /kkbk → "kkbk", /kkbk/ → "kkbk", / → null, /foo/bar → "foo"
 */
function getSlugFromPath() {
    const path = window.location.pathname.replace(/^\/+|\/+$/g, '')
    const firstSegment = path.split('/')[0] || null
    return firstSegment
}

function App() {
    // Auth liegt in HttpOnly-Cookies (JS-unlesbar). Der Login-Zustand wird daher
    // per stillem Check gegen /pwa/me bestimmt, nicht aus einem lokalen Token.
    // `authed`: null = wird geprüft.
    const [authed, setAuthed] = useState(null)
    // Der TOTP-Seed liegt als non-extractable Key in IndexedDB — nie als String
    // im State/localStorage. Hier wird nur getrackt, OB ein Key vorhanden ist.
    const [totpReady, setTotpReady] = useState(false)
    const [userData, setUserData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [offline, setOffline] = useState(!navigator.onLine)

    // Schulinfo (aus URL-Slug oder API aufgelöst)
    const [schoolInfo, setSchoolInfo] = useState(null)
    const [schoolList, setSchoolList] = useState(null)  // Für Schulauswahl (wenn kein Slug)

    // HIGHLANDER: Wurde der User auf einem anderen Gerät eingeloggt?
    const [kickedOut, setKickedOut] = useState(false)

    // Online/Offline Status
    useEffect(() => {
        const handleOnline = () => setOffline(false)
        const handleOffline = () => setOffline(true)
        window.addEventListener('online', handleOnline)
        window.addEventListener('offline', handleOffline)
        return () => {
            window.removeEventListener('online', handleOnline)
            window.removeEventListener('offline', handleOffline)
        }
    }, [])

    // Schule auflösen: Slug → Schulinfo
    useEffect(() => {
        resolveSchool()
    }, [])

    const resolveSchool = async () => {
        const slug = getSlugFromPath()

        if (slug) {
            // Slug in URL → Schule per API auflösen
            try {
                const res = await fetch(`${API_URL}/schools/public/${slug}`)
                if (res.ok) {
                    const data = await res.json()
                    setSchoolInfo(data)
                } else {
                    // Unbekannter Slug → alle Schulen laden
                    await loadSchoolList()
                }
            } catch {
                // Offline → aus Cache versuchen
                const cached = localStorage.getItem('school_info')
                if (cached) setSchoolInfo(JSON.parse(cached))
            }
        } else {
            // Kein Slug → Schulliste laden
            await loadSchoolList()
        }
    }

    const loadSchoolList = async () => {
        try {
            const res = await fetch(`${API_URL}/schools/public`)
            if (res.ok) {
                const schools = await res.json()
                if (schools.length === 1) {
                    // Nur eine Schule → direkt verwenden
                    setSchoolInfo(schools[0])
                } else {
                    setSchoolList(schools)
                }
            }
        } catch {
            const cached = localStorage.getItem('school_info')
            if (cached) setSchoolInfo(JSON.parse(cached))
        }
    }

    // Schulinfo cachen wenn aufgelöst
    useEffect(() => {
        if (schoolInfo) {
            localStorage.setItem('school_info', JSON.stringify(schoolInfo))
        }
    }, [schoolInfo])

    /**
     * API-Fetch Wrapper: Auth über HttpOnly-Cookie → `credentials: 'include'`,
     * keine Auth-Header. Highlander „kicked_out" wird erkannt.
     */
    const apiFetch = useCallback(async (endpoint, options = {}) => {
        const res = await fetch(`${API_URL}${endpoint}`, {
            ...options,
            credentials: 'include',
        })

        if (res.status === 401) {
            const kickedOutHeader = res.headers.get('X-Kicked-Out')
            if (kickedOutHeader === 'true') {
                setKickedOut(true)
                clearLocalData()
                return null
            }
        }

        return res
    }, [])

    // TOTP-Seed vom Server als non-extractable Key übernehmen — NICHT in
    // localStorage ablegen. Setzt totpReady, sobald ein Key vorhanden ist.
    const adoptTotpSecret = async (secret) => {
        if (secret) {
            await importTotpSecret(secret)
        }
        setTotpReady(await hasTotpKey())
    }

    // Lokalen Cache leeren (Auth-Cookie löscht der Server via /auth/student/logout).
    const clearLocalData = () => {
        // Defensive: auch mögliche Reste älterer App-Versionen entfernen
        localStorage.removeItem('totp_secret')
        localStorage.removeItem('user_data')
        localStorage.removeItem('photo_private_key')
        localStorage.removeItem('student_token')
        localStorage.removeItem('session_token')
        // Signier- UND TOTP-Schlüssel aus IndexedDB entfernen (Hygiene auf geteilten Geräten)
        clearDeviceKey()
        clearTotpKey()
        setTotpReady(false)
        setUserData(null)
        setAuthed(false)
    }

    // Beim Start: persistenten Speicher anfordern (iOS: für installierte PWAs i.d.R.
    // gewährt → IndexedDB-Schlüssel überleben) + stiller Login-Check. Dazu prüfen, ob
    // schon ein TOTP-Key da ist (offline-fähig, ohne Server).
    useEffect(() => {
        if (navigator.storage?.persist) {
            navigator.storage.persist().catch(() => {})
        }
        hasTotpKey().then(setTotpReady)
        silentCheck()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // Stiller Check gegen /pwa/me: entscheidet über eingeloggt/ausgeloggt.
    const silentCheck = async () => {
        // Cache sofort anzeigen (auch offline), bevor der Server antwortet.
        const cached = localStorage.getItem('user_data')
        if (cached) {
            setUserData(JSON.parse(cached))
        }
        try {
            const res = await apiFetch('/pwa/me')
            if (!res) return  // kicked_out schon behandelt
            if (res.ok) {
                const data = await res.json()
                // Den TOTP-Seed NICHT mit ins gecachte user_data schreiben —
                // er wird separat als non-extractable Key importiert.
                const { totp_secret, ...safe } = data
                setUserData(safe)
                localStorage.setItem('user_data', JSON.stringify(safe))
                adoptTotpSecret(totp_secret)
                setAuthed(true)
            } else if (res.status === 401) {
                // Cookie weg/abgelaufen ODER Schüler abgemeldet/inaktiv → kein Ausweis.
                clearLocalData()
            }
        } catch {
            // Offline/Netzfehler: mit Cache eingeloggt bleiben, sonst Login zeigen.
            setAuthed(!!cached)
        } finally {
            setLoading(false)
        }
    }

    // Manuelles Neuladen der Ausweis-Daten (z.B. nach Foto-Upload)
    const fetchUserData = async () => {
        try {
            const res = await apiFetch('/pwa/me')
            if (!res) return
            if (res.ok) {
                const data = await res.json()
                // Den TOTP-Seed NICHT mit ins gecachte user_data schreiben —
                // er wird separat als non-extractable Key importiert.
                const { totp_secret, ...safe } = data
                setUserData(safe)
                localStorage.setItem('user_data', JSON.stringify(safe))
                adoptTotpSecret(totp_secret)
            } else if (res.status === 401) {
                clearLocalData()
            }
        } catch {
            /* offline — Cache bleibt */
        }
    }

    // Nach erfolgreichem Login (Server hat die Cookies gesetzt): totp übernehmen,
    // eingeloggt schalten und Ausweis-Daten frisch holen.
    const login = (newTotpSecret) => {
        adoptTotpSecret(newTotpSecret)   // importieren, nicht speichern
        setKickedOut(false)
        setAuthed(true)
        fetchUserData()
    }

    const logout = async () => {
        try {
            await fetch(`${API_URL}/auth/student/logout`, {
                method: 'POST',
                credentials: 'include',
            })
        } catch { /* egal — Cookie läuft ohnehin ab */ }
        clearLocalData()
    }

    const dismissKickout = () => {
        setKickedOut(false)
    }

    /**
     * Schulauswahl-Handler (wenn User eine Schule aus der Liste wählt)
     */
    const selectSchool = (school) => {
        setSchoolInfo(school)
        setSchoolList(null)
        // URL aktualisieren (ohne Reload)
        if (school.path_slug) {
            window.history.replaceState(null, '', `/${school.path_slug}`)
        }
    }

    // Loading
    if (loading) {
        return (
            <div className="loading-screen">
                <div className="spinner"></div>
                <p>Lade Ausweis...</p>
            </div>
        )
    }

    // HIGHLANDER: Kickout-Meldung
    if (kickedOut) {
        return (
            <div className="kickout-screen">
                <div className="kickout-card">
                    <div className="kickout-icon">📱</div>
                    <h2>Anderes Gerät erkannt</h2>
                    <p>
                        Du hast dich auf einem anderen Gerät angemeldet.
                        Pro Schüler kann nur ein Gerät gleichzeitig aktiv sein.
                    </p>
                    <button className="btn btn-primary" onClick={dismissKickout}>
                        Erneut anmelden
                    </button>
                </div>
            </div>
        )
    }

    // Schulauswahl (wenn mehrere Schulen und kein Slug in URL)
    if (!authed && !schoolInfo && schoolList) {
        return (
            <div className="school-select-screen">
                <div className="school-select-card">
                    <h2>Schule auswählen</h2>
                    <div className="school-list">
                        {schoolList.map((school) => (
                            <button
                                key={school.id}
                                className="school-item"
                                onClick={() => selectSchool(school)}
                                style={{ borderLeft: `4px solid ${school.primary_color || '#1a73e8'}` }}
                            >
                                {school.name}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
        )
    }

    // Nicht eingeloggt → Login
    if (!authed) {
        return (
            <Login
                onLogin={login}
                apiUrl={API_URL}
                schoolInfo={schoolInfo}
            />
        )
    }

    // Ausweis anzeigen
    return (
        <Card
            userData={userData}
            totpReady={totpReady}
            onLogout={logout}
            onRefresh={fetchUserData}
            offline={offline}
            apiUrl={API_URL}
        />
    )
}

export default App
