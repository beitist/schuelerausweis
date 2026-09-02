/**
 * ID Card Component
 * 
 * Der digitale Schülerausweis mit dynamischem QR-Code.
 */

import { useState, useEffect, useRef } from 'react'
import QRCode from 'qrcode'
// On-Device-Signierschlüssel (ECDSA P-256, non-extractable, in IndexedDB).
// Der private Schlüssel entsteht auf dem Gerät und verlässt es nie — siehe lib/deviceKey.js.
import { signPayload, hasDeviceKey, registerDeviceKey, generateTotpCode } from '../lib/deviceKey'

// Die TOTP-Erzeugung liegt in lib/deviceKey.js (generateTotpCode): der Seed ist
// ein non-extractable HMAC-Key in IndexedDB, kein Base32-String im Speicher.
// Der Session-Hash-Prefix kommt vom Server via /pwa/me
// (userData.session_hash_prefix); der Session-Token selbst ist HttpOnly.

/**
 * ECDSA-P-256-Signatur für den QR-Code erstellen.
 * Signiert: student_id:totp:session_hash:timestamp — mit dem non-extractable
 * Geräteschlüssel aus IndexedDB (lib/deviceKey.js). Der private Schlüssel
 * verlässt das Gerät nie; hier wird nur signiert.
 */
async function signQRCode(studentId, totp, sessionHash, timestamp) {
    const payload = `${studentId}:${totp}:${sessionHash}:${timestamp}`
    const signature = await signPayload(payload)
    // null → kein Schlüssel auf dem Gerät registriert; Backend lehnt das als
    // "nicht verifizierbar" ab (fail-closed).
    return signature || 'NO_KEY'
}

