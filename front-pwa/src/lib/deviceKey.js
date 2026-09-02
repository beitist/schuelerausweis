/**
 * Geräte-Signierschlüssel (On-Device-Keygen)
 * =============================================================================
 * SICHERHEIT — die Vertrauensrichtung:
 * Würde der SERVER das Keypair erzeugen und den PRIVATEN Schlüssel im
 * Login-Response ausliefern, kennten ihn Server, Proxy und Logs — und
 * localStorage-XSS könnte ihn stehlen. Die PWA erzeugt das Keypair daher selbst —
 * als ECDSA-P-256-CryptoKey mit `extractable: false`. Der private Schlüssel ist damit
 * PER KONSTRUKTION nicht auslesbar (auch nicht durch XSS) und liegt als CryptoKey
 * in IndexedDB. Nur der PUBLIC KEY wird beim Server registriert.
 *
 * Warum P-256 und nicht Ed25519: `extractable:false` gibt es nur für nativ
 * unterstützte Kurven. Ein Ed25519-Polyfill würde den Schlüssel wieder in
 * JS-lesbaren Speicher legen. P-256 kann jede Engine nativ.
 *
 * Draht-Format (muss exakt zum Backend services/crypto.py passen):
 *   Public Key:  exportKey('raw', pub) → 65 B (0x04||X||Y) → Base64
 *   Signatur:    sign({name:'ECDSA', hash:'SHA-256'}) → 64 B raw r||s → Base64
 */

const DB_NAME = 'hagenid-keys'
const STORE = 'keys'
const KEY_ID = 'device-signing-key'
const TOTP_KEY_ID = 'totp-hmac-key'   // TOTP-Seed als non-extractable HMAC-Key

// ---- minimaler IndexedDB-Wrapper -------------------------------------------

function openDb() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, 1)
        req.onupgradeneeded = () => req.result.createObjectStore(STORE)
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
    })
}

async function idbGet(key) {
    const db = await openDb()
    try {
        return await new Promise((resolve, reject) => {
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(key)
            req.onsuccess = () => resolve(req.result)
            req.onerror = () => reject(req.error)
        })
    } finally {
        db.close()
    }
}

async function idbPut(key, value) {
    const db = await openDb()
    try {
        await new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, 'readwrite')
            tx.objectStore(STORE).put(value, key)
            tx.oncomplete = () => resolve()
            tx.onerror = () => reject(tx.error)
        })
    } finally {
        db.close()
    }
}

async function idbDelete(key) {
    const db = await openDb()
    try {
        await new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, 'readwrite')
            tx.objectStore(STORE).delete(key)
            tx.oncomplete = () => resolve()
            tx.onerror = () => reject(tx.error)
        })
    } finally {
        db.close()
    }
}

function bufToB64(buf) {
    return btoa(String.fromCharCode(...new Uint8Array(buf)))
}

// ---- öffentliche API --------------------------------------------------------

/**
 * Stellt das non-extractable Geräte-Keypair sicher und gibt den Public Key
 * (Base64, raw) zurück. Mit regenerate=true wird IMMER ein frisches Keypair
 * erzeugt (neuer Login = neues Gerät = neuer Schlüssel).
 */
export async function ensureDeviceKey({ regenerate = false } = {}) {
    let record = regenerate ? null : await idbGet(KEY_ID)

    if (!record?.privateKey) {
        const kp = await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            false,                       // <-- privater Schlüssel NICHT exportierbar
            ['sign', 'verify']
        )
        const rawPub = await crypto.subtle.exportKey('raw', kp.publicKey)  // 65 B
        record = { privateKey: kp.privateKey, publicKeyB64: bufToB64(rawPub) }
        await idbPut(KEY_ID, record)   // CryptoKey ist structured-cloneable, auch non-extractable
    }
    return record.publicKeyB64
}

/** True, wenn auf diesem Gerät ein Signier-Schlüssel vorhanden ist. */
export async function hasDeviceKey() {
    try {
        const record = await idbGet(KEY_ID)
        return !!record?.privateKey
    } catch {
        return false
    }
}

/**
 * Signiert den Payload mit dem Geräteschlüssel → Base64(raw r||s), oder null,
 * wenn (noch) kein Schlüssel vorhanden ist.
 */
export async function signPayload(payload) {
    try {
        const record = await idbGet(KEY_ID)
        if (!record?.privateKey) return null
        const sig = await crypto.subtle.sign(
            { name: 'ECDSA', hash: 'SHA-256' },
            record.privateKey,
            new TextEncoder().encode(payload)
        )
        return bufToB64(sig)
    } catch (err) {
        console.error('[deviceKey] Signatur-Fehler:', err)
        return null
    }
}

