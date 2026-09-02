/**
 * Settings Page
 *
 * Schuleinstellungen bearbeiten.
 * - Gültigkeitsdauer für Ausweise
 * - Feature-Toggles
 * - Branding (Logo, Farbe)
 * - Kontakt
 * - API-Zugang (Key-Verwaltung für externes Sync-Tool)
 */

import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { apiError } from '../lib/apiError'

// Verfügbare Features mit Beschreibungen
const AVAILABLE_FEATURES = [
    { key: 'schulbescheinigung', label: 'Schulbescheinigung', description: 'Digitale Schulbescheinigung zum Download' },
    { key: 'push_notifications', label: 'Push-Benachrichtigungen', description: 'Push-Nachrichten an Schüler senden' },
    { key: 'photo_upload', label: 'Foto-Upload', description: 'Schüler können eigene Fotos hochladen' },
]

export default function Settings({ user, onLogout, apiUrl, token }) {
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')

    // Settings State
    const [settings, setSettings] = useState({
        validity_strategy: 'half_year',
        validity_custom_date: '',
        features_enabled: {},
        contact_email: '',
        address: '',
        school_number: '',
        phone: '',
        logo_url: '',
        primary_color: '#1a73e8',
        cert_template_student: '',
        cert_template_teacher: ''
    })

    // Klassen (für den Schulbescheinigungs-Klassenfilter)
    const [classes, setClasses] = useState([])
    // Logo-Neuladen State
    const [logoRefreshing, setLogoRefreshing] = useState(false)

    // API-Key State
    const [apiKey, setApiKey] = useState({ hasKey: false, createdAt: null })
    const [newApiKey, setNewApiKey] = useState(null)
    const [apiKeyLoading, setApiKeyLoading] = useState(false)
    const [apiKeyCopied, setApiKeyCopied] = useState(false)
    const [showRevokeConfirm, setShowRevokeConfirm] = useState(false)
    const [showGenerateConfirm, setShowGenerateConfirm] = useState(false)

    // Sync-IP-Allowlist State
    const [allowlist, setAllowlist] = useState({ enabled: false, entries: [], your_ip: null })
    const [newCidr, setNewCidr] = useState('')
    const [newNote, setNewNote] = useState('')
    const [allowlistBusy, setAllowlistBusy] = useState(false)
    const [allowlistError, setAllowlistError] = useState('')

    // Schul-ID: die eigene Schule des Admins.
    // Superadmins ohne zugeordnete Schule sehen unten einen Hinweis.
    const schoolId = user?.school_id

    // API-Key-Verwaltung: mindestens school_admin (ein teacher darf den Sync-Key
    // NICHT ausstellen — er umgeht alle Session-Layer und gibt den ganzen Roster frei).
    // Das Backend erzwingt dasselbe Gate; das hier blendet die UI nur passend aus.
    const role = (user?.role || '').toLowerCase()
    const canManageApiKey = role === 'school_admin' || role === 'superadmin'

    useEffect(() => {
        if (schoolId) {
            fetchSettings()
            fetchApiKeyStatus()
            fetchClasses()
            fetchAllowlist()
        }
    }, [schoolId])

    // Klassen der Schule laden (für den Schulbescheinigungs-Klassenfilter)
    const fetchClasses = async () => {
        try {
            const res = await fetch(`${apiUrl}/admin/classes?school_id=${schoolId}`, {
                credentials: 'include'
            })
            if (res.ok) {
                setClasses(await res.json())
            }
        } catch {
            // Nicht kritisch — Klassenliste ist optional
        }
    }

    const fetchSettings = async () => {
        setLoading(true)
        setError('')
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/settings`, {
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Laden'))
            }

            const data = await res.json()
            setSettings({
                validity_strategy: data.validity_strategy || 'half_year',
                validity_custom_date: data.validity_custom_date || '',
                features_enabled: data.features_enabled || {},
                contact_email: data.contact_email || '',
                address: data.address || '',
                school_number: data.school_number || '',
                phone: data.phone || '',
                // Sicherstellen dass diese immer Strings sind (nicht null oder Objekte)
                logo_url: typeof data.logo_url === 'string' ? data.logo_url : '',
                primary_color: typeof data.primary_color === 'string' ? data.primary_color : '#1a73e8',
                cert_template_student: data.cert_template_student || '',
                cert_template_teacher: data.cert_template_teacher || ''
            })
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const handleSave = async (e) => {
        e.preventDefault()
        setSaving(true)
        setError('')
        setSuccess('')

        try {
            // Payload vorbereiten: Leere Strings in null konvertieren für date-Felder
            const payload = {
                ...settings,
                // Leere Strings werden von Pydantic nicht als gültige Dates akzeptiert
                validity_custom_date: settings.validity_custom_date || null
            }

            const res = await fetch(`${apiUrl}/schools/${schoolId}/settings`, {
                method: 'PUT',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })

            if (!res.ok) {
                const data = await res.json()
                // data.detail kann ein String oder ein Objekt sein (bei Pydantic Validation Errors)
                let errorMsg = 'Fehler beim Speichern'
                if (typeof data.detail === 'string') {
                    errorMsg = data.detail
                } else if (Array.isArray(data.detail)) {
                    // Pydantic Validation Errors sind Arrays
                    errorMsg = data.detail.map(e => e.msg || e.message || JSON.stringify(e)).join(', ')
                } else if (data.detail && typeof data.detail === 'object') {
                    errorMsg = JSON.stringify(data.detail)
                }
                throw new Error(errorMsg)
            }

            setSuccess('Einstellungen gespeichert!')
            setTimeout(() => setSuccess(''), 3000)
        } catch (err) {
            setError(err.message || String(err))
        } finally {
            setSaving(false)
        }
    }

    // Feature Toggle Handler
    const toggleFeature = (key) => {
        setSettings(prev => ({
            ...prev,
            features_enabled: {
                ...prev.features_enabled,
                [key]: !prev.features_enabled[key]
            }
        }))
    }

    // =========================================================================
    // SCHULBESCHEINIGUNG: Klassenfilter + Vorläufig-Modus
    // =========================================================================

    // Aktuell freigeschaltete Klassen-IDs (aus features_enabled)
    const enabledClassIds = settings.features_enabled.schulbescheinigung_class_ids || []

    const toggleCertClass = (classId) => {
        const current = settings.features_enabled.schulbescheinigung_class_ids || []
        const next = current.includes(classId)
            ? current.filter(id => id !== classId)
            : [...current, classId]
        setSettings(prev => ({
            ...prev,
            features_enabled: { ...prev.features_enabled, schulbescheinigung_class_ids: next }
        }))
    }

    const setAllCertClasses = (selectAll) => {
        setSettings(prev => ({
            ...prev,
            features_enabled: {
                ...prev.features_enabled,
                schulbescheinigung_class_ids: selectAll ? classes.map(c => c.id) : []
            }
        }))
    }

    const toggleCertProvisional = () => {
        setSettings(prev => ({
            ...prev,
            features_enabled: {
                ...prev.features_enabled,
                schulbescheinigung_provisional: !prev.features_enabled.schulbescheinigung_provisional
            }
        }))
    }

    // Logo aus der gespeicherten URL neu laden (lokaler Cache für PDF)
    const handleLogoRefresh = async () => {
        setLogoRefreshing(true)
        setError('')
        setSuccess('')
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/logo/refresh`, {
                method: 'POST',
                credentials: 'include'
            })
            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Aktualisieren'))
            }
            setSuccess('Logo aktualisiert!')
            setTimeout(() => setSuccess(''), 3000)
        } catch (err) {
            setError(err.message)
        } finally {
            setLogoRefreshing(false)
        }
    }

    // =========================================================================
    // API-KEY FUNKTIONEN
    // =========================================================================

    const fetchApiKeyStatus = async () => {
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/api-key`, {
                credentials: 'include'
            })
            if (res.ok) {
                const data = await res.json()
                setApiKey({
                    hasKey: data.has_key,
                    createdAt: data.created_at
                })
            }
        } catch {
            // Stille Fehlerbehandlung - API-Key Status ist nicht kritisch
        }
    }

    const handleGenerateApiKey = async () => {
        setApiKeyLoading(true)
        setNewApiKey(null)
        setApiKeyCopied(false)
        setShowGenerateConfirm(false)

        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/api-key`, {
                method: 'POST',
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Generieren'))
            }

            const data = await res.json()
            setNewApiKey(data.api_key)
            setApiKey({
                hasKey: true,
                createdAt: data.created_at
            })
        } catch (err) {
            setError(err.message)
        } finally {
            setApiKeyLoading(false)
        }
    }

    const handleRevokeApiKey = async () => {
        setApiKeyLoading(true)
        setShowRevokeConfirm(false)

        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/api-key`, {
                method: 'DELETE',
                credentials: 'include'
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(apiError(data, 'Fehler beim Widerrufen'))
            }

            setApiKey({ hasKey: false, createdAt: null })
            setNewApiKey(null)
            setSuccess('API-Key widerrufen')
            setTimeout(() => setSuccess(''), 3000)
        } catch (err) {
            setError(err.message)
        } finally {
            setApiKeyLoading(false)
        }
    }

    // ---- Sync-IP-Allowlist ----
    const fetchAllowlist = async () => {
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/sync-allowlist`, {
                credentials: 'include'
            })
            if (res.ok) setAllowlist(await res.json())
        } catch {
            // nicht kritisch
        }
    }

    const addAllowlistEntry = async (e) => {
        e.preventDefault()
        setAllowlistBusy(true)
        setAllowlistError('')
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/sync-allowlist`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cidr: newCidr.trim(), note: newNote.trim() || null })
            })
            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Eintrag konnte nicht hinzugefügt werden'))
            }
            setNewCidr('')
            setNewNote('')
            fetchAllowlist()
        } catch (err) {
            setAllowlistError(err.message)
        } finally {
            setAllowlistBusy(false)
        }
    }

    const deleteAllowlistEntry = async (id) => {
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/sync-allowlist/${id}`, {
                method: 'DELETE',
                credentials: 'include'
            })
            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Eintrag konnte nicht entfernt werden'))
            }
            fetchAllowlist()
        } catch (err) {
            setAllowlistError(err.message)
        }
    }

    const toggleAllowlist = async (enabled) => {
        setAllowlistBusy(true)
        setAllowlistError('')
        try {
            const res = await fetch(`${apiUrl}/schools/${schoolId}/sync-allowlist`, {
                method: 'PUT',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled })
            })
            if (!res.ok) {
                const data = await res.json().catch(() => ({}))
                throw new Error(apiError(data, 'Status konnte nicht geändert werden'))
            }
            setAllowlist(await res.json())
        } catch (err) {
            setAllowlistError(err.message)
        } finally {
            setAllowlistBusy(false)
        }
    }

    const copyApiKey = async () => {
        if (!newApiKey) return
        try {
            await navigator.clipboard.writeText(newApiKey)
            setApiKeyCopied(true)
            setTimeout(() => setApiKeyCopied(false), 3000)
        } catch {
            // Fallback: Select the text
            const el = document.querySelector('.api-key-value')
            if (el) {
                const range = document.createRange()
                range.selectNodeContents(el)
                window.getSelection().removeAllRanges()
                window.getSelection().addRange(range)
            }
        }
    }

    const formatDate = (isoString) => {
        if (!isoString) return '-'
        const d = new Date(isoString)
        return d.toLocaleDateString('de-DE', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit'
        })
    }

    // Keine Schul-ID = kein Zugriff
    if (!schoolId) {
        return (
            <Layout user={user} onLogout={onLogout}>
                <div className="page-header">
                    <h1>Einstellungen</h1>
                </div>
                <div className="card">
                    <div className="alert alert-warning">
                        Keine Schule zugeordnet. Bitte wenden Sie sich an einen Superadmin.
                    </div>
                </div>
            </Layout>
        )
    }

    return (
        <Layout user={user} onLogout={onLogout}>
            <div className="page-header">
                <h1>Einstellungen</h1>
                <p>Konfiguration für Ihre Schule</p>
            </div>

            {loading ? (
                <div className="card text-center">
                    <div className="spinner" style={{ margin: '2rem auto' }}></div>
                </div>
            ) : (
                <>
                <form onSubmit={handleSave}>
                    {/* Alerts */}
                    {error && <div className="alert alert-error mb-md">{error}</div>}
                    {success && <div className="alert alert-success mb-md">{success}</div>}

                    {/* Gültigkeit */}
                    <div className="card mb-md">
                        <h3 style={{ marginBottom: '1rem' }}>📅 Gültigkeit</h3>
                        <div className="form-group">
                            <label className="form-label">Berechnungs-Modus</label>

                            <div className="flex gap-md mb-sm">
                                <label className="flex items-center gap-sm cursor-pointer">
                                    <input
                                        type="radio"
                                        name="validity_strategy"
                                        value="half_year"
                                        checked={settings.validity_strategy === 'half_year'}
                                        onChange={(e) => setSettings({ ...settings, validity_strategy: e.target.value })}
                                    />
                                    <strong>Automatisch (Halbjahr/Schuljahr)</strong>
                                </label>

                                <label className="flex items-center gap-sm cursor-pointer">
                                    <input
                                        type="radio"
                                        name="validity_strategy"
                                        value="custom_date"
                                        checked={settings.validity_strategy === 'custom_date'}
                                        onChange={(e) => setSettings({ ...settings, validity_strategy: e.target.value })}
                                    />
                                    <strong>Festes Datum</strong>
                                </label>
                            </div>

                            <p className="text-sm opacity-70 mb-md">
                                {settings.validity_strategy === 'half_year'
                                    ? "Gültigkeit endet automatisch am nächsten Halbjahresende (31.07. oder 1. Freitag nach 30.01.)."
                                    : "Alle neuen Ausweise erhalten das fest eingestellte Enddatum."
                                }
                            </p>

                            {settings.validity_strategy === 'custom_date' && (
                                <div className="mt-sm fade-in">
                                    <label className="form-label text-sm">Festes Ablaufdatum</label>
                                    <input
                                        type="date"
                                        className="form-input"
                                        value={settings.validity_custom_date || ''}
                                        onChange={(e) => setSettings({ ...settings, validity_custom_date: e.target.value })}
                                        required={settings.validity_strategy === 'custom_date'}
                                    />
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Features */}
                    <div className="card mb-md">
                        <h3 style={{ marginBottom: '1rem' }}>⚡ Features</h3>
                        <div className="features-list">
                            {AVAILABLE_FEATURES.map(feature => (
                                <div key={feature.key} className="feature-item">
                                    <label className="feature-toggle">
                                        <input
                                            type="checkbox"
                                            checked={settings.features_enabled[feature.key] || false}
                                            onChange={() => toggleFeature(feature.key)}
                                        />
                                        <span className="toggle-slider"></span>
                                    </label>
                                    <div className="feature-info">
                                        <strong>{feature.label}</strong>
                                        <small>{feature.description}</small>
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Schulbescheinigung: Klassenfilter + Vorläufig-Modus */}
                        {settings.features_enabled.schulbescheinigung && (
                            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--color-border)' }}>
                                <div className="flex justify-between items-center" style={{ marginBottom: '0.5rem' }}>
                                    <strong style={{ fontSize: '0.9rem' }}>📄 Freigeschaltete Klassen</strong>
                                    <div className="flex gap-sm">
                                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAllCertClasses(true)}>Alle</button>
                                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAllCertClasses(false)}>Keine</button>
                                    </div>
                                </div>
                                <p className="text-sm opacity-70" style={{ marginBottom: '0.75rem' }}>
                                    Nur Schüler der gewählten Klassen sehen den Button „Schulbescheinigung" in der App.
                                </p>
                                {classes.length === 0 ? (
                                    <p className="text-sm opacity-60">Keine Klassen gefunden.</p>
                                ) : (
                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '0.4rem', maxHeight: '220px', overflowY: 'auto' }}>
                                        {classes.map(cls => (
                                            <label key={cls.id} className="flex items-center gap-sm cursor-pointer" style={{ fontSize: '0.875rem' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={enabledClassIds.includes(cls.id)}
                                                    onChange={() => toggleCertClass(cls.id)}
                                                />
                                                <span>{cls.name} <span style={{ opacity: 0.5 }}>({cls.student_count})</span></span>
                                            </label>
                                        ))}
                                    </div>
                                )}

                                {classes.length > 0 && enabledClassIds.length === 0 && (
                                    <div className="alert alert-warning" style={{ marginTop: '0.75rem', fontSize: '0.85rem' }}>
                                        ⚠️ Keine Klasse ausgewählt – aktuell sieht niemand die Schulbescheinigung.
                                    </div>
                                )}

                                {/* Vorläufig-Modus (Schuljahresbeginn) */}
                                <label className="flex items-center gap-sm cursor-pointer" style={{ marginTop: '1rem', fontSize: '0.875rem', alignItems: 'flex-start' }}>
                                    <input
                                        type="checkbox"
                                        checked={!!settings.features_enabled.schulbescheinigung_provisional}
                                        onChange={toggleCertProvisional}
                                        style={{ marginTop: '3px' }}
                                    />
                                    <span>
                                        <strong>Vorläufig-Modus (Schuljahresbeginn)</strong><br />
                                        <span className="opacity-70">Bescheinigungen, die zwischen dem 01.08. und 31.10. ausgestellt werden, erhalten den Hinweis „Vorläufige Schulbescheinigung – nur 3 Monate gültig".</span>
                                    </span>
                                </label>
                            </div>
                        )}
                    </div>

                    {/* Bescheinigungs-Texte (Template-Editor) */}
                    <div className="card mb-md">
                        <h3 style={{ marginBottom: '0.5rem' }}>📝 Bescheinigungs-Texte</h3>
                        <p className="text-sm opacity-70" style={{ marginBottom: '0.75rem' }}>
                            Eigener Text für den Hauptabsatz der PDF-Bescheinigungen. Leer lassen = Standardtext.
                            Verfügbare Variablen:{' '}
                            <code>{'{Vorname}'}</code> <code>{'{Name}'}</code> <code>{'{GebDatum}'}</code>{' '}
                            <code>{'{Klasse}'}</code> <code>{'{Bildungsgang}'}</code> <code>{'{Schule}'}</code>{' '}
                            <code>{'{Schuljahr}'}</code>
                            {' '}— <em>{'{Bildungsgang}'} ist bereits vorgesehen, bleibt aber leer, bis das Feld aus SchILD mitkommt.</em>
                        </p>

                        <div className="form-group">
                            <label className="form-label">Schulbescheinigung (Schüler)</label>
                            <textarea
                                className="form-input"
                                rows={4}
                                value={settings.cert_template_student}
                                onChange={(e) => setSettings(prev => ({ ...prev, cert_template_student: e.target.value }))}
                                placeholder={'Standard: Hiermit wird bescheinigt, dass\n\n{Vorname} {Name}, geboren am {GebDatum},\n\nim Schuljahr {Schuljahr} Schülerin bzw. Schüler der {Schule} in der Klasse {Klasse} ist.'}
                            />
                        </div>

                        <div className="form-group">
                            <label className="form-label">Dienstbescheinigung (Kollegium)</label>
                            <textarea
                                className="form-input"
                                rows={4}
                                value={settings.cert_template_teacher}
                                onChange={(e) => setSettings(prev => ({ ...prev, cert_template_teacher: e.target.value }))}
                                placeholder={'Standard: Hiermit wird bescheinigt, dass\n\n{Vorname} {Name}\n\nan der {Schule} als Lehrkraft tätig ist.'}
                            />
                            <small className="opacity-70">
                                Lehrkräfte (Klasse „Lehrerkollegium") erhalten automatisch den Titel „Dienstbescheinigung"
                                und „Funktion: Lehrkraft" statt der Klassen-Zeile. Damit das Kollegium den Button in der App
                                sieht, muss „Lehrerkollegium" oben bei den freigeschalteten Klassen angehakt sein.
                            </small>
                        </div>
                    </div>

                    {/* Branding */}
                    <div className="card mb-md">
                        <h3 style={{ marginBottom: '1rem' }}>🎨 Branding</h3>

                        <div className="form-group">
                            <label className="form-label">Logo URL</label>
                            <input
                                type="url"
                                className="form-input"
                                value={settings.logo_url}
                                onChange={(e) => setSettings({ ...settings, logo_url: e.target.value })}
                                placeholder="https://..."
                            />
                            <div className="flex gap-sm items-center" style={{ marginTop: '0.5rem' }}>
                                <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    onClick={handleLogoRefresh}
                                    disabled={logoRefreshing || !settings.logo_url}
                                >
                                    {logoRefreshing ? 'Lädt…' : '🔄 Logo neu laden'}
                                </button>
                                <small style={{ opacity: 0.6 }}>
                                    Lädt das Logo von der URL und speichert es lokal (für die PDF-Schulbescheinigung).
                                    Nach dem Ändern der URL bitte zuerst speichern.
                                </small>
                            </div>
                        </div>

                        <div className="form-group">
                            <label className="form-label">Primärfarbe</label>
                            <div className="flex gap-md items-center">
                                <input
                                    type="color"
                                    value={settings.primary_color}
                                    onChange={(e) => setSettings({ ...settings, primary_color: e.target.value })}
                                    style={{ width: '60px', height: '40px', padding: 0, border: 'none' }}
                                />
                                <input
                                    type="text"
                                    className="form-input"
                                    value={settings.primary_color}
                                    onChange={(e) => setSettings({ ...settings, primary_color: e.target.value })}
                                    style={{ maxWidth: '120px' }}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Kontakt & Briefkopf */}
                    <div className="card mb-md">
                        <h3 style={{ marginBottom: '0.5rem' }}>📧 Kontakt & Briefkopf</h3>
                        <p className="text-sm opacity-70" style={{ marginBottom: '0.75rem' }}>
                            Diese Angaben erscheinen im Briefkopf der PDF-Bescheinigungen
                            (Schul-/Dienstbescheinigung). Leere Felder werden weggelassen.
                        </p>
                        <div className="form-group">
                            <label className="form-label">E-Mail für Rückfragen</label>
                            <input
                                type="email"
                                className="form-input"
                                value={settings.contact_email}
                                onChange={(e) => setSettings({ ...settings, contact_email: e.target.value })}
                                placeholder="sekretariat@schule.de"
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Anschrift</label>
                            <textarea
                                className="form-input"
                                rows={2}
                                value={settings.address}
                                onChange={(e) => setSettings({ ...settings, address: e.target.value })}
                                placeholder={'Musterstraße 1\n58095 Hagen'}
                            />
                        </div>
                        <div className="flex gap-md">
                            <div className="form-group" style={{ flex: 1 }}>
                                <label className="form-label">Telefon</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={settings.phone}
                                    onChange={(e) => setSettings({ ...settings, phone: e.target.value })}
                                    placeholder="02331 12345-0"
                                />
                            </div>
                            <div className="form-group" style={{ flex: 1 }}>
                                <label className="form-label">Schulnummer</label>
                                <input
                                    type="text"
                                    className="form-input"
                                    value={settings.school_number}
                                    onChange={(e) => setSettings({ ...settings, school_number: e.target.value })}
                                    placeholder="123456"
                                />
                            </div>
                        </div>
                    </div>

                    {/* Save Button */}
                    <div className="flex justify-end">
                        <button type="submit" className="btn btn-primary" disabled={saving}>
                            {saving ? 'Speichern...' : '💾 Einstellungen speichern'}
                        </button>
                    </div>
                </form>

                {/* ================================================================
                    API-ZUGANG (außerhalb des Forms, eigene Aktionen)

                    SICHERHEIT: Nur school_admin/superadmin. Der API-Key umgeht ALLE
                    Session-Layer (portabel, session-los, kein Rate-Limit, unbegrenzt
                    gültig) und schaltet den kompletten Roster lesend UND schreibend
                    frei — das ist eine Stufe über "Sekretariat darf Schülerdaten pflegen".
                    Das Backend erzwingt dasselbe Gate (routes/schools.py).
                    ================================================================ */}
                {canManageApiKey && (
                <div className="card mb-md" style={{ marginTop: 'var(--space-xl)' }}>
                    <h3 style={{ marginBottom: '0.5rem' }}>🔑 API-Zugang</h3>
                    <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem', marginBottom: '1.25rem' }}>
                        API-Key für das externe Sync-Tool (z.B. SchILD-Connector).
                        Damit kann das Tool Schülerdaten und Fotos automatisch synchronisieren.
                    </p>

                    {/* Status-Anzeige */}
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 'var(--space-md)',
                        padding: 'var(--space-md)',
                        background: apiKey.hasKey ? 'rgba(52, 168, 83, 0.06)' : 'var(--color-bg)',
                        borderRadius: 'var(--radius-md)',
                        border: `1px solid ${apiKey.hasKey ? 'rgba(52, 168, 83, 0.2)' : 'var(--color-border)'}`,
                        marginBottom: 'var(--space-lg)'
                    }}>
                        <span style={{
                            width: '10px', height: '10px', borderRadius: '50%',
                            background: apiKey.hasKey ? 'var(--color-success)' : 'var(--color-text-light)',
                            flexShrink: 0
                        }} />
                        <div>
                            <strong style={{ fontSize: '0.875rem' }}>
                                {apiKey.hasKey ? 'API-Key aktiv' : 'Kein API-Key konfiguriert'}
                            </strong>
                            {apiKey.hasKey && apiKey.createdAt && (
                                <div style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                                    Erstellt am {formatDate(apiKey.createdAt)}
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Neu generierter Key (nur einmal sichtbar!) */}
                    {newApiKey && (
                        <div style={{
                            background: 'rgba(251, 188, 4, 0.08)',
                            border: '1px solid rgba(251, 188, 4, 0.3)',
                            borderRadius: 'var(--radius-md)',
                            padding: 'var(--space-md)',
                            marginBottom: 'var(--space-lg)'
                        }}>
                            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: 'var(--space-sm)', color: '#b45309' }}>
                                Neuer API-Key - jetzt sicher speichern!
                            </div>
                            <div style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-sm)' }}>
                                Dieser Key wird nur einmal angezeigt. Kopieren Sie ihn und tragen Sie ihn in Ihrem Sync-Tool ein.
                            </div>
                            <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 'var(--space-sm)',
                                background: 'white',
                                border: '1px solid var(--color-border)',
                                borderRadius: 'var(--radius-sm)',
                                padding: 'var(--space-sm) var(--space-md)',
                            }}>
                                <code className="api-key-value" style={{
                                    flex: 1,
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: '0.8rem',
                                    wordBreak: 'break-all',
                                    userSelect: 'all'
                                }}>
                                    {newApiKey}
                                </code>
                                <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    onClick={copyApiKey}
                                    style={{ flexShrink: 0 }}
                                >
                                    {apiKeyCopied ? 'Kopiert!' : 'Kopieren'}
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Aktionen */}
                    <div className="flex gap-md">
                        {/* Generieren-Button (mit Bestätigung wenn schon ein Key existiert) */}
                        {!showGenerateConfirm ? (
                            <button
                                type="button"
                                className="btn btn-primary"
                                disabled={apiKeyLoading}
                                onClick={() => {
                                    if (apiKey.hasKey) {
                                        setShowGenerateConfirm(true)
                                    } else {
                                        handleGenerateApiKey()
                                    }
                                }}
                            >
                                {apiKeyLoading ? 'Wird generiert...' : (apiKey.hasKey ? 'Neuen Key generieren' : 'API-Key generieren')}
                            </button>
                        ) : (
                            <div className="flex gap-sm items-center">
                                <span style={{ fontSize: '0.875rem', color: 'var(--color-error)' }}>
                                    Der alte Key wird sofort ungültig!
                                </span>
                                <button
                                    type="button"
                                    className="btn btn-danger btn-sm"
                                    onClick={handleGenerateApiKey}
                                >
                                    Ja, ersetzen
                                </button>
                                <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => setShowGenerateConfirm(false)}
                                >
                                    Abbrechen
                                </button>
                            </div>
                        )}

                        {/* Widerrufen-Button */}
                        {apiKey.hasKey && !showGenerateConfirm && (
                            <>
                                {!showRevokeConfirm ? (
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        disabled={apiKeyLoading}
                                        onClick={() => setShowRevokeConfirm(true)}
                                    >
                                        Key widerrufen
                                    </button>
                                ) : (
                                    <div className="flex gap-sm items-center">
                                        <span style={{ fontSize: '0.875rem', color: 'var(--color-error)' }}>
                                            Sync-Tool verliert Zugriff!
                                        </span>
                                        <button
                                            type="button"
                                            className="btn btn-danger btn-sm"
                                            onClick={handleRevokeApiKey}
                                        >
                                            Ja, widerrufen
                                        </button>
                                        <button
                                            type="button"
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => setShowRevokeConfirm(false)}
                                        >
                                            Abbrechen
                                        </button>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
                )}

                {/* ================================================================
                    SYNC-IP-ALLOWLIST
                    Optionale IP-Grenze für den Sync-Kanal. Aus (Default) = Fallback
                    für Tests / dynamische IPs. An = nur noch erlaubte IPs, der Key
                    allein reicht dann nicht mehr. Gleiches Gate wie der API-Key.
                    ================================================================ */}
                {canManageApiKey && (
                <div className="card mb-md" style={{ marginTop: 'var(--space-xl)' }}>
                    <h3 style={{ marginBottom: '0.5rem' }}>🌐 Sync-IP-Allowlist</h3>
                    <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem', marginBottom: '1rem' }}>
                        Optional: Begrenzt den Sync-Zugriff auf bekannte IP-Adressen (z.B. die
                        feste IP des Schul-PCs). Ist die Liste aktiv, wird der API-Key zum zweiten
                        Faktor — ein geleakter Key allein nützt dann nichts mehr. Standard: <strong>aus</strong>.
                    </p>

                    {/* Status + Toggle */}
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 'var(--space-md)',
                        padding: 'var(--space-md)', borderRadius: 'var(--radius-md)',
                        background: allowlist.enabled ? 'rgba(245, 158, 11, 0.08)' : 'var(--color-bg)',
                        border: `1px solid ${allowlist.enabled ? 'rgba(245, 158, 11, 0.3)' : 'var(--color-border)'}`,
                        marginBottom: 'var(--space-lg)'
                    }}>
                        <span style={{
                            width: '10px', height: '10px', borderRadius: '50%',
                            background: allowlist.enabled ? '#f59e0b' : 'var(--color-text-light)', flexShrink: 0
                        }} />
                        <div style={{ flex: 1 }}>
                            <strong style={{ fontSize: '0.875rem' }}>
                                {allowlist.enabled ? 'Allowlist aktiv — Sync nur von erlaubten IPs' : 'Allowlist aus — Sync von überall (nur Key nötig)'}
                            </strong>
                            {allowlist.your_ip && (
                                <div style={{ fontSize: '0.8rem', color: 'var(--color-text-light)', marginTop: '0.25rem' }}>
                                    Deine aktuelle IP: <code>{allowlist.your_ip}</code>
                                    {' '}
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-sm"
                                        style={{ marginLeft: '0.5rem' }}
                                        disabled={allowlistBusy}
                                        onClick={() => { setNewCidr(allowlist.your_ip); setNewNote('Mein Zugang') }}
                                    >
                                        übernehmen
                                    </button>
                                </div>
                            )}
                        </div>
                        <button
                            type="button"
                            className={allowlist.enabled ? 'btn btn-secondary' : 'btn btn-primary'}
                            disabled={allowlistBusy || (!allowlist.enabled && allowlist.entries.length === 0)}
                            title={!allowlist.enabled && allowlist.entries.length === 0 ? 'Zuerst mindestens eine IP hinzufügen' : ''}
                            onClick={() => toggleAllowlist(!allowlist.enabled)}
                        >
                            {allowlist.enabled ? 'Deaktivieren' : 'Aktivieren'}
                        </button>
                    </div>

                    {/* Eintrag hinzufügen */}
                    <form onSubmit={addAllowlistEntry} className="flex gap-sm" style={{ marginBottom: 'var(--space-md)', flexWrap: 'wrap' }}>
                        <input
                            type="text"
                            className="form-input"
                            style={{ flex: '1 1 180px' }}
                            placeholder="IP oder CIDR, z.B. 203.0.113.7 oder 203.0.113.0/24"
                            value={newCidr}
                            onChange={(e) => setNewCidr(e.target.value)}
                            required
                        />
                        <input
                            type="text"
                            className="form-input"
                            style={{ flex: '1 1 120px' }}
                            placeholder="Notiz (optional)"
                            value={newNote}
                            onChange={(e) => setNewNote(e.target.value)}
                        />
                        <button type="submit" className="btn btn-primary" disabled={allowlistBusy || !newCidr.trim()}>
                            Hinzufügen
                        </button>
                    </form>

                    {allowlistError && <div className="alert alert-error mb-md">{allowlistError}</div>}

                    {/* Liste */}
                    {allowlist.entries.length === 0 ? (
                        <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>Noch keine Einträge.</p>
                    ) : (
                        <div className="table-container">
                            <table className="table">
                                <thead>
                                    <tr><th>IP / Bereich</th><th>Notiz</th><th></th></tr>
                                </thead>
                                <tbody>
                                    {allowlist.entries.map((e) => (
                                        <tr key={e.id}>
                                            <td style={{ fontFamily: 'monospace' }}>{e.cidr}</td>
                                            <td style={{ fontSize: '0.85rem' }}>{e.note || '—'}</td>
                                            <td>
                                                <button className="btn btn-danger btn-sm" onClick={() => deleteAllowlistEntry(e.id)}>
                                                    Entfernen
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
                )}
                </>
            )}
        </Layout>
    )
}