export default function Card({ userData, totpReady, onLogout, onRefresh, offline, apiUrl }) {
    const [qrDataUrl, setQrDataUrl] = useState('')
    const [totpCode, setTotpCode] = useState('')
    const [secondsRemaining, setSecondsRemaining] = useState(30)
    const canvasRef = useRef()
    const photoInputRef = useRef()
    const [uploadingPhoto, setUploadingPhoto] = useState(false)
    const [downloadingCert, setDownloadingCert] = useState(false)
    // Fehlt der Signier-Schlüssel (z.B. alte Session), ist der QR nicht verifizierbar
    const [signable, setSignable] = useState(true)

    // Bild client-seitig komprimieren (max 800px, JPEG 95%)
    const compressImage = (file) => {
        return new Promise((resolve, reject) => {
            const img = new Image()
            const url = URL.createObjectURL(file)
            img.onload = () => {
                URL.revokeObjectURL(url)
                const MAX = 800
                let { width, height } = img
                if (Math.max(width, height) > MAX) {
                    if (width > height) {
                        height = Math.round(height * MAX / width)
                        width = MAX
                    } else {
                        width = Math.round(width * MAX / height)
                        height = MAX
                    }
                }
                const canvas = document.createElement('canvas')
                canvas.width = width
                canvas.height = height
                const ctx = canvas.getContext('2d')
                ctx.drawImage(img, 0, 0, width, height)
                canvas.toBlob(
                    (blob) => blob ? resolve(blob) : reject(new Error('Komprimierung fehlgeschlagen')),
                    'image/jpeg',
                    0.95
                )
            }
            img.onerror = () => {
                URL.revokeObjectURL(url)
                reject(new Error('Bild konnte nicht geladen werden'))
            }
            img.src = url
        })
    }

    const handlePhotoUpload = async (e) => {
        const file = e.target.files?.[0]
        if (!file) return

        setUploadingPhoto(true)
        try {
            // Client-seitig komprimieren bevor Upload
            const compressed = await compressImage(file)

            const formData = new FormData()
            formData.append('file', compressed, 'photo.jpg')

            const res = await fetch(`${apiUrl}/pwa/photo`, {
                method: 'POST',
                credentials: 'include',   // Auth via HttpOnly-Cookie
                body: formData
            })

            if (!res.ok) {
                const data = await res.json()
                throw new Error(data.detail || 'Upload fehlgeschlagen')
            }

            // User-Daten neu laden (enthält jetzt photo_b64)
            if (onRefresh) onRefresh()
        } catch (err) {
            alert('Fehler: ' + err.message)
        } finally {
            setUploadingPhoto(false)
            if (photoInputRef.current) photoInputRef.current.value = ''
        }
    }

    // Schulbescheinigung als PDF laden und öffnen/herunterladen
    const handleDownloadCert = async () => {
        setDownloadingCert(true)
        try {
            const res = await fetch(`${apiUrl}/pwa/schulbescheinigung.pdf`, {
                credentials: 'include'   // Auth via HttpOnly-Cookie
            })

            if (!res.ok) {
                let msg = 'Schulbescheinigung konnte nicht erstellt werden'
                try {
                    const data = await res.json()
                    msg = data.detail || msg
                } catch {
                    // Keine JSON-Antwort — Standardmeldung behalten
                }
                throw new Error(msg)
            }

            // PDF als Blob laden und über einen Download-Link öffnen
            const blob = await res.blob()
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = userData?.is_teacher ? 'dienstbescheinigung.pdf' : 'schulbescheinigung.pdf'
            a.target = '_blank'
            document.body.appendChild(a)
            a.click()
            a.remove()
            setTimeout(() => URL.revokeObjectURL(url), 60000)
        } catch (err) {
            alert('Fehler: ' + err.message)
        } finally {
            setDownloadingCert(false)
        }
    }

    // Selbstheilung: Fehlt der Geräteschlüssel — weil die Registrierung beim
    // Login scheiterte ODER iOS die IndexedDB geräumt hat — holen wir ihn online nach.
    // Die Session steckt im HttpOnly-Cookie, also ist KEIN Re-Login nötig: ein frischer
    // Key wird still registriert (ensureDeviceKey erzeugt einen, falls keiner da ist).
    useEffect(() => {
        if (offline) return
        registerDeviceKey(apiUrl, { regenerate: false })
            .then((ok) => { if (ok) setSignable(true) })
    }, [apiUrl, offline])

    // QR-Code generieren und aktualisieren
    useEffect(() => {
        if (!totpReady || !userData) return

        const updateQR = async () => {
            // TOTP-Code aus dem gespeicherten non-extractable HMAC-Key
            const code = await generateTotpCode()
            if (!code) return  // kein Key auf dem Gerät → QR kann nicht gebaut werden
            setTotpCode(code)

            // Session-Hash-Prefix (für Highlander-Validierung beim Scannen): kommt
            // vom Server via /pwa/me (der Session-Token selbst ist HttpOnly und für
            // JS unlesbar). Fehlt er (alter Cache) → QR wird als "nicht verifizierbar" abgelehnt.
            const sessionHashPrefix = userData.session_hash_prefix || ''

            // QR-Payload erstellen
            // Format: student_id:totp_code:session_hash_prefix:timestamp:signature
            const timestamp = Math.floor(Date.now() / 1000 / 30)

            // ECDSA-P-256-Signatur mit dem Geräteschlüssel (offline-fähig!)
            // Ohne registrierten Schlüssel scannt der QR als "nicht verifizierbar" → Hinweis
            setSignable(await hasDeviceKey())
            const signature = await signQRCode(
                userData.id,
                code,
                sessionHashPrefix,
                timestamp
            )

            const payload = `${userData.id}:${code}:${sessionHashPrefix}:${timestamp}:${signature}`

            // URL für den QR-Code erstellen
            // Beim Scannen öffnet sich die Verifizierungsseite automatisch im Browser
            const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'
            const verifyUrl = `${apiBaseUrl}/check?p=${encodeURIComponent(payload)}`

            // QR-Code als Data-URL generieren
            try {
                const url = await QRCode.toDataURL(verifyUrl, {
                    width: 180,
                    margin: 2,
                    color: {
                        dark: '#1e293b',
                        light: '#ffffff'
                    }
                })
                setQrDataUrl(url)
            } catch (err) {
                console.error('QR-Code Fehler:', err)
            }
        }

        updateQR()

        // Timer für verbleibende Sekunden
        const interval = setInterval(() => {
            const now = Math.floor(Date.now() / 1000)
            const remaining = 30 - (now % 30)
            setSecondsRemaining(remaining)

            // Bei neuem Intervall: QR aktualisieren
            if (remaining === 30) {
                updateQR()
            }
        }, 1000)

        return () => clearInterval(interval)
    }, [totpReady, userData])

    if (!userData) {
        return (
            <div className="loading-screen">
                <div className="spinner"></div>
                <p>Lade Ausweis...</p>
            </div>
        )
    }

    const isTeacher = userData.is_teacher

    return (
        <div className="card-page">
            <div
                className={`id-card ${isTeacher ? 'teacher' : ''}`}
                style={{
                    '--primary-color': userData.school_color || '#1a73e8',
                    borderTop: `6px solid ${userData.school_color || '#1a73e8'}`
                }}
            >
                {/* Header mit Schulfarbe als Hintergrund */}
                <div className="card-header" style={{
                    backgroundColor: userData.school_color || '#1a73e8',
                    color: 'white'
                }}>
                    <div className="flex items-center" style={{ width: '100%', gap: '12px' }}>
                        {/* 1. Schul-Logo (Links) */}
                        <div style={{ flexShrink: 0 }}>
                            {userData.school_logo_url ? (
                                <img
                                    src={userData.school_logo_url}
                                    alt="Schullogo"
                                    style={{ height: '50px', width: 'auto', maxWidth: '70px', objectFit: 'contain' }}
                                    onError={(e) => {
                                        e.target.style.display = 'none'
                                    }}
                                />
                            ) : (
                                <div style={{
                                    width: '50px',
                                    height: '50px',
                                    borderRadius: '8px',
                                    backgroundColor: 'rgba(255,255,255,0.2)', // Transparenter auf farbigem Hintergrund
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    color: 'white',
                                    fontWeight: 'bold',
                                    fontSize: '18px',
                                    border: '1px solid rgba(255,255,255,0.3)'
                                }}>
                                    {(userData.school_name || 'S').charAt(0)}
                                </div>
                            )}
                        </div>

                        {/* 2. Text (Mitte, füllt Rest aus) */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="school-name" style={{
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis'
                            }}>
                                {userData.school_name || 'Schule'}
                            </div>
                            <div className="card-type">
                                {isTeacher ? 'Dienstausweis' : 'Schülerausweis'}
                            </div>
                        </div>

                        {/* 3. Stadtwappen (Rechts, fixiert) */}
                        <img
                            src="/Stadtwappen-Hagen.png"
                            alt=""
                            style={{
                                height: '45px',
                                width: 'auto',
                                opacity: 0.9,
                                flexShrink: 0
                            }}
                        />
                    </div>
                    {offline && <span className="offline-badge">📴 Offline</span>}
                </div>

                {/* Content */}
                <div className="card-content">
                    <div className="card-top">
                        {/* Foto mit Hologramm */}
                        <div className="photo-container" style={{ position: 'relative' }}>
                            {/* Foto kommt als Base64 Data-URI direkt aus /pwa/me */}
                            {userData.photo_b64 ? (
                                <img src={userData.photo_b64} alt="Foto" />
                            ) : userData.has_photo ? (
                                <div className="photo-placeholder">⏳</div>
                            ) : (
                                <div className="photo-placeholder">👤</div>
                            )}
                            {/* Glitter / Hologram Overlay */}
                            <div className="hologram-overlay"></div>
                            {/* Random Sparkles */}
                            <div className="sparkle" style={{ top: '10%', left: '20%', animationDelay: '0s' }}></div>
                            <div className="sparkle" style={{ top: '80%', left: '70%', animationDelay: '1s' }}></div>
                            <div className="sparkle" style={{ top: '40%', left: '80%', animationDelay: '2s' }}></div>

                            {/* Kamera-Button für Lehrer */}
                            {isTeacher && (
                                <>
                                    <input
                                        ref={photoInputRef}
                                        type="file"
                                        accept="image/*"
                                        capture="user"
                                        onChange={handlePhotoUpload}
                                        style={{ display: 'none' }}
                                    />
                                    <button
                                        onClick={() => photoInputRef.current?.click()}
                                        disabled={uploadingPhoto || offline}
                                        style={{
                                            position: 'absolute',
                                            bottom: '4px',
                                            right: '4px',
                                            width: '32px',
                                            height: '32px',
                                            borderRadius: '50%',
                                            border: 'none',
                                            background: 'rgba(0,0,0,0.6)',
                                            color: 'white',
                                            fontSize: '16px',
                                            cursor: 'pointer',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                            zIndex: 10
                                        }}
                                        title="Foto ändern"
                                    >
                                        {uploadingPhoto ? '...' : '📷'}
                                    </button>
                                </>
                            )}
                        </div>

                        {/* Info */}
                        <div className="student-info">
                            <div className="student-name">
                                {userData.first_name}<br />{userData.last_name}
                            </div>
                            <div className="info-row">
                                <span className="info-label">Klasse</span>
                                <span className="info-value">{userData.class_name || '-'}</span>
                            </div>
                            <div className="info-row">
                                <span className="info-label">Geb.</span>
                                <span className="info-value">{userData.dob}</span>
                            </div>
                        </div>
                    </div>

                    {/* QR Code */}
                    <div className="qr-section">
                        <div className="qr-container">
                            {qrDataUrl && (
                                <>
                                    <img src={qrDataUrl} alt="QR-Code" className="qr-code" />
                                    <div className="hologram-overlay"></div>
                                </>
                            )}
                        </div>
                        {!signable && (
                            <div style={{
                                marginTop: '8px',
                                fontSize: '0.72rem',
                                lineHeight: 1.3,
                                color: '#b45309',
                                background: 'rgba(251, 188, 4, 0.12)',
                                border: '1px solid rgba(251, 188, 4, 0.35)',
                                borderRadius: '8px',
                                padding: '6px 8px',
                                textAlign: 'center'
                            }}>
                                ⚠️ Ausweis nicht signiert. Bitte einmal <strong>neu einloggen</strong>,
                                damit der QR-Code prüfbar ist.
                            </div>
                        )}
                    </div>
                </div>

                {/* Gültigkeit */}
                <div className="validity" style={{
                    backgroundColor: userData.school_color || '#1a73e8',
                    color: 'white'
                }}>
                    Gültig bis: {userData.valid_until || 'Unbegrenzt'}
                </div>

                {/* Schulbescheinigung (nur wenn Klasse freigeschaltet) */}
                {userData.schulbescheinigung_enabled && (
                    <div style={{ padding: '0.75rem 1rem 0' }}>
                        <button
                            onClick={handleDownloadCert}
                            disabled={downloadingCert || offline}
                            style={{
                                width: '100%',
                                padding: '0.6rem',
                                borderRadius: '8px',
                                border: `1px solid ${userData.school_color || '#1a73e8'}`,
                                background: userData.school_color || '#1a73e8',
                                color: 'white',
                                fontWeight: 600,
                                fontSize: '0.9rem',
                                cursor: (downloadingCert || offline) ? 'not-allowed' : 'pointer',
                                opacity: (downloadingCert || offline) ? 0.55 : 1
                            }}
                            title={offline ? 'Nur online verfügbar' : (userData.is_teacher ? 'Dienstbescheinigung als PDF' : 'Schulbescheinigung als PDF')}
                        >
                            {downloadingCert
                                ? 'Wird erstellt…'
                                : (userData.is_teacher
                                    ? (offline ? '📄 Dienstbescheinigung (online)' : '📄 Dienstbescheinigung')
                                    : (offline ? '📄 Schulbescheinigung (online)' : '📄 Schulbescheinigung'))}
                        </button>
                    </div>
                )}

                {/* Footer */}
                <div className="card-footer">
                    <button
                        className="btn-logout"
                        onClick={onLogout}
                        style={{ borderColor: userData.school_color || '#1a73e8', color: userData.school_color || '#1a73e8' }}
                    >
                        Abmelden
                    </button>
                </div>
            </div>
        </div>
    )
}