/** Löscht den Geräteschlüssel (beim Logout, Hygiene auf geteilten Geräten). */
export async function clearDeviceKey() {
    try { await idbDelete(KEY_ID) } catch { /* egal */ }
}

// ---- TOTP-Seed als non-extractable HMAC-Key ---------------------------------
// Läge der Base32-Seed im Klartext in localStorage, könnte XSS ihn heben und
// den TOTP-Generator dauerhaft klonen. Der Seed wird daher EINMAL als
// non-extractable HMAC-SHA1-CryptoKey importiert und in IndexedDB abgelegt.
// XSS kann danach höchstens einen aktuellen Code erzeugen (offene Seite), aber
// den Seed nicht auslesen. (Der QR ist ohnehin erst mit der ECDSA-Signatur
// gültig — das hier ist Defense-in-Depth.)

function base32ToBytes(secret) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
    let bits = ''
    for (const ch of (secret || '').toUpperCase()) {
        const val = chars.indexOf(ch)
        if (val === -1) continue
        bits += val.toString(2).padStart(5, '0')
    }
    const bytes = new Uint8Array(Math.floor(bits.length / 8))
    for (let i = 0; i < bytes.length; i++) {
        bytes[i] = parseInt(bits.substr(i * 8, 8), 2)
    }
    return bytes
}

/**
 * Importiert den Base32-TOTP-Seed als non-extractable HMAC-Key und legt ihn in
 * IndexedDB ab. Danach wird der Klartext-Seed nicht mehr gebraucht/gespeichert.
 * Best-effort: gibt true/false zurück, wirft nicht.
 */
export async function importTotpSecret(base32) {
    try {
        const keyBytes = base32ToBytes(base32)
        if (!keyBytes.length) return false
        const key = await crypto.subtle.importKey(
            'raw', keyBytes, { name: 'HMAC', hash: 'SHA-1' },
            false,          // <-- non-extractable: Seed nicht mehr auslesbar
            ['sign']
        )
        await idbPut(TOTP_KEY_ID, { key })
        return true
    } catch (err) {
        console.error('[totpKey] Import fehlgeschlagen:', err)
        return false
    }
}

/** True, wenn ein TOTP-Key auf diesem Gerät liegt (offline-fähig). */
export async function hasTotpKey() {
    try {
        const r = await idbGet(TOTP_KEY_ID)
        return !!r?.key
    } catch {
        return false
    }
}

/**
 * Erzeugt den aktuellen 6-stelligen TOTP-Code aus dem gespeicherten Key
 * (RFC 6238, HMAC-SHA1, 30s-Fenster). null, wenn kein Key vorhanden ist.
 */
export async function generateTotpCode() {
    const r = await idbGet(TOTP_KEY_ID)
    if (!r?.key) return null

    // Counter (Zeit / 30s) als 8-Byte Big-Endian
    const counterBytes = new Uint8Array(8)
    let c = Math.floor(Date.now() / 1000 / 30)
    for (let i = 7; i >= 0; i--) {
        counterBytes[i] = c & 0xff
        c = Math.floor(c / 256)
    }

    const sigBuf = await crypto.subtle.sign('HMAC', r.key, counterBytes)
    const hash = new Uint8Array(sigBuf)

    // Dynamic Truncation (RFC 4226)
    const offset = hash[hash.length - 1] & 0x0f
    const binary =
        ((hash[offset] & 0x7f) << 24) |
        ((hash[offset + 1] & 0xff) << 16) |
        ((hash[offset + 2] & 0xff) << 8) |
        (hash[offset + 3] & 0xff)
    return (binary % 1000000).toString().padStart(6, '0')
}

/** Löscht den TOTP-Key (beim Logout). */
export async function clearTotpKey() {
    try { await idbDelete(TOTP_KEY_ID) } catch { /* egal */ }
}

/**
 * Registriert den (ggf. frisch erzeugten) Public Key beim Server.
 * Best-effort: gibt true/false zurück, wirft nicht.
 *
 * Auth läuft über das HttpOnly-Session-Cookie (`credentials: 'include'`) —
 * kein Bearer-/Session-Header. So kann die Card den Schlüssel bei gültiger
 * Session auch STILL neu registrieren, falls iOS die IndexedDB geräumt hat.
 *
 * @param regenerate true beim Login (frischer Key), false zum Selbstheilen beim Card-Mount.
 */
export async function registerDeviceKey(apiUrl, { regenerate = false } = {}) {
    try {
        const publicKey = await ensureDeviceKey({ regenerate })
        const res = await fetch(`${apiUrl}/pwa/register-key`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ public_key: publicKey }),
        })
        return res.ok
    } catch (err) {
        console.error('[deviceKey] Registrierung fehlgeschlagen:', err)
        return false
    }
}
